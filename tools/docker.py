"""Docker container lifecycle management for sandboxed code execution."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def _docker_available() -> bool:
    """Return True if the docker binary exists on PATH."""
    return shutil.which("docker") is not None


def start_container(repo_root: Path, image: str = "python:3.11-slim") -> str | None:
    """
    Start a Docker container with repo_root mounted at /workspace.

    Returns the container_id string on success, or None if Docker is not
    available (soft-fail: pipeline continues without sandbox).

    Raises RuntimeError if Docker is available but the container fails to start.
    """
    if not _docker_available():
        print(
            "Warning: docker binary not found. Running without code sandbox.",
            file=sys.stderr,
        )
        return None

    repo_root = Path(repo_root).resolve()
    cmd = [
        "docker", "run",
        "--detach",
        "--rm",
        "--volume", f"{repo_root}:/workspace",
        "--workdir", "/workspace",
        image,
        "sleep", "infinity",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"docker run failed: {result.stderr.strip()}")
    return result.stdout.strip()


def stop_container(container_id: str) -> None:
    """
    Stop a running container by ID.

    Silently ignores errors (container may already be gone).
    The --rm flag in start_container means removal is automatic on stop.
    """
    if not container_id:
        return
    try:
        subprocess.run(
            ["docker", "stop", container_id],
            capture_output=True,
            text=True,
        )
    except Exception:
        pass


def install_project_deps(container_id: str, repo_root: Path) -> None:
    """
    Install project dependencies inside the container if pyproject.toml exists.

    Runs: docker exec <container_id> pip install -e . --quiet
    Silently skips if pyproject.toml is absent.
    Warns (does not raise) if pip install fails.
    """
    if not (Path(repo_root) / "pyproject.toml").exists():
        return
    result = subprocess.run(
        ["docker", "exec", container_id, "pip", "install", "-e", ".", "--quiet"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            f"Warning: pip install -e . in container failed: {result.stderr.strip()}",
            file=sys.stderr,
        )
