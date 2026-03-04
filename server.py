"""FastAPI web server exposing the pipeline as an HTTP API."""

from __future__ import annotations

import os
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from pipeline import run_pipeline
from tools.state import read_state

app = FastAPI(title="gh-issue-to-pr", version="0.1.0")

# ---------------------------------------------------------------------------
# In-memory job registry
# ---------------------------------------------------------------------------

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class IssueRequest(BaseModel):
    issue_url: str
    repo_url: str
    local_path: Optional[str] = None
    guidelines: Optional[str] = None  # inline string; written to tempfile internally
    budget: float = Field(default=2.0, gt=0)


class AcceptedResponse(BaseModel):
    issue_url: str
    status_url: str


class StatusResponse(BaseModel):
    status: str          # "queued" | "running" | "completed" | "failed"
    issue_url: str
    run_dir: Optional[str] = None
    outcome: Optional[str] = None
    error: Optional[str] = None
    state: Optional[dict] = None  # full STATE.json content when available


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# class IssueRequest(BaseModel):
#     issue_url: str
#     repo_url: str
#     local_path: Optional[str] = None
#     guidelines: Optional[str] = None  # inline string; written to tempfile internally
#     budget: float = Field(default=2.0, gt=0)

@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return """
    <html>
        <form id="issue-form">
            <input type="url" name="issue_url" placeholder="Issue URL" required>
            <input type="url" name="repo_url" placeholder="Repo URL" required>
            <button type="submit">Submit</button>
        </form>
    </html>
    <script>
    const form = document.getElementById('issue-form');
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const formData = new FormData(form);
        const data = {
            issue_url: formData.get('issue_url'),
            repo_url: formData.get('repo_url'),
        };
        await fetch('/issue', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data),
        });
    </script>
    """

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


@app.post("/issue", status_code=202, response_model=AcceptedResponse)
def submit_issue(req: IssueRequest) -> AcceptedResponse:
    """
    Accept a pipeline job and start it in the background.

    Returns HTTP 202 immediately with a status polling URL.
    Returns HTTP 422 for invalid input (handled by FastAPI/Pydantic).
    """
    issue_url = req.issue_url
    status_url = f"/status?issue={issue_url}"

    with _jobs_lock:
        existing = _jobs.get(issue_url)
        if existing and existing["status"] in ("queued", "running"):
            # Already in progress — return 202 pointing to the same status URL
            return AcceptedResponse(issue_url=issue_url, status_url=status_url)
        _jobs[issue_url] = {
            "status": "queued",
            "run_dir": None,
            "outcome": None,
            "error": None,
        }

    thread = threading.Thread(
        target=_run_pipeline_job,
        args=(req,),
        daemon=True,
    )
    thread.start()

    return AcceptedResponse(issue_url=issue_url, status_url=status_url)


@app.get("/status", response_model=StatusResponse)
def get_status(issue_url: str) -> StatusResponse:
    """
    Return the current status of a pipeline job.

    Poll this endpoint after POST /issue returns 202.
    Returns the full STATE.json when a run directory is available.
    """
    with _jobs_lock:
        job = _jobs.get(issue_url)

    if job is None:
        raise HTTPException(status_code=404, detail="No job found for this issue URL")

    state_data: Optional[dict] = None
    run_dir = job.get("run_dir")
    if run_dir:
        try:
            state = read_state(Path(run_dir))
            state_data = state.model_dump()
        except Exception:
            pass

    return StatusResponse(
        status=job["status"],
        issue_url=issue_url,
        run_dir=run_dir,
        outcome=job.get("outcome"),
        error=job.get("error"),
        state=state_data,
    )


# ---------------------------------------------------------------------------
# Background job runner
# ---------------------------------------------------------------------------

def _run_pipeline_job(req: IssueRequest) -> None:
    issue_url = req.issue_url

    with _jobs_lock:
        _jobs[issue_url]["status"] = "running"

    guidelines_path: Optional[str] = None
    tmp_guidelines: Optional[str] = None

    if req.guidelines:
        fd, tmp_guidelines = tempfile.mkstemp(suffix=".md")
        try:
            os.write(fd, req.guidelines.encode("utf-8"))
        finally:
            os.close(fd)
        guidelines_path = tmp_guidelines

    run_dir: Optional[Path] = None
    outcome = "fail"
    error: Optional[str] = None

    try:
        run_dir = run_pipeline(
            repo_url=req.repo_url,
            issue_url=req.issue_url,
            guidelines_path=guidelines_path,
            local_path=req.local_path,
        )
        outcome = "pass"
    except SystemExit:
        outcome = "fail"
    except Exception as e:
        traceback.print_exc()
        error = str(e)
        outcome = "fail"
    finally:
        if tmp_guidelines:
            try:
                os.unlink(tmp_guidelines)
            except OSError:
                pass

    final_status = "completed" if outcome == "pass" else "failed"
    with _jobs_lock:
        _jobs[issue_url]["status"] = final_status
        _jobs[issue_url]["run_dir"] = str(run_dir) if run_dir else None
        _jobs[issue_url]["outcome"] = outcome
        _jobs[issue_url]["error"] = error
