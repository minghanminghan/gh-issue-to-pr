"""Test agent: run tests, append new tests, commit on success."""

from __future__ import annotations

from pathlib import Path

from agents.base import AgentResult, run_agent
from schemas.state import FailureSource, Step
from tools.fs import append_file, grep, read_file
from tools.manifests import TEST_MANIFEST, build_tools
from tools.shell import execute_cli
from tools.state import read_state, write_state

_SYSTEM_PROMPT = """\
You are the Test agent in an automated GitHub issue → pull request pipeline.

Your job:
1. Run the existing test suite using execute_cli.
2. If coverage gaps exist, APPEND new tests to existing test files (never overwrite).
3. Write TEST.md documenting the test results.
4. On success: commit all changes (source files from FILES.md + new test files).

## Rules

- You may ONLY append to test files — never overwrite them.
- Test deletions are NEVER permitted. If you find you need to delete a test,
  classify as `plan_invalid` and set failure_source accordingly in your output.
- Test edits (modifying existing tests) are only allowed if the issue explicitly
  changes a public interface. You must justify this in TEST.md BEFORE making edits.
- If the repo has no tests, write TEST.md noting their absence and commit without test changes.

## TEST.md format

```
# Test Report

## Test run results
- Command: `<test command>`
- Outcome: PASS / FAIL
- Output:
  <relevant output>

## New tests added
- <file>: <description of what was added>

## Commit
- SHA: <commit sha if committed>

## Overall result: PASS / FAIL
- Classification: <minor|spec_deviation|plan_invalid> (only if FAIL)
- Rationale: <reason>
```

## Commit on success

When all tests pass, run:
  git add <source files from FILES.md> <any appended test files>
  git commit -m "agent: <issue title> (#<issue number>)"

The commit message format is: `agent: <issue title> (#<issue number>)`.
You can find the issue title and number in ISSUE.md.
"""


_TEST_ALLOWLIST = [
    ("pytest", ""),
    ("python", "-m pytest"),
    ("python", "-m coverage"),
    ("python", "-m unittest"),
    ("npm", "test"),
    ("cargo", "test"),
    ("node", ""),
    ("git", "add"),
    ("git", "commit"),
    ("git", "status"),
]


def run_test_agent(run_dir: Path) -> AgentResult:
    run_dir = Path(run_dir)
    state = read_state(run_dir)
    repo_root = _repo_root(run_dir)

    container_id = state.container_id

    # Determine test files (for append-only enforcement)
    test_files = _find_test_files(repo_root)

    def _append_file(path: str, content: str):
        resolved = str((repo_root / path).resolve())
        # Only allow appending to existing test files
        if not any(resolved == str(tf.resolve()) for tf in test_files):
            # Check if it's a new test file being created (in a tests/ directory)
            if "test" not in path.lower():
                return {"ok": False, "output": "", "error": f"append_file is only for test files, not '{path}'"}
        return append_file(path, content, repo_root=repo_root, read_only=state.read_only)

    def _execute_cli_fn(cmd: str):
        return execute_cli(cmd, _TEST_ALLOWLIST, cwd=repo_root, container_id=container_id)

    user_prompt = "\n".join([
        "## Files changed\n",
        _read_file_safe(run_dir / "FILES.md"),
        "\n## Validation results\n",
        _read_file_safe(run_dir / "VALIDATE.md"),
        "\n## Issue (for commit message)\n",
        _read_file_safe(run_dir / "ISSUE.md")[:500],
        "\n## Instructions\n",
        "Run the test suite, append any needed tests, then commit on success.",
        f"\nRepository root: {repo_root}",
        f"Run directory (write TEST.md here): {run_dir}",
    ])

    def _write_test_md(path: str, content: str):
        if Path(path).name == "TEST.md":
            p = run_dir / "TEST.md"
            p.write_text(content, encoding="utf-8")
            return {"ok": True, "output": f"Written: {p}", "error": None}
        return {"ok": False, "output": "", "error": "Test agent may only write TEST.md via write_file."}

    handlers = {
        "read_file": lambda path: read_file(path, repo_root=repo_root),
        "append_file": _append_file,
        "grep": lambda pattern, path, flags="": grep(pattern, path, repo_root=repo_root, flags=flags),
        "execute_cli": _execute_cli_fn,
        # Allow write_file only for TEST.md
        "write_file": _write_test_md,
    }

    # Add write_file to test manifest temporarily
    from tools.manifests import ToolManifest, build_tools as _build_tools
    from tools.manifests import _SCHEMAS
    test_manifest_with_write = ToolManifest(
        agent_name="test",
        tool_names=["read_file", "append_file", "execute_cli", "grep", "write_file"],
    )

    result = run_agent(
        agent_name="test",
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        tool_schemas=_build_tools(test_manifest_with_write),
        tool_handlers=handlers,
        run_dir=run_dir,
    )

    # Parse TEST.md for pass/fail
    test_md_path = run_dir / "TEST.md"
    if test_md_path.exists():
        test_content = test_md_path.read_text(encoding="utf-8")
        passed = _extract_overall_pass(test_content.lower())
        if not passed:
            classification = _extract_classification(test_content.lower())
            state = read_state(run_dir)
            state.failure_source = _map_classification(classification)
            state.last_failure_reason = _extract_rationale(test_content)
            state.current_step = Step.test
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
        else:
            # Extract commit SHA if present
            commit_sha = _extract_commit_sha(test_content)
            if commit_sha:
                state = read_state(run_dir)
                state.commit_sha = commit_sha
                write_state(run_dir, state)

    if result.ok:
        state = read_state(run_dir)
        state.current_step = Step.summary
        write_state(run_dir, state)

    return result


def _find_test_files(repo_root: Path) -> list[Path]:
    test_files = []
    for pattern in ("tests/**/*.py", "test_*.py", "**/test_*.py", "**/*_test.py"):
        test_files.extend(repo_root.glob(pattern))
    return test_files


def _extract_overall_pass(content: str) -> bool:
    import re
    m = re.search(r"overall result:\s*(pass|fail)", content, re.IGNORECASE)
    if m:
        return m.group(1).lower() == "pass"
    return "fail" not in content


def _extract_classification(content: str) -> str:
    import re
    m = re.search(r"classification:\s*(minor|spec_deviation|plan_invalid|unrecoverable)", content, re.IGNORECASE)
    return m.group(1).lower() if m else "minor"


def _extract_rationale(content: str) -> str:
    import re
    m = re.search(r"rationale:\s*(.+)", content)
    return m.group(1).strip() if m else "Tests failed"


def _extract_commit_sha(content: str) -> str | None:
    import re
    m = re.search(r"SHA:\s*([a-f0-9]{7,40})", content)
    return m.group(1) if m else None


def _map_classification(classification: str) -> FailureSource:
    mapping = {
        "minor": FailureSource.test,
        "spec_deviation": FailureSource.test,
        "plan_invalid": FailureSource.test,
        "unrecoverable": FailureSource.unrecoverable,
    }
    return mapping.get(classification, FailureSource.test)


def _read_file_safe(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else f"({path.name} not found)"


def _repo_root(run_dir: Path) -> Path:
    return run_dir.parent.parent
