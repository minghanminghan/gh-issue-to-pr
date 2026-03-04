"""FastAPI web server exposing the pipeline as an HTTP API."""

from __future__ import annotations

import os
import tempfile
import traceback
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from pipeline import run_pipeline
from tools.state import read_state

app = FastAPI(title="gh-issue-to-pr", version="0.1.0")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class IssueRequest(BaseModel):
    issue_url: str
    repo_url: str
    local_path: Optional[str] = None
    guidelines: Optional[str] = None  # inline string; written to tempfile internally
    budget: float = Field(default=2.0, gt=0)


class IssueResponse(BaseModel):
    run_dir: str
    outcome: str              # "pass" | "fail"
    pr_url: Optional[str] = None
    cost_spent_usd: float
    loop_count: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


@app.post("/issue", response_model=IssueResponse)
def run_issue(req: IssueRequest) -> IssueResponse:
    """
    Run the full pipeline synchronously.

    Returns HTTP 200 even on pipeline failure (outcome="fail").
    Returns HTTP 422 for invalid input (handled by FastAPI/Pydantic).
    Returns HTTP 500 for unexpected exceptions.
    """
    guidelines_path: Optional[str] = None
    tmp_guidelines: Optional[str] = None

    if req.guidelines:
        # Write inline guidelines string to a tempfile for run_pipeline
        fd, tmp_guidelines = tempfile.mkstemp(suffix=".md")
        try:
            os.write(fd, req.guidelines.encode("utf-8"))
        finally:
            os.close(fd)
        guidelines_path = tmp_guidelines

    run_dir: Optional[Path] = None
    outcome = "fail"

    try:
        # run_pipeline may call sys.exit(1) via run_report on failure.
        # Catch SystemExit to prevent killing the server process.
        run_dir = run_pipeline(
            repo_url=req.repo_url,
            issue_url=req.issue_url,
            guidelines_path=guidelines_path,
            local_path=req.local_path,
        )
        outcome = "pass"
    except SystemExit:
        outcome = "fail"
    except Exception:
        traceback.print_exc()
        raise  # FastAPI converts unhandled exceptions to HTTP 500
    finally:
        if tmp_guidelines:
            try:
                os.unlink(tmp_guidelines)
            except OSError:
                pass

    pr_url: Optional[str] = None
    cost_spent_usd: float = 0.0
    loop_count: int = 0

    if run_dir is not None:
        try:
            state = read_state(run_dir)
            pr_url = state.pr_url
            cost_spent_usd = state.cost_spent_usd
            loop_count = state.loop_count
        except Exception:
            pass

    return IssueResponse(
        run_dir=str(run_dir) if run_dir else "",
        outcome=outcome,
        pr_url=pr_url,
        cost_spent_usd=cost_spent_usd,
        loop_count=loop_count,
    )
