"""Shell execution tool with allowlist enforcement."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from tools.fs import ToolResult, _err, _ok

# Shell metacharacters that are rejected
_SHELL_METACHARACTERS = [";", "|", "&", "$(", "`"]

# Full allowlist for all agents (binary, args_prefix)
DEFAULT_ALLOWLIST: list[tuple[str, str]] = [
    ("python", "-m pytest"),
    ("python", "-m mypy"),
    ("python", "-m ruff"),
    ("python", ""),
    ("node", ""),
    ("cargo", ""),
    ("npm", ""),
    ("pytest", ""),
    ("ruff", "check"),
    ("ruff", "format"),
    ("mypy", ""),
    ("git", "status"),
    ("git", "add"),
    ("git", "commit"),
    ("git", "rebase"),
    ("git", "push"),
    ("git", "branch"),
    ("git", "checkout"),
    ("git", "log"),
    ("gh", "issue"),
    ("gh", "pr"),
]


def execute_cli(
    cmd: str,
    allowlist: list[tuple[str, str]],
    cwd: Path,
) -> ToolResult:
    """
    Execute a CLI command subject to allowlist and safety checks.

    Args:
        cmd: Full command string (e.g. "pytest tests/")
        allowlist: List of (binary, allowed_args_prefix) pairs
        cwd: Working directory for execution (must be repo root)
    """
    # Reject shell metacharacters
    for meta in _SHELL_METACHARACTERS:
        if meta in cmd:
            return _err(f"Rejected: command contains shell metacharacter '{meta}'")

    try:
        parts = shlex.split(cmd)
    except ValueError as e:
        return _err(f"Failed to parse command: {e}")

    if not parts:
        return _err("Empty command")

    binary = parts[0]
    args = " ".join(parts[1:]) if len(parts) > 1 else ""

    # Reject .. in any argument
    for part in parts:
        if ".." in part.replace("\\", "/").split("/"):
            return _err(f"Rejected: '..' path component in argument '{part}'")

    # Check allowlist
    if not _check_allowlist(binary, args, allowlist):
        return _err(
            f"Rejected: '{binary}' with args '{args}' not in allowlist. "
            f"Allowed: {allowlist}"
        )

    try:
        result = subprocess.run(
            parts,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        combined = result.stdout
        if result.stderr:
            combined = combined + "\n--- stderr ---\n" + result.stderr if combined else result.stderr

        if result.returncode == 0:
            return _ok(combined.strip() or "(command succeeded, no output)")
        else:
            return _err(combined.strip() or f"Command failed with exit code {result.returncode}")
    except subprocess.TimeoutExpired:
        return _err("Command timed out after 300 seconds")
    except FileNotFoundError:
        return _err(f"Binary not found: {binary}")
    except Exception as e:
        return _err(str(e))


def _check_allowlist(
    binary: str,
    args: str,
    allowlist: list[tuple[str, str]],
) -> bool:
    for allowed_binary, allowed_prefix in allowlist:
        if binary != allowed_binary:
            continue
        # Empty prefix means any args are allowed
        if allowed_prefix == "" or args.startswith(allowed_prefix):
            return True
    return False
