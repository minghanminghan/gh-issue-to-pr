"""Report step: write FAILURE.md, close trace, export OTLP."""

from __future__ import annotations

import sys
from pathlib import Path

from tools.state import read_state
from tools.trace import close_trace


def run_report(run_dir: Path, outcome: str) -> None:
    """
    Step 6 (post-flight, deterministic — no agent).

    - outcome == "pass": closes trace, writes TRACE.json, no FAILURE.md
    - outcome == "fail": writes FAILURE.md, closes trace, exits non-zero

    Always runs — even if earlier steps raised an exception.
    """
    run_dir = Path(run_dir)

    try:
        state = read_state(run_dir)
    except Exception:
        state = None

    # Close trace (writes TRACE.json)
    close_trace(run_dir, outcome)

    if outcome == "pass":
        return

    # Write FAILURE.md
    failure_source = getattr(state, "failure_source", None) if state else None
    last_reason = getattr(state, "last_failure_reason", None) if state else None
    current_step = getattr(state, "current_step", None) if state else None
    loop_count = getattr(state, "loop_count", 0) if state else 0

    failure_content = f"""# Pipeline Failure Report

failure_source: {failure_source or "unknown"}
last_failure_reason: {last_reason or "unknown"}
current_step: {current_step or "unknown"}
loop_count: {loop_count}
"""
    (run_dir / "FAILURE.md").write_text(failure_content, encoding="utf-8")
    sys.exit(1)
