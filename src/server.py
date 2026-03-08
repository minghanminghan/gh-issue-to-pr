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
    local_path: str | None = None
    guidelines: str | None = None  # inline string; written to tempfile internally
    budget: float | None = Field(default=None, gt=0)
    model_name: str | None = Field(default=None)
    max_steps: int | None = Field(default=None, gt=0)
    model_api_key: str | None = None
    model_endpoint: str | None = None


class AcceptedResponse(BaseModel):
    issue_url: str
    status_url: str


class StatusResponse(BaseModel):
    status: str  # "queued" | "running" | "completed" | "failed"
    issue_url: str
    run_dir: str | None = None
    outcome: str | None = None
    error: str | None = None
    finish_reason: str | None = None  # set when completed/failed: "submitted", "limits_exceeded", ...


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>gh-issue-to-pr</title>
  <script src="https://unpkg.com/htmx.org@2.0.4"></script>

  <style>
    body { font-family: system-ui, -apple-system, sans-serif; max-width: 800px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; color: #333; }
    form { display: flex; flex-direction: column; gap: 1rem; margin-top: 2rem; border: 1px solid #ddd; padding: 1.5rem; border-radius: 8px; background-color: #f9f9f9; }
    label { display: flex; flex-direction: column; gap: 0.25rem; font-weight: 500; }
    input, textarea { padding: 0.6rem; border: 1px solid #ccc; border-radius: 4px; font-size: 1rem; }
    textarea { height: 100px; }
    fieldset { border: 1px solid #ddd; padding: 1rem; border-radius: 4px; margin-top: 1rem; }
    legend { font-weight: bold; padding: 0 0.5rem; }
    button { padding: 0.75rem; background-color: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 1rem; font-weight: bold; margin-top: 1rem; }
    button:hover { background-color: #0056b3; }
    #jobs { margin-bottom: 2rem; border-bottom: 2px solid #eee; padding-bottom: 1rem; }
    ul { padding: 0; list-style: none; }
    li { padding: 0.5rem; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; }
    li span { font-weight: bold; color: #555; }
  </style>

</head>
<body>
  <h1>gh-issue-to-pr</h1>

  <section id="jobs" hx-get="/jobs" hx-trigger="load, every 3s" hx-swap="innerHTML">
  </section>

  <form id="submit-form">
    <label>Issue URL *<input type="url" name="issue_url" required></label>
    <label>Local path <span>(optional)</span><input type="text" name="local_path"></label>
    <label>Guidelines <span>(optional)</span><textarea name="guidelines"></textarea></label>
    <fieldset>
      <legend>Model (optional)</legend>
      <label>Model name<input type="text" name="model_name" placeholder="anthropic/claude-..."></label>
      <label>API endpoint<input type="url" name="model_endpoint"></label>
      <label>API key<input type="password" name="model_api_key"></label>
    </fieldset>
    <fieldset>
      <legend>Limits (optional)</legend>
      <label>Max steps<input type="number" name="max_steps" min="1"></label>
      <label>Budget (USD)<input type="number" name="budget" step="0.01" min="0.01" placeholder="2.00"></label>
    </fieldset>
    <button>Submit</button>
  </form>

  <script>
    const form = document.getElementById('submit-form');
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const raw = Object.fromEntries(new FormData(form));
      const data = {};
      for (const [k, v] of Object.entries(raw)) {
        if (v === '') continue;
        if (k === 'max_steps') data[k] = parseInt(v, 10);
        else if (k === 'budget') data[k] = parseFloat(v);
        else data[k] = v;
      }
      await fetch('/issue', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
      form.reset();
      htmx.trigger('#jobs', 'load');
    });
  </script>
</body>
</html>"""


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
            "finish_reason": None,
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


@app.get("/jobs", response_class=HTMLResponse)
def get_jobs() -> str:
    log.debug("GET /jobs")
    with _jobs_lock:
        jobs_snapshot = dict(_jobs)

    if not jobs_snapshot:
        return "<p>No jobs yet.</p>"

    items = []
    for url, job in jobs_snapshot.items():
        status = job["status"]
        finish_reason = job.get("finish_reason")
        if status in ("queued", "running"):
            badge = f'<span>{status}</span>'
        else:
            reason_str = f" ({finish_reason})" if finish_reason else ""
            badge = f'<span>{status}{reason_str}</span>'
        items.append(f'<li><a href="{url}" target="_blank">{url}</a> {badge}</li>')

    return "<ul>" + "".join(items) + "</ul>"


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
        finish_reason=job.get("finish_reason"),
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
    finish_reason: str | None = None
    error: str | None = None

    try:
        log.debug(f"Invoking run_pipeline for {issue_url!r}")
        outcome, finish_reason = run_pipeline(
            issue_url=req.issue_url,
            guidelines_path=guidelines_path,
            local_path=req.local_path,
            model_name=req.model_name,
            max_steps=req.max_steps,
            budget=req.budget,
            model_api_key=req.model_api_key,
            model_endpoint=req.model_endpoint,
        )
        log.debug(f"run_pipeline completed: outcome={outcome!r}, finish_reason={finish_reason!r}")
    except SystemExit:
        log.debug("run_pipeline called sys.exit(); outcome=fail")
        outcome = "fail"
        finish_reason = "sys_exit"
    except Exception as e:
        log.debug(f"run_pipeline raised exception: type={type(e).__name__}, msg={e!r}")
        traceback.print_exc()
        error = str(e)
        outcome = "fail"
        finish_reason = "exception"
    finally:
        if tmp_guidelines:
            log.debug(f"Removing temp guidelines file: {tmp_guidelines}")
            try:
                os.unlink(tmp_guidelines)
            except OSError:
                pass

    final_status = "completed" if outcome == "pass" else "failed"
    log.debug(
        f"Job done: issue_url={issue_url!r}, outcome={outcome!r}, final_status={final_status!r}, finish_reason={finish_reason!r}, run_dir={run_dir}, error={error!r}"
    )
    with _jobs_lock:
        _jobs[issue_url]["status"] = final_status
        _jobs[issue_url]["run_dir"] = str(run_dir) if run_dir else None
        _jobs[issue_url]["outcome"] = outcome
        _jobs[issue_url]["finish_reason"] = finish_reason
        _jobs[issue_url]["error"] = error
