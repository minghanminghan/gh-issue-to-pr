"""Context injection for loop-back scenarios."""

from __future__ import annotations

from pathlib import Path

from schemas.state import FailureSource, PipelineState

# Approximate token cap for context injection block (1 token ≈ 4 chars)
_MAX_CHARS = 4000 * 4


def build_context_block(state: PipelineState, run_dir: Path) -> str:
    """
    Build a "Previous Attempt" block to prepend to an agent's user prompt
    on loop-back.

    Returns an empty string if loop_count == 0 and no failure is set.
    """
    if state.loop_count == 0 and state.failure_source is None:
        return ""

    artifact_content = _load_artifact(state, run_dir)

    block = f"""## Previous Attempt (loop {state.loop_count})
Failure source: {state.failure_source or "unknown"}
Failure reason: {state.last_failure_reason or "unknown"}

### Relevant artifacts
{artifact_content}
"""
    # Truncate to token budget (oldest content first = beginning of artifact)
    if len(block) > _MAX_CHARS:
        # Keep the header and truncate artifact
        header_end = block.index("### Relevant artifacts\n") + len("### Relevant artifacts\n")
        header = block[:header_end]
        available = _MAX_CHARS - len(header) - len("\n...(truncated)...")
        if available > 0:
            block = header + artifact_content[-available:] + "\n...(truncated)..."
        else:
            block = header + "...(truncated)..."

    return block


def _load_artifact(state: PipelineState, run_dir: Path) -> str:
    run_dir = Path(run_dir)

    # Determine which artifact to use based on failure source
    if state.failure_source in (FailureSource.validate, "minor", "spec_deviation"):
        path = run_dir / "VALIDATE.md"
    elif state.failure_source == FailureSource.test:
        path = run_dir / "TEST.md"
    elif state.failure_source == FailureSource.ci:
        path = run_dir / "SUMMARY.md"
    else:
        # Default: try VALIDATE.md, then TEST.md, then SUMMARY.md
        for name in ("VALIDATE.md", "TEST.md", "SUMMARY.md"):
            path = run_dir / name
            if path.exists():
                break

    if path.exists():
        return path.read_text(encoding="utf-8")
    return "(no artifact found)"
