"""File-system tool primitives used by agents."""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path
from typing import Optional, TypedDict


class ToolResult(TypedDict):
    ok: bool
    output: str
    error: Optional[str]


def _ok(output: str) -> ToolResult:
    return {"ok": True, "output": output, "error": None}


def _err(error: str) -> ToolResult:
    return {"ok": False, "output": "", "error": error}


def read_file(path: str, repo_root: Path | None = None) -> ToolResult:
    try:
        p = _resolve(path, repo_root)
        if not p.exists():
            return _err(f"File not found: {path}")
        return _ok(p.read_text(encoding="utf-8"))
    except Exception as e:
        return _err(str(e))


def write_file(
    path: str,
    content: str,
    repo_root: Path | None = None,
    read_only: list[str] | None = None,
) -> ToolResult:
    try:
        if read_only and _is_read_only(path, read_only):
            return _err(f"Path is read-only: {path}")
        p = _resolve(path, repo_root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return _ok(f"Written: {path}")
    except Exception as e:
        return _err(str(e))


def create_file(
    path: str,
    content: str,
    repo_root: Path | None = None,
    read_only: list[str] | None = None,
) -> ToolResult:
    try:
        if read_only and _is_read_only(path, read_only):
            return _err(f"Path is read-only: {path}")
        p = _resolve(path, repo_root)
        if p.exists():
            return _err(f"File already exists: {path}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return _ok(f"Created: {path}")
    except Exception as e:
        return _err(str(e))


def append_file(
    path: str,
    content: str,
    repo_root: Path | None = None,
    read_only: list[str] | None = None,
) -> ToolResult:
    try:
        if read_only and _is_read_only(path, read_only):
            return _err(f"Path is read-only: {path}")
        p = _resolve(path, repo_root)
        if not p.exists():
            return _err(f"File not found (use create_file for new files): {path}")
        with open(p, "a", encoding="utf-8") as f:
            f.write(content)
        return _ok(f"Appended to: {path}")
    except Exception as e:
        return _err(str(e))


def list_dir(path: str, repo_root: Path | None = None) -> ToolResult:
    try:
        p = _resolve(path, repo_root)
        if not p.exists():
            return _err(f"Directory not found: {path}")
        if not p.is_dir():
            return _err(f"Not a directory: {path}")
        entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
        lines = []
        for entry in entries:
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{entry.name}{suffix}")
        return _ok("\n".join(lines))
    except Exception as e:
        return _err(str(e))


def grep(
    pattern: str,
    path: str,
    repo_root: Path | None = None,
    flags: str = "",
) -> ToolResult:
    try:
        p = _resolve(path, repo_root)
        re_flags = 0
        if "i" in flags:
            re_flags |= re.IGNORECASE

        compiled = re.compile(pattern, re_flags)
        results: list[str] = []

        files = [p] if p.is_file() else list(p.rglob("*"))
        for file in files:
            if not file.is_file():
                continue
            try:
                text = file.read_text(encoding="utf-8", errors="ignore")
                for lineno, line in enumerate(text.splitlines(), 1):
                    if compiled.search(line):
                        rel = file.relative_to(repo_root) if repo_root else file
                        results.append(f"{rel}:{lineno}: {line.rstrip()}")
            except Exception:
                continue

        if not results:
            return _ok("(no matches)")
        return _ok("\n".join(results))
    except re.error as e:
        return _err(f"Invalid regex pattern: {e}")
    except Exception as e:
        return _err(str(e))


def _resolve(path: str, repo_root: Path | None) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    if repo_root:
        return repo_root / p
    return Path(path)


def _is_read_only(path: str, read_only: list[str]) -> bool:
    """Check if path matches any read-only glob pattern."""
    path_norm = path.replace("\\", "/")
    for pattern in read_only:
        if fnmatch.fnmatch(path_norm, pattern):
            return True
        # Also try matching just the filename
        name = Path(path_norm).name
        if fnmatch.fnmatch(name, pattern):
            return True
    return False
