"""Tests for context injection on loop-back."""

import tempfile
from pathlib import Path

import pytest

from schemas.state import FailureSource, PipelineState, Step
from tools.context import build_context_block, _MAX_CHARS


def _make_state(**kwargs) -> PipelineState:
    defaults = dict(
        repo_url="https://github.com/x/y",
        local_dir="/tmp/run",
        issue_url="https://github.com/x/y/issues/1",
    )
    defaults.update(kwargs)
    return PipelineState(**defaults)


class TestBuildContextBlock:
    def test_returns_empty_when_no_failure(self, tmp_path):
        state = _make_state()
        block = build_context_block(state, tmp_path)
        assert block == ""

    def test_returns_empty_when_loop_count_zero_no_failure(self, tmp_path):
        state = _make_state(loop_count=0, failure_source=None)
        block = build_context_block(state, tmp_path)
        assert block == ""

    def test_contains_loop_number(self, tmp_path):
        state = _make_state(
            loop_count=1,
            failure_source=FailureSource.exec,
            last_failure_reason="lint error",
        )
        block = build_context_block(state, tmp_path)
        assert "loop 1" in block

    def test_contains_failure_source(self, tmp_path):
        state = _make_state(
            loop_count=1,
            failure_source=FailureSource.exec,
            last_failure_reason="lint error",
        )
        block = build_context_block(state, tmp_path)
        assert "exec" in block

    def test_contains_failure_reason(self, tmp_path):
        state = _make_state(
            loop_count=1,
            failure_source=FailureSource.exec,
            last_failure_reason="ruff found 3 errors",
        )
        block = build_context_block(state, tmp_path)
        assert "ruff found 3 errors" in block

    def test_injects_validate_md_for_exec_failure(self, tmp_path):
        (tmp_path / "VALIDATE.md").write_text("## Validation\nFailed: ruff errors")
        state = _make_state(
            loop_count=1,
            failure_source=FailureSource.exec,
            last_failure_reason="minor",
        )
        block = build_context_block(state, tmp_path)
        assert "Failed: ruff errors" in block

    def test_injects_test_md_for_test_failure(self, tmp_path):
        (tmp_path / "TEST.md").write_text("## Tests\nFailed: 3 tests")
        state = _make_state(
            loop_count=1,
            failure_source=FailureSource.test,
            last_failure_reason="tests failed",
        )
        block = build_context_block(state, tmp_path)
        assert "Failed: 3 tests" in block

    def test_injects_summary_md_for_ci_failure(self, tmp_path):
        (tmp_path / "SUMMARY.md").write_text("## CI failed\nBuild error")
        state = _make_state(
            loop_count=1,
            failure_source=FailureSource.ci,
            last_failure_reason="CI failed",
        )
        block = build_context_block(state, tmp_path)
        assert "Build error" in block

    def test_truncates_at_limit(self, tmp_path):
        # Create a very large artifact
        large_content = "x" * (_MAX_CHARS * 10)
        (tmp_path / "VALIDATE.md").write_text(large_content)
        state = _make_state(
            loop_count=1,
            failure_source=FailureSource.exec,
            last_failure_reason="big failure",
        )
        block = build_context_block(state, tmp_path)
        assert len(block) <= _MAX_CHARS + 500  # some slack for header
        assert "truncated" in block

    def test_no_artifact_file_handled_gracefully(self, tmp_path):
        state = _make_state(
            loop_count=1,
            failure_source=FailureSource.exec,
            last_failure_reason="something",
        )
        block = build_context_block(state, tmp_path)
        assert "Previous Attempt" in block
        assert "no artifact found" in block
