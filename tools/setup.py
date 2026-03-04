"""Setup step: fetch issue, clone/verify repo, create branch, init STATE.json."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from schemas.state import Step
from tools.docker import install_project_deps, start_container
from tools.state import init_run, read_state, write_state, _run_hash
from tools.trace import open_trace


def run_setup(
    repo_url: str,
    issue_url: str,
    local_path: str | None = None,
) -> Path:
    """
    Step 0 (pre-flight, deterministic — no agent).

    1. Fetch issue via `gh issue view`; write ISSUE.md to run dir
    2. Clone repo if not already local, or verify local path is clean
    3. Create branch `agent/<hash>`; fail loudly if it already exists
    4. Populate STATE.json

    Returns run_dir (the .agent/<hash>/ directory inside repo root).
    """
    run_hash = _run_hash(issue_url)

    # ------------------------------------------------------------------ #
    # 1. Determine / acquire repo root
    # ------------------------------------------------------------------ #
    if local_path:
        repo_root = Path(local_path).resolve()
        _verify_clean_repo(repo_root)
    else:
        repo_root = _clone_repo(repo_url, run_hash)

    # ------------------------------------------------------------------ #
    # 2. Initialise run directory and open trace
    # ------------------------------------------------------------------ #
    run_dir = init_run(repo_url, issue_url, repo_root)
    open_trace(run_dir)

    # ------------------------------------------------------------------ #
    # 3. Fetch issue
    # ------------------------------------------------------------------ #
    issue_md, issue_body = _fetch_issue(issue_url)
    (run_dir / "ISSUE.md").write_text(issue_md, encoding="utf-8")

    # ------------------------------------------------------------------ #
    # 4. Create feature branch
    # ------------------------------------------------------------------ #
    branch_name = f"agent/{run_hash}"
    _create_branch(repo_root, branch_name)

    # ------------------------------------------------------------------ #
    # 5. Update STATE.json
    # ------------------------------------------------------------------ #
    state = read_state(run_dir)
    state.branch_name = branch_name
    state.issue_body = issue_body
    state.current_step = Step.plan
    write_state(run_dir, state)

    # ------------------------------------------------------------------ #
    # 6. Start Docker sandbox (optional — no-op if docker not found)
    # ------------------------------------------------------------------ #
    container_id = start_container(repo_root)
    if container_id:
        install_project_deps(container_id, repo_root)
        state = read_state(run_dir)
        state.container_id = container_id
        write_state(run_dir, state)

    return run_dir


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

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


def _clone_repo(repo_url: str, run_hash: str) -> Path:
    clone_dir = Path(".agent") / f"repos" / run_hash
    clone_dir.mkdir(parents=True, exist_ok=True)

    # Check if already cloned
    if (clone_dir / ".git").exists():
        result = subprocess.run(
            ["git", "fetch", "--quiet"],
            cwd=clone_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git fetch failed: {result.stderr}")
        return clone_dir.resolve()

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
    # Check if branch already exists
    result = subprocess.run(
        ["git", "branch", "--list", branch_name],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        raise RuntimeError(
            f"Branch '{branch_name}' already exists. "
            "Delete it or use a different issue URL."
        )

    result = subprocess.run(
        ["git", "checkout", "-b", branch_name],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git checkout -b failed: {result.stderr}")
