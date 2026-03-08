"""Branch management: ensure/sync self-loop branch; auto-merge."""

from __future__ import annotations

import subprocess

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
