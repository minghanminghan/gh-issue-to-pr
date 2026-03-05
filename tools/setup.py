"""Setup step: fetch issue, clone/verify repo, create branch."""

from __future__ import annotations

import json
import os
import shutil
import stat
import hashlib
import subprocess
from pathlib import Path

# from schema.state import Step
from tools.trace import open_trace
from tools.logger import log_event


GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")  # must have repo access; used by gh CLI
if not GITHUB_TOKEN:
    print("Error: GITHUB_TOKEN environment variable not set. Please set it to a GitHub Personal Access Token with repo access.", file=__import__("sys").stderr)
    raise ValueError("GITHUB_TOKEN environment variable not set.")


def _run_hash(issue_url: str) -> str:
    return hashlib.sha256(issue_url.encode()).hexdigest()[:8]


def init_run(issue_url: str, repo_root: Path) -> Path:
    """Create run directory and add .agent/ to .gitignore."""
    repo_root = Path(repo_root)
    hash = _run_hash(issue_url)
    agent_dir = repo_root / ".agent"
    run_dir = agent_dir / hash

    run_dir.mkdir(parents=True, exist_ok=True)

    # Add .agent/ to .gitignore if not present
    gitignore_path = repo_root / ".gitignore"
    _ensure_gitignore_entry(gitignore_path, ".agent/")

    return run_dir


def _ensure_gitignore_entry(gitignore_path: Path, entry: str) -> None:
    if gitignore_path.exists():
        lines = gitignore_path.read_text().splitlines()
        if entry in lines or entry.rstrip("/") in lines:
            return
        with open(gitignore_path, "a") as f:
            f.write(f"\n{entry}\n")
    else:
        gitignore_path.write_text(f"{entry}\n")


def run_setup(
    issue_url: str,
    local_path: str | None = None,
) -> Path:
    """
    Step 0 (pre-flight, deterministic — no agent).

    1. Fetch issue via `gh issue view`; write ISSUE.md to run dir
    2. Clone repo if not already local, or verify local path is clean
    3. Create branch `agent/<hash>`; overwrite and log if it already exists

    Returns run_dir (the .agent/<hash>/ directory inside repo root).
    """
    hash = _run_hash(issue_url)

    # 1. Determine / acquire repo root
    if local_path:
        repo_root = Path(local_path).resolve()
        _verify_clean_repo(repo_root)
    else:
        repo_root = _clone_repo(issue_url, hash)

    # 2. Initialise run directory and open trace
    run_dir = init_run(issue_url, repo_root)
    open_trace(run_dir)

    # 3. Fetch issue
    issue_md, issue_body = _fetch_issue(issue_url)
    (run_dir / "ISSUE.md").write_text(issue_md, encoding="utf-8")
    
    # 4. Create feature branch
    branch_name = f"agent/{hash}"
    _create_branch(repo_root, branch_name)

    return run_dir


# Private helpers

def _verify_clean_repo(repo_root: Path) -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git status failed: {result.stderr}")
    if result.stdout.strip():
        raise RuntimeError(
            f"Local repo has uncommitted changes:\n{result.stdout}\n"
            "Please commit or stash before running the pipeline."
        )


def _force_remove_readonly(func, path, _exc_info):
    """onerror handler for shutil.rmtree: clear read-only bit and retry."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _clone_repo(issue_url: str, hash: str) -> Path:
    clone_dir = Path(".agent") / hash
    repo_url = issue_url.split("/issues/")[0]

    # Check if already cloned
    if clone_dir.exists():
        log_event(clone_dir, "overwriting_existing_issue", {"issue_url": issue_url, "hash": hash})
        shutil.rmtree(clone_dir, onerror=_force_remove_readonly)
    clone_dir.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        ["git", "clone", "--quiet", repo_url, str(clone_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed: {result.stderr}")

    return clone_dir.resolve()


def _fetch_issue(issue_url: str) -> tuple[str, str]:
    """Fetch issue via gh CLI and return (markdown, body_text)."""
    result = subprocess.run(
        ["gh", "issue", "view", issue_url, "--json", "title,body,comments,number,url"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh issue view failed: {result.stderr}")

    data = json.loads(result.stdout)
    title = data.get("title", "")
    body = data.get("body", "")
    number = data.get("number", "")
    url = data.get("url", issue_url)
    comments = data.get("comments", [])

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
    # Check if branch already exists; force-reset it if so
    result = subprocess.run(
        ["git", "branch", "--list", branch_name],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        print(
            f"Setup: branch '{branch_name}' already exists — deleting and recreating.",
            file=__import__("sys").stderr,
        )
        subprocess.run(
            ["git", "branch", "-D", branch_name],
            cwd=repo_root,
            capture_output=True,
        )

    result = subprocess.run(
        ["git", "checkout", "-b", branch_name],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git checkout -b failed: {result.stderr}")
