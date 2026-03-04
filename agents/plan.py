"""Plan agent: read issue + repo, produce PLAN.md and FILES.md."""

from __future__ import annotations

from pathlib import Path

from agents.base import AgentResult, run_agent
from schemas.state import Step
from tools.context import build_context_block
from tools.fs import append_file, grep, list_dir, read_file, write_file
from tools.manifests import PLAN_MANIFEST, build_tools
from tools.state import read_state, write_state

_SYSTEM_PROMPT = """\
You are the Plan agent in an automated GitHub issue → pull request pipeline.

Your job:
1. Read ISSUE.md to understand exactly what needs to be done.
2. Explore the repository to find all relevant files.
3. Write PLAN.md: a sequential list of concrete code changes, each with a
   runnable bash verification command (e.g. `ruff check .`, `pytest tests/`).
4. Write FILES.md: one line per file that will be modified, in the format:
   `- path/to/file.py: <one-line rationale>`

Rules:
- Write NO code in this step.
- Every step in PLAN.md must have a verification command.
- Only list files that MUST change to solve the issue — no speculative edits.
- If guidelines are provided, follow them strictly.
- If this is a loop-back (Previous Attempt block present), address the prior failures.
"""


def run_plan_agent(
    run_dir: Path,
    guidelines: str = "",
    context_block: str = "",
) -> AgentResult:
    run_dir = Path(run_dir)
    state = read_state(run_dir)

    # Load ISSUE.md
    issue_path = run_dir / "ISSUE.md"
    issue_text = issue_path.read_text(encoding="utf-8") if issue_path.exists() else "(ISSUE.md not found)"

    repo_root = _repo_root(run_dir)

    # Build system prompt with optional guidelines
    system = _SYSTEM_PROMPT
    if guidelines:
        system += f"\n\n## Repository guidelines\n\n{guidelines}"

    # Build user prompt
    user_parts = []
    if context_block:
        user_parts.append(context_block)

    user_parts += [
        "## Issue to implement\n",
        issue_text,
        "\n## Your tasks\n",
        "1. Explore the repository structure and relevant files.",
        "2. Write PLAN.md with sequential, actionable steps and verification commands.",
        "3. Write FILES.md listing every file that must be changed.",
        "",
        f"Repository root is at: {repo_root}",
        f"Run directory (write PLAN.md and FILES.md here): {run_dir}",
    ]
    user_prompt = "\n".join(user_parts)

    # Build tool handlers (plan agent: only PLAN.md and FILES.md writable)
    allowed_write_paths = {
        str(run_dir / "PLAN.md"),
        str(run_dir / "FILES.md"),
    }

    def _write_file(path: str, content: str):
        # Resolve the path
        resolved = str((repo_root / path).resolve()) if not Path(path).is_absolute() else path
        resolved_run = str((run_dir / path).resolve()) if not Path(path).is_absolute() else path
        # Allow writing to run_dir/PLAN.md and run_dir/FILES.md
        if resolved_run in allowed_write_paths or resolved in allowed_write_paths:
            return write_file(path, content, repo_root=run_dir, read_only=state.read_only)
        return {"ok": False, "output": "", "error": f"Plan agent may only write PLAN.md and FILES.md, not '{path}'"}

    handlers = {
        "list_dir": lambda path: list_dir(path, repo_root=repo_root),
        "read_file": lambda path: read_file(path, repo_root=repo_root),
        "write_file": _write_file,
        "grep": lambda pattern, path, flags="": grep(pattern, path, repo_root=repo_root, flags=flags),
    }

    result = run_agent(
        agent_name="plan",
        system_prompt=system,
        user_prompt=user_prompt,
        tool_schemas=build_tools(PLAN_MANIFEST),
        tool_handlers=handlers,
        run_dir=run_dir,
    )

    if result.ok:
        state = read_state(run_dir)
        state.current_step = Step.execute
        state.plan_version += 1
        write_state(run_dir, state)

    return result


def _repo_root(run_dir: Path) -> Path:
    # run_dir = repo_root/.agent/<hash>/
    return run_dir.parent.parent
