"""Run trace: writes TRACE.json after each pipeline run."""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

from schema.config import AgentConfig
from tools.log import get_logger

log = get_logger(__name__)


def close_trace(
    run_dir: Path, outcome: str, issue_url: str, agent_config: AgentConfig
) -> None:
    """Write TRACE.json summarising the completed run."""
    run_dir = Path(run_dir)
    trace_json = {
        "run_id": run_dir.name,
        "issue_url": issue_url,
        "end_time": datetime.now(timezone.utc).isoformat(),
        "outcome": outcome,
        "human_feedback": None,
    }
    (run_dir / "TRACE.json").write_text(json.dumps(trace_json, indent=2))
    log.debug(f"TRACE.json written to {run_dir / 'TRACE.json'}")
