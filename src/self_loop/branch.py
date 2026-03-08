"""Branch management: ensure/sync self-loop branch; auto-merge."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from tools.log import get_logger

log = get_logger(__name__)


def ensure_self_loop_branch(repo_path: str, branch: str = "self-loop") -> None:
    """Ensure the self-loop branch exists locally and is up to date.

    If origin/<branch> exists, check it out and reset --hard.
    Otherwise, create it from main and push.
    """
    # Fetch all remotes
    subprocess.run(["git", "fetch", "--all"], cwd=repo_path, check=True)

    # Check if remote branch exists
    result = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch],
        cwd=repo_path, capture_output=True, text=True,
    )
    remote_exists = bool(result.stdout.strip())

    if remote_exists:
        # Check if local branch exists
        local = subprocess.run(
            ["git", "branch", "--list", branch],
            cwd=repo_path, capture_output=True, text=True,
        )
        if local.stdout.strip():
            subprocess.run(["git", "checkout", branch], cwd=repo_path, check=True)
        else:
            subprocess.run(
                ["git", "checkout", "-b", branch, f"origin/{branch}"],
                cwd=repo_path, check=True,
            )
        subprocess.run(
            ["git", "reset", "--hard", f"origin/{branch}"],
            cwd=repo_path, check=True,
        )
        log.debug(f"Checked out existing remote branch {branch}")
    else:
        # Create from main
        _checkout_or_create(repo_path, "main")
        local = subprocess.run(
            ["git", "branch", "--list", branch],
            cwd=repo_path, capture_output=True, text=True,
        )
        if not local.stdout.strip():
            subprocess.run(["git", "checkout", "-b", branch], cwd=repo_path, check=True)
        else:
            subprocess.run(["git", "checkout", branch], cwd=repo_path, check=True)
        subprocess.run(
            ["git", "push", "-u", "origin", branch],
            cwd=repo_path, check=True,
        )
        log.debug(f"Created and pushed new branch {branch}")


def sync_self_loop_branch(repo_path: str, branch: str = "self-loop") -> None:
    """Checkout branch, fetch, reset --hard to origin, clean working tree."""
    subprocess.run(["git", "fetch", "origin", branch], cwd=repo_path, check=True)
    local = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=repo_path, capture_output=True, text=True,
    )
    if local.stdout.strip():
        subprocess.run(["git", "checkout", branch], cwd=repo_path, check=True)
    else:
        subprocess.run(
            ["git", "checkout", "-b", branch, f"origin/{branch}"],
            cwd=repo_path, check=True,
        )
    subprocess.run(
        ["git", "reset", "--hard", f"origin/{branch}"],
        cwd=repo_path, check=True,
    )
    subprocess.run(["git", "clean", "-fd"], cwd=repo_path, check=True)
    log.debug(f"Synced {branch} to origin/{branch}")


def auto_merge_pr(pr_url: str, repo_path: str) -> bool:
    """Squash-merge the PR and delete the branch. Returns True on success."""
    result = subprocess.run(
        ["gh", "pr", "merge", pr_url, "--squash", "--auto", "--delete-branch"],
        cwd=repo_path, capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.error(f"gh pr merge failed: {result.stderr}")
        return False
    log.info(f"PR merged: {pr_url}")
    return True


def commit_state_to_branch(repo_path: str, state_file: str, branch: str = "self-loop") -> None:
    """Stage and commit STATE.json to the self-loop branch."""
    subprocess.run(["git", "add", state_file], cwd=repo_path, check=True)
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_path, capture_output=True,
    )
    if result.returncode == 0:
        log.debug("No changes to STATE.json; skipping commit")
        return
    subprocess.run(
        ["git", "commit", "-m", "chore: update self-loop STATE.json [skip ci]"],
        cwd=repo_path, check=True,
    )
    subprocess.run(
        ["git", "push", "origin", branch],
        cwd=repo_path, check=True,
    )
    log.debug("STATE.json committed and pushed")


def setup_run_worktree(repo_path: str, branch: str, run_dir: str) -> None:
    """Create or re-use the git worktree at run_dir checked out to branch."""
    wt_path = Path(run_dir).resolve()

    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo_path, capture_output=True, text=True, check=True,
    )
    registered = str(wt_path) in result.stdout

    if registered:
        log.debug(f"Worktree at {run_dir} already exists; syncing")
        sync_run_worktree(run_dir, branch)
    else:
        wt_path.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "worktree", "add", "--force", str(wt_path), branch],
            cwd=repo_path, check=True,
        )
        log.info(f"Created worktree at {run_dir} on branch {branch!r}")


def sync_run_worktree(run_dir: str, branch: str = "self-loop") -> None:
    """Reset the worktree at run_dir to origin/branch, clean working tree."""
    subprocess.run(["git", "fetch", "origin", branch], cwd=run_dir, check=True)
    local = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=run_dir, capture_output=True, text=True,
    )
    if local.stdout.strip():
        subprocess.run(["git", "checkout", branch], cwd=run_dir, check=True)
    else:
        subprocess.run(
            ["git", "checkout", "-b", branch, f"origin/{branch}"],
            cwd=run_dir, check=True,
        )
    subprocess.run(["git", "reset", "--hard", f"origin/{branch}"], cwd=run_dir, check=True)
    subprocess.run(["git", "clean", "-fd"], cwd=run_dir, check=True)
    log.debug(f"Synced worktree {run_dir} to origin/{branch}")


def copy_src_to_main(run_dir: str, repo_path: str) -> None:
    """Copy run_dir/src/ into repo_path/src/, overwriting existing files."""
    src_from = Path(run_dir) / "src"
    src_to = Path(repo_path) / "src"
    if not src_from.is_dir():
        log.warning(f"copy_src_to_main: {src_from} does not exist; skipping")
        return
    shutil.copytree(src_from, src_to, dirs_exist_ok=True)
    log.info(f"Copied {src_from} -> {src_to}")


def _checkout_or_create(repo_path: str, branch: str) -> None:
    local = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=repo_path, capture_output=True, text=True,
    )
    if local.stdout.strip():
        subprocess.run(["git", "checkout", branch], cwd=repo_path, check=True)
    else:
        subprocess.run(
            ["git", "checkout", "-b", branch, f"origin/{branch}"],
            cwd=repo_path, check=True,
        )
