"""Structured JSON-line logger that writes to RUN.log in the run directory."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def log_tool_call(
    run_dir: Path,
    agent: str,
    tool: str,
    args_summary: str,
    ok: bool,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "tool": tool,
        "args_summary": args_summary,
        "ok": ok,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost_usd,
    }
    log_path = Path(run_dir) / "RUN.log"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def log_event(
    run_dir: Path,
    event: str,
    data: Optional[dict] = None,
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **(data or {}),
    }
    log_path = Path(run_dir) / "RUN.log"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
