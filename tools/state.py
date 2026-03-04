from __future__ import annotations

import hashlib
import json
from pathlib import Path

from schemas.state import DEFAULT_READ_ONLY, PipelineState, Step


def _run_hash(issue_url: str) -> str:
    return hashlib.sha256(issue_url.encode()).hexdigest()[:8]


def read_state(run_dir: Path) -> PipelineState:
    state_path = Path(run_dir) / "STATE.json"
    with open(state_path) as f:
        data = json.load(f)
    return PipelineState.model_validate(data)


def write_state(run_dir: Path, state: PipelineState) -> None:
    state_path = Path(run_dir) / "STATE.json"
    state_path.write_text(state.model_dump_json(indent=2))


def init_run(repo_url: str, issue_url: str, repo_root: Path) -> Path:
    """Create run directory, initialise STATE.json and add .agent/ to .gitignore."""
    repo_root = Path(repo_root)
    run_hash = _run_hash(issue_url)
    agent_dir = repo_root / ".agent"
    run_dir = agent_dir / run_hash

    run_dir.mkdir(parents=True, exist_ok=True)

    # Add .agent/ to .gitignore if not present
    gitignore_path = repo_root / ".gitignore"
    _ensure_gitignore_entry(gitignore_path, ".agent/")

    # Write initial STATE.json if it doesn't exist
    state_path = run_dir / "STATE.json"
    if not state_path.exists():
        state = PipelineState(
            repo_url=repo_url,
            local_dir=str(run_dir.resolve()),
            issue_url=issue_url,
            current_step=Step.setup,
            read_only=list(DEFAULT_READ_ONLY),
        )
        write_state(run_dir, state)

    return run_dir


def _ensure_gitignore_entry(gitignore_path: Path, entry: str) -> None:
    if gitignore_path.exists():
        lines = gitignore_path.read_text().splitlines()
        if entry in lines or entry.rstrip("/") in lines:
            return
        with open(gitignore_path, "a") as f:
            f.write(f"\n{entry}\n")
    else:
        gitignore_path.write_text(f"{entry}\n")
