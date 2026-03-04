"""Execute agent: translate PLAN.md steps into code changes."""

from __future__ import annotations

from pathlib import Path

from agents.base import AgentResult, run_agent
from schemas.state import Step
from tools.context import build_context_block
from tools.fs import create_file, grep, read_file, write_file
from tools.manifests import EXECUTE_MANIFEST, build_tools
from tools.shell import DEFAULT_ALLOWLIST, execute_cli
from tools.state import read_state, write_state

_SYSTEM_PROMPT = """\
You are the Execute agent in an automated GitHub issue → pull request pipeline.

Your job:
1. Read PLAN.md and FILES.md.
2. Implement every step in PLAN.md by modifying the files listed in FILES.md.
3. Write CHANGES.md documenting every file change in this format:
   ```
   ### path/to/file.py
   - Step N: <justification for this change>
   ```

Rules:
- Only modify files listed in FILES.md. Writing to any other file will be rejected.
- Do NOT modify read-only files (README.md, CONTRIBUTING.md, LICENSE, .github/**, *.lock, .env*).
- Do NOT run tests in this step — only implement the plan.
- Your CHANGES.md must account for every file you modified.
- If this is a retry (spec_deviation), read VALIDATE.md to understand what was wrong.
"""


def run_execute_agent(
    run_dir: Path,
    context_block: str = "",
) -> AgentResult:
    run_dir = Path(run_dir)
    state = read_state(run_dir)
    repo_root = _repo_root(run_dir)

    # Load FILES.md to get allowed write targets
    files_md_path = run_dir / "FILES.md"
    files_md = files_md_path.read_text(encoding="utf-8") if files_md_path.exists() else ""
    allowed_files = _parse_files_md(files_md, repo_root)

    # Also allow writing CHANGES.md in run_dir
    allowed_run_files = {str(run_dir / "CHANGES.md")}

    user_parts = []
    if context_block:
        user_parts.append(context_block)

    user_parts += [
        "## Plan to implement\n",
        _read_file_safe(run_dir / "PLAN.md"),
        "\n## Files to modify\n",
        files_md or "(FILES.md not found)",
        "\n## Instructions\n",
        "Implement every step in the plan. Write CHANGES.md to document your changes.",
        f"\nRepository root: {repo_root}",
        f"Run directory (write CHANGES.md here): {run_dir}",
    ]
    user_prompt = "\n".join(user_parts)

    def _write_file(path: str, content: str):
        resolved = str(_resolve(path, repo_root))
        resolved_run = str(_resolve(path, run_dir))
        if resolved_run in allowed_run_files:
            return write_file(path, content, repo_root=run_dir, read_only=state.read_only)
        if resolved in allowed_files:
            return write_file(path, content, repo_root=repo_root, read_only=state.read_only)
        return {"ok": False, "output": "", "error": f"'{path}' is not in FILES.md. Only listed files may be modified."}

    def _create_file(path: str, content: str):
        resolved = str(_resolve(path, repo_root))
        if resolved in allowed_files:
            return create_file(path, content, repo_root=repo_root, read_only=state.read_only)
        return {"ok": False, "output": "", "error": f"'{path}' is not in FILES.md."}

    def _execute_cli(cmd: str):
        return execute_cli(cmd, DEFAULT_ALLOWLIST, cwd=repo_root)

    handlers = {
        "read_file": lambda path: read_file(path, repo_root=repo_root),
        "write_file": _write_file,
        "create_file": _create_file,
        "grep": lambda pattern, path, flags="": grep(pattern, path, repo_root=repo_root, flags=flags),
        "execute_cli": _execute_cli,
    }

    result = run_agent(
        agent_name="execute",
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        tool_schemas=build_tools(EXECUTE_MANIFEST),
        tool_handlers=handlers,
        run_dir=run_dir,
    )

    if result.ok:
        state = read_state(run_dir)
        state.current_step = Step.validate
        write_state(run_dir, state)

    return result


def _parse_files_md(files_md: str, repo_root: Path) -> set[str]:
    """Parse FILES.md lines like `- path/to/file.py: rationale` → set of resolved abs paths."""
    paths = set()
    for line in files_md.splitlines():
        line = line.strip()
        if line.startswith("- "):
            line = line[2:]
        if ":" in line:
            path_part = line.split(":")[0].strip()
        else:
            path_part = line.strip()
        if path_part:
            paths.add(str((repo_root / path_part).resolve()))
    return paths


def _resolve(path: str, root: Path) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    return (root / p).resolve()


def _read_file_safe(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else f"({path.name} not found)"


def _repo_root(run_dir: Path) -> Path:
    return run_dir.parent.parent
