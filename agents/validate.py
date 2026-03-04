"""Validate agent: run verification steps and classify failures."""

from __future__ import annotations

from pathlib import Path

from agents.base import AgentResult, run_agent
from schemas.state import FailureSource, Step
from tools.fs import grep, read_file, write_file
from tools.manifests import VALIDATE_MANIFEST, build_tools
from tools.shell import execute_cli
from tools.state import read_state, write_state

_SYSTEM_PROMPT = """\
You are the Validate agent in an automated GitHub issue → pull request pipeline.

Your job:
1. Read PLAN.md to get the verification commands for each step.
2. Run each verification command using execute_cli.
3. Check that CHANGES.md matches the PLAN.md spec (code does what plan describes).
4. Write VALIDATE.md documenting pass/fail for each step with evidence.
5. Set the failure classification in STATE.json if any step failed.

## Failure classification taxonomy

- `minor`: lint error, compile error, trivial logic mistake → retry execute
- `spec_deviation`: code doesn't match PLAN.md spec → retry execute with VALIDATE.md
- `plan_invalid`: plan is fundamentally wrong / contradicted by results → re-plan
- `unrecoverable`: cannot determine how to proceed

## VALIDATE.md format

```
# Validation Report

## Step N: <step title>
- Command: `<verification command>`
- Result: PASS / FAIL
- Evidence: <relevant output lines>

## Overall result: PASS / FAIL
- Classification: <minor|spec_deviation|plan_invalid> (only if FAIL)
- Rationale: <why this classification>
```

Rules:
- Run EVERY verification command from PLAN.md — do not skip any.
- You may ONLY write VALIDATE.md (in the run dir). No other writes.
- Be conservative with classification: prefer `minor` over `spec_deviation`,
  and `spec_deviation` over `plan_invalid`.
"""


def run_validate_agent(run_dir: Path) -> AgentResult:
    run_dir = Path(run_dir)
    state = read_state(run_dir)
    repo_root = _repo_root(run_dir)

    container_id = state.container_id

    # Determine allowlist: only commands from PLAN.md verification steps
    plan_commands = _extract_verification_commands(run_dir / "PLAN.md")
    allowlist = _build_allowlist(plan_commands)

    user_prompt = "\n".join([
        "## Plan (with verification commands)\n",
        _read_file_safe(run_dir / "PLAN.md"),
        "\n## Changes made\n",
        _read_file_safe(run_dir / "CHANGES.md"),
        "\n## Instructions\n",
        "Run each verification command, check the code matches the spec, and write VALIDATE.md.",
        f"\nRepository root: {repo_root}",
        f"Run directory (write VALIDATE.md here): {run_dir}",
    ])

    def _write_file(path: str, content: str):
        # Only allow writing VALIDATE.md
        norm = Path(path).name
        if norm == "VALIDATE.md":
            return write_file("VALIDATE.md", content, repo_root=run_dir, read_only=[])
        return {"ok": False, "output": "", "error": "Validate agent may only write VALIDATE.md."}

    def _execute_cli_fn(cmd: str):
        return execute_cli(cmd, allowlist, cwd=repo_root, container_id=container_id)

    handlers = {
        "read_file": lambda path: read_file(path, repo_root=repo_root),
        "write_file": _write_file,
        "grep": lambda pattern, path, flags="": grep(pattern, path, repo_root=repo_root, flags=flags),
        "execute_cli": _execute_cli_fn,
    }

    result = run_agent(
        agent_name="validate",
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        tool_schemas=build_tools(VALIDATE_MANIFEST),
        tool_handlers=handlers,
        run_dir=run_dir,
    )

    # Read VALIDATE.md to determine pass/fail and classification
    validate_md_path = run_dir / "VALIDATE.md"
    if validate_md_path.exists():
        validate_content = validate_md_path.read_text(encoding="utf-8").lower()
        passed = _extract_overall_pass(validate_content)
        if not passed:
            classification = _extract_classification(validate_content)
            state = read_state(run_dir)
            state.failure_source = _map_classification(classification)
            state.last_failure_reason = _extract_rationale(
                validate_md_path.read_text(encoding="utf-8")
            )
            state.current_step = Step.execute
            write_state(run_dir, state)
            return AgentResult(
                ok=False,
                output=result.output,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                cost_usd=result.cost_usd,
                failure_source=str(state.failure_source),
                failure_reason=state.last_failure_reason,
            )

    if result.ok:
        state = read_state(run_dir)
        state.current_step = Step.test
        write_state(run_dir, state)

    return result


def _extract_verification_commands(plan_path: Path) -> list[str]:
    """Extract bash commands from PLAN.md (lines with backtick-wrapped commands)."""
    if not plan_path.exists():
        return []
    text = plan_path.read_text(encoding="utf-8")
    commands = []
    for line in text.splitlines():
        # Look for inline code blocks like `pytest tests/` or `ruff check .`
        import re
        matches = re.findall(r"`([^`]+)`", line)
        for m in matches:
            parts = m.strip().split()
            if parts and parts[0] in {"pytest", "ruff", "mypy", "python", "node", "cargo", "npm", "git", "gh"}:
                commands.append(m.strip())
    return commands


def _build_allowlist(commands: list[str]) -> list[tuple[str, str]]:
    """Build an allowlist from extracted commands."""
    import shlex
    allowlist = []
    for cmd in commands:
        try:
            parts = shlex.split(cmd)
            if parts:
                binary = parts[0]
                args = " ".join(parts[1:])
                allowlist.append((binary, args))
        except Exception:
            pass
    # Always allow git status and ruff check as fallback
    if not allowlist:
        allowlist = [
            ("ruff", "check"),
            ("ruff", "format"),
            ("pytest", ""),
            ("mypy", ""),
            ("python", "-m pytest"),
            ("python", "-m ruff"),
            ("python", "-m mypy"),
        ]
    return allowlist


def _extract_overall_pass(content: str) -> bool:
    import re
    m = re.search(r"overall result:\s*(pass|fail)", content, re.IGNORECASE)
    if m:
        return m.group(1).lower() == "pass"
    # Default: if "fail" appears in the document, it failed
    return "fail" not in content


def _extract_classification(content: str) -> str:
    import re
    m = re.search(r"classification:\s*(minor|spec_deviation|plan_invalid|unrecoverable)", content, re.IGNORECASE)
    return m.group(1).lower() if m else "minor"


def _extract_rationale(content: str) -> str:
    import re
    m = re.search(r"rationale:\s*(.+)", content)
    return m.group(1).strip() if m else "Validation failed"


def _map_classification(classification: str) -> FailureSource:
    mapping = {
        "minor": FailureSource.exec,
        "spec_deviation": FailureSource.exec,
        "plan_invalid": FailureSource.validate,
        "unrecoverable": FailureSource.unrecoverable,
    }
    return mapping.get(classification, FailureSource.exec)


def _read_file_safe(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else f"({path.name} not found)"


def _repo_root(run_dir: Path) -> Path:
    return run_dir.parent.parent
