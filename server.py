"""FastAPI web server exposing the pipeline as an HTTP API."""

from __future__ import annotations

import os
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from pipeline import run_pipeline
from tools.log import get_logger
log = get_logger(__name__)


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
    local_path: str | None = None
    guidelines: str | None = None  # inline string; written to tempfile internally
    budget: float = Field(default=2.0, gt=0)  # TODO: propagate to AgentConfig
    model_name: str | None = Field(default=None)
    max_steps: int | None = Field(default=None, gt=0)


class AcceptedResponse(BaseModel):
    issue_url: str
    status_url: str


class StatusResponse(BaseModel):
    status: str  # "queued" | "running" | "completed" | "failed"
    issue_url: str
    run_dir: str | None = None
    outcome: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# class IssueRequest(BaseModel):
#     issue_url: str
#     repo_url: str
#     local_path: Optional[str] = None
#     guidelines: Optional[str] = None  # inline string; written to tempfile internally
#     config_path: Optional[str] = None # Path to a custom agent configuration YAML file
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
    log.debug("GET /health")
    return {"status": "ok", "version": "0.1.0"}


@app.post("/issue", status_code=202, response_model=AcceptedResponse)
def submit_issue(req: IssueRequest) -> AcceptedResponse:
    """
    Accept a pipeline job and start it in the background.

    Returns HTTP 202 immediately with a status polling URL.
    Returns HTTP 422 for invalid input (handled by FastAPI/Pydantic).
    """
    issue_url = req.issue_url
    log.debug(
        f"POST /issue: issue_url={issue_url!r}, model_name={req.model_name!r}, max_steps={req.max_steps!r}, local_path={req.local_path!r}, budget={req.budget!r}"
    )
    status_url = f"/status?issue_url={issue_url}"

    with _jobs_lock:
        existing = _jobs.get(issue_url)
        if existing and existing["status"] in ("queued", "running"):
            log.debug(
                f"Job for {issue_url!r} already in status={existing['status']!r}; returning existing status_url"
            )
            # Already in progress — return 202 pointing to the same status URL
            return AcceptedResponse(issue_url=issue_url, status_url=status_url)
        log.debug(f"Registering new job for {issue_url!r} with status='queued'")
        _jobs[issue_url] = {
            "status": "queued",
            "run_dir": None,
            "outcome": None,
            "error": None,
        }

    log.debug(f"Spawning background thread for job: {issue_url!r}")
    thread = threading.Thread(
        target=_run_pipeline_job,
        args=(req,),
        daemon=True,
    )
    thread.start()
    log.debug(f"Background thread started; returning status_url={status_url!r}")

    return AcceptedResponse(issue_url=issue_url, status_url=status_url)


@app.get("/status", response_model=StatusResponse)
def get_status(issue_url: str) -> StatusResponse:
    """
    Return the current status of a pipeline job.

    Poll this endpoint after POST /issue returns 202.
    """
    log.debug(f"GET /status: issue_url={issue_url!r}")
    with _jobs_lock:
        job = _jobs.get(issue_url)

    if job is None:
        log.debug(f"No job found for issue_url={issue_url!r}; returning 404")
        raise HTTPException(status_code=404, detail="No job found for this issue URL")

    log.debug(
        f"Job status for {issue_url!r}: status={job['status']!r}, outcome={job.get('outcome')!r}, error={job.get('error')!r}"
    )
    return StatusResponse(
        status=job["status"],
        issue_url=issue_url,
        outcome=job.get("outcome"),
        error=job.get("error"),
    )


# ---------------------------------------------------------------------------
# Background job runner
# ---------------------------------------------------------------------------


def _run_pipeline_job(req: IssueRequest) -> None:
    issue_url = req.issue_url
    log.debug(f"_run_pipeline_job started: issue_url={issue_url!r}")

    with _jobs_lock:
        _jobs[issue_url]["status"] = "running"
    log.debug(f"Job status set to 'running' for {issue_url!r}")

    guidelines_path: str | None = None
    tmp_guidelines: str | None = None

    if req.guidelines:
        fd, tmp_guidelines = tempfile.mkstemp(suffix=".md")
        log.debug(f"Writing guidelines to temp file: {tmp_guidelines}")
        try:
            os.write(fd, req.guidelines.encode("utf-8"))
        finally:
            os.close(fd)
        guidelines_path = tmp_guidelines
        log.debug(
            f"Guidelines written: {len(req.guidelines)} chars to {tmp_guidelines}"
        )
    else:
        log.debug("No inline guidelines provided")

    run_dir: Path | None = None
    outcome = "fail"
    error: str | None = None

    try:
        log.debug(f"Invoking run_pipeline for {issue_url!r}")
        run_pipeline(
            issue_url=req.issue_url,
            guidelines_path=guidelines_path,
            local_path=req.local_path,
            model_name=req.model_name,
            max_steps=req.max_steps,
        )
        log.debug("run_pipeline completed.")
        outcome = "pass"
    except SystemExit:
        log.debug("run_pipeline called sys.exit(); outcome=fail")
        outcome = "fail"
    except Exception as e:
        log.debug(f"run_pipeline raised exception: type={type(e).__name__}, msg={e!r}")
        traceback.print_exc()
        error = str(e)
        outcome = "fail"
    finally:
        if tmp_guidelines:
            log.debug(f"Removing temp guidelines file: {tmp_guidelines}")
            try:
                os.unlink(tmp_guidelines)
            except OSError:
                pass

    final_status = "completed" if outcome == "pass" else "failed"
    log.debug(
        f"Job done: issue_url={issue_url!r}, outcome={outcome!r}, final_status={final_status!r}, run_dir={run_dir}, error={error!r}"
    )
    with _jobs_lock:
        _jobs[issue_url]["status"] = final_status
        _jobs[issue_url]["run_dir"] = str(run_dir) if run_dir else None
        _jobs[issue_url]["outcome"] = outcome
        _jobs[issue_url]["error"] = error
