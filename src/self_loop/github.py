"""GitHub CLI helpers: list/create issues, get CI status."""

from __future__ import annotations

import json
import subprocess

from tools.log import get_logger

log = get_logger(__name__)


def list_open_issues(repo_url: str) -> list[dict]:
    """Return list of open issues as dicts with number, title, url."""
    result = subprocess.run(
        ["gh", "issue", "list", "--repo", repo_url,
         "--state", "open", "--json", "number,title,url", "--limit", "100"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.warning(f"gh issue list failed: {result.stderr}")
        return []
    try:
        return json.loads(result.stdout)
    except Exception as e:
        log.warning(f"Failed to parse issue list: {e}")
        return []


def create_issue(repo_url: str, title: str, body: str, labels: list[str] | None = None) -> str:
    """Create a GitHub issue and return its URL."""
    cmd = ["gh", "issue", "create", "--repo", repo_url, "--title", title, "--body", body]
    if labels:
        cmd += ["--label", ",".join(labels)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh issue create failed: {result.stderr}")
    url = result.stdout.strip()
    log.debug(f"Created issue: {url}")
    return url


def get_pr_ci_status(pr_url: str, repo_path: str) -> str:
    """Return 'pass', 'fail', or 'pending' for the PR's CI checks."""
    result = subprocess.run(
        ["gh", "pr", "checks", pr_url, "--json", "state"],
        cwd=repo_path, capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.warning(f"gh pr checks failed: {result.stderr}")
        return "fail"
    try:
        checks = json.loads(result.stdout)
        states = [c.get("state", "").lower() for c in checks]
        if any(s in ("fail", "failure", "error") for s in states):
            return "fail"
        if any(s in ("pending", "in_progress", "queued") for s in states):
            return "pending"
        return "pass"
    except Exception as e:
        log.warning(f"Failed to parse pr checks: {e}")
        return "fail"


def wait_for_ci(pr_url: str, repo_path: str) -> str:
    """Block until CI finishes. Returns 'pass' or 'fail'."""
    log.info(f"Waiting for CI on {pr_url}")
    result = subprocess.run(
        ["gh", "pr", "checks", pr_url, "--watch"],
        cwd=repo_path, capture_output=True, text=True,
    )
    status = "pass" if result.returncode == 0 else "fail"
    log.info(f"CI finished with: {status}")
    return status
