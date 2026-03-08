"""Setup step: fetch issue, clone/verify repo, create branch."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

from schema.issue import Issue
from tools.log import get_logger


GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    print(
        "Error: GITHUB_TOKEN environment variable not set. Please set it to a GitHub Personal Access Token with repo access.",
        file=__import__("sys").stderr,
    )
    raise ValueError("GITHUB_TOKEN environment variable not set.")


log = get_logger(__name__)


def _run_hash(issue_url: str) -> str:
    import hashlib
    hash = hashlib.sha256(issue_url.encode()).hexdigest()
    log.debug(f"issue_url: {issue_url}, hash: {hash}, truncated: {hash[:8]}")
    return hash[:8]


def _is_pr(url: str) -> bool:
    return "/pull/" in url


def _get_repo_url(url: str) -> str:
    if _is_pr(url):
        return url.split("/pull/")[0]
    return url.split("/issues/")[0]


def run_setup(
    issue_url: str,
    local_path: str | None = None,
) -> Issue:
    log.debug(f"run_setup: issue_url={issue_url!r}, local_path={local_path!r}")
    hash = _run_hash(issue_url)
    is_pr = _is_pr(issue_url)
    repo_url = _get_repo_url(issue_url)

    # 1. Determine / acquire repo root
    if local_path:
        repo_root = Path(local_path).resolve()
        log.debug(f"Using local repo at: {repo_root}")
        _verify_clean_repo(repo_root)
        log.debug("Local repo verified clean")
    else:
        log.debug("No local_path provided; cloning repo")
        repo_root = _clone_repo(repo_url, hash)

    # 2. Fetch issue or PR
    if is_pr:
        issue_md, issue_body = _fetch_pr(issue_url)
    else:
        issue_md, issue_body = _fetch_issue(issue_url)

    # 3. Create feature branch
    branch_name = f"agent/{hash}"
    _create_branch(repo_root, branch_name)

    return Issue(
        url=issue_url,
        repo=repo_url,
        dir=repo_root,
        desc=issue_md
    )


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
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _clone_repo(repo_url: str, hash: str) -> Path:
    clone_dir = Path("run") / hash
    log.debug(f"_clone_repo: repo_url={repo_url!r}, clone_dir={clone_dir}")

    # Check if already cloned
    if clone_dir.exists():
        # Check if it's a valid repo and matches the URL
        valid = False
        if (clone_dir / ".git").exists():
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=clone_dir, capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip() == repo_url:
                valid = True
        
        if valid:
            log.debug(f"Repo already exists and is valid: {clone_dir}")
            # Reset and clean
            subprocess.run(["git", "fetch", "--all"], cwd=clone_dir, check=True)
            # Assuming main branch for reset; might need refinement
            subprocess.run(["git", "reset", "--hard", "origin/HEAD"], cwd=clone_dir, check=True)
            subprocess.run(["git", "clean", "-fd"], cwd=clone_dir, check=True)
            return clone_dir.resolve()
        
        log.debug(f"Clone dir invalid or mismatch; removing and re-cloning")
        shutil.rmtree(clone_dir, onexc=_force_remove_readonly)
        
    clone_dir.mkdir(parents=True, exist_ok=True)

    log.debug(f"Running: git clone --quiet {repo_url} {clone_dir}")
    result = subprocess.run(
        ["git", "clone", "--quiet", repo_url, str(clone_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed: {result.stderr}")

    resolved = clone_dir.resolve()
    log.debug(f"Repo cloned successfully to {resolved}")
    return resolved


def _fetch_issue(issue_url: str) -> tuple[str, str]:
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


def _fetch_pr(pr_url: str) -> tuple[str, str]:
    result = subprocess.run(
        ["gh", "pr", "view", pr_url, "--json", "title,body,comments,number,url"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh pr view failed: {result.stderr}")

    data = json.loads(result.stdout)
    title = data.get("title", "")
    body = data.get("body", "")
    number = data.get("number", "")
    url = data.get("url", pr_url)
    comments = data.get("comments", [])

    md_lines = [
        f"# PR #{number}: {title}",
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
        subprocess.run(["git", "branch", "-D", branch_name], cwd=repo_root, check=True)

    subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_root, check=True)
