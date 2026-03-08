"""LoopState persistence: read/write STATE.json."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from self_loop.schema.loop_state import IterationRecord, LoopState
from tools.log import get_logger

log = get_logger(__name__)

_DEFAULT_STATE: LoopState = {
    "total_iterations": 0,
    "total_cost_usd": 0.0,
    "seen_fingerprints": [],
    "iterations": [],
    "termination_reason": None,
}


def load_state(state_file: str) -> LoopState:
    path = Path(state_file)
    if not path.exists():
        log.debug(f"No state file at {path}; starting fresh")
        return dict(_DEFAULT_STATE)  # type: ignore[return-value]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        log.debug(f"Loaded state from {path}: {data['total_iterations']} iterations")
        return data
    except Exception as e:
        log.warning(f"Could not parse state file {path}: {e}; starting fresh")
        return dict(_DEFAULT_STATE)  # type: ignore[return-value]


def save_state(state: LoopState, state_file: str) -> None:
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    log.debug(f"State saved to {path}")


def record_iteration(
    state: LoopState,
    iteration: int,
    issue_url: str | None,
    pr_url: str | None,
    outcome: str,
    reason: str,
    cost_usd: float,
    fingerprint: str | None = None,
) -> None:
    record: IterationRecord = {
        "iteration": iteration,
        "issue_url": issue_url,
        "pr_url": pr_url,
        "outcome": outcome,
        "reason": reason,
        "cost_usd": cost_usd,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    state["iterations"].append(record)
    state["total_iterations"] = iteration
    state["total_cost_usd"] = round(state["total_cost_usd"] + cost_usd, 6)
    if fingerprint and fingerprint not in state["seen_fingerprints"]:
        state["seen_fingerprints"].append(fingerprint)
