"""SelfLoopConfig TypedDict."""

from __future__ import annotations

from typing import TypedDict


class SelfLoopConfig(TypedDict):
    repo_local_path: str         # Absolute path to this repo
    repo_github_url: str         # e.g. "https://github.com/owner/gh-issue-to-pr"
    self_loop_branch: str        # Always "self-loop"
    max_iterations: int          # Hard cap (default: 10)
    max_total_budget_usd: float  # Total across all runs (default: 30.0)
    per_run_budget_usd: float    # Per pipeline run (default: 3.0)
    per_run_max_steps: int       # Per pipeline run (default: 100)
    scanner_model: str           # Model for scanner (can be cheaper)
    fix_model: str               # Model for fix agent
    state_file: str              # "self-loop/STATE.json"
    dry_run: bool                # Scan + print, no issue creation
    min_issue_priority: str      # "critical"|"high"|"medium"|"low"
    guidelines_path: str | None
