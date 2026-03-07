"""Setup step: fetch issue, clone/verify repo, create branch."""

from __future__ import annotations

import json
import os
import shutil
import stat
import hashlib
import subprocess
from pathlib import Path

from schema.issue import Issue
from tools.log import get_logger


GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")  # must have repo access; used by gh CLI
if not GITHUB_TOKEN:
    print(
        "Error: GITHUB_TOKEN environment variable not set. Please set it to a GitHub Personal Access Token with repo access.",
        file=__import__("sys").stderr,
    )
    raise ValueError("GITHUB_TOKEN environment variable not set.")


log = get_logger(__name__)


def _run_hash(issue_url: str) -> str:
    hash = hashlib.sha256(issue_url.encode()).hexdigest()
    log.debug(f"issue_url: {issue_url}, hash: {hash}, truncated: {hash[:8]}")
    return hash[:8]


def run_setup(
    issue_url: str,
    local_path: str | None = None,
) -> Issue:
    """
    Step 0 (pre-flight, deterministic — no agent).

    1. Fetch issue via `gh issue view`; write ISSUE.md to run dir
    2. Clone repo if not already local, or verify local path is clean
    3. Create branch `agent/<hash>`; overwrite and log if it already exists

    Return issue as markdown (title, description)
    """
    log.debug(f"run_setup: issue_url={issue_url!r}, local_path={local_path!r}")
    hash = _run_hash(issue_url)

    # 1. Determine / acquire repo root
    if local_path:
        repo_root = Path(local_path).resolve()
        log.debug(f"Using local repo at: {repo_root}")
        _verify_clean_repo(repo_root)
        log.debug("Local repo verified clean")
    else:
        log.debug("No local_path provided; cloning repo")
        repo_root = _clone_repo(issue_url, hash)

    # 2. Fetch issue
    issue_md, issue_body = _fetch_issue(issue_url)

    # 3. Create feature branch
    branch_name = f"agent/{hash}"
    _create_branch(repo_root, branch_name)

    return Issue(
        url=issue_url,
        repo=issue_url.split("/issues/")[0],
        dir=repo_root,
        desc=issue_md
    )


# Private helpers


def _verify_clean_repo(repo_root: Path) -> None:
    log.debug(f"Verifying repo is clean: {repo_root}")
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    log.debug(
        f"git status --porcelain returncode={result.returncode}, stdout={result.stdout!r}"
    )
    if result.returncode != 0:
        raise RuntimeError(f"git status failed: {result.stderr}")
    if result.stdout.strip():
        raise RuntimeError(
            f"Local repo has uncommitted changes:\n{result.stdout}\n"
            "Please commit or stash before running the pipeline."
        )
    log.debug("Repo is clean")


def _force_remove_readonly(func, path, _exc_info):
    """onerror handler for shutil.rmtree: clear read-only bit and retry."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _clone_repo(issue_url: str, hash: str) -> Path:
    """Clone a repository and return the local path."""
    clone_dir = Path("run") / hash
    repo_url = issue_url.split("/issues/")[0]
    log.debug(f"_clone_repo: repo_url={repo_url!r}, clone_dir={clone_dir}")

    # Check if already cloned
    if clone_dir.exists():
        log.debug(f"Clone dir already exists at {clone_dir}; removing and re-cloning")
        shutil.rmtree(clone_dir, onexc=_force_remove_readonly)
    clone_dir.mkdir(parents=True, exist_ok=True)

    log.debug(f"Running: git clone --quiet {repo_url} {clone_dir}")
    result = subprocess.run(
        ["git", "clone", "--quiet", repo_url, str(clone_dir)],
        capture_output=True,
        text=True,
    )
    # log.debug(f"git clone returncode={result.returncode}, stderr={result.stderr!r}")
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed: {result.stderr}")

    resolved = clone_dir.resolve()
    log.debug(f"Repo cloned successfully to {resolved}")
    return resolved


def _fetch_issue(issue_url: str) -> tuple[str, str]:
    """Fetch issue via gh CLI and return (markdown, body_text)."""
    log.debug(f"_fetch_issue: issue_url={issue_url!r}")
    result = subprocess.run(
        ["gh", "issue", "view", issue_url, "--json", "title,body,comments,number,url"],
        capture_output=True,
        text=True,
    )
    log.debug(
        f"gh issue view returncode={result.returncode}, stdout_len={len(result.stdout)}, stderr={result.stderr!r}"
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh issue view failed: {result.stderr}")

    data = json.loads(result.stdout)
    title = data.get("title", "")
    body = data.get("body", "")
    number = data.get("number", "")
    url = data.get("url", issue_url)
    comments = data.get("comments", [])
    log.debug(
        f"Issue fetched: number={number!r}, title={title!r}, comments={len(comments)}"
    )

    md_lines = [
        f"# Issue #{number}: {title}",
        f"URL: {url}",
        "",
        "## Description",
        "",
        body or "(no description)",
    ]

    if comments:
        md_lines += ["", "## Comments", ""]
        for c in comments:
            author = c.get("author", {}).get("login", "unknown")
            created = c.get("createdAt", "")
            comment_body = c.get("body", "")
            md_lines += [
                f"### {author} ({created})",
                "",
                comment_body,
                "",
            ]

    return "\n".join(md_lines), body


def _create_branch(repo_root: Path, branch_name: str) -> None:
    log.debug(f"_create_branch: repo_root={repo_root}, branch_name={branch_name!r}")
    # Check if branch already exists; force-reset it if so
    result = subprocess.run(
        ["git", "branch", "--list", branch_name],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    log.debug(
        f"git branch --list returncode={result.returncode}, stdout={result.stdout!r}"
    )
    if result.stdout.strip():
        log.debug(f"Branch {branch_name!r} already exists; deleting it")
        print(
            f"Setup: branch '{branch_name}' already exists — deleting and recreating.",
            file=__import__("sys").stderr,
        )
        delete_result = subprocess.run(
            ["git", "branch", "-D", branch_name],
            cwd=repo_root,
            capture_output=True,
        )
        log.debug(f"git branch -D returncode={delete_result.returncode}")

    log.debug(f"Running: git checkout -b {branch_name!r} in {repo_root}")
    result = subprocess.run(
        ["git", "checkout", "-b", branch_name],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    log.debug(
        f"git checkout -b returncode={result.returncode}, stderr={result.stderr!r}"
    )
    if result.returncode != 0:
        raise RuntimeError(f"git checkout -b failed: {result.stderr}")
    log.debug(f"Branch {branch_name!r} created and checked out")
