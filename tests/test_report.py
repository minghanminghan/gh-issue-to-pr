"""Tests for the report step."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from schemas.state import FailureSource, PipelineState, Step
from tools.state import write_state


def _make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / ".agent" / "abc12345"
    run_dir.mkdir(parents=True)
    state = PipelineState(
        repo_url="https://github.com/x/y",
        local_dir=str(run_dir),
        issue_url="https://github.com/x/y/issues/1",
        current_step=Step.report,
        failure_source=FailureSource.exec,
        last_failure_reason="tests failed",
        loop_count=2,
    )
    write_state(run_dir, state)
    return run_dir


class TestRunReport:
    def test_pass_outcome_no_failure_md(self, tmp_path):
        run_dir = _make_run_dir(tmp_path)
        from tools.report import run_report
        # Pass outcome should not create FAILURE.md or sys.exit
        run_report(run_dir, "pass")
        assert not (run_dir / "FAILURE.md").exists()

    def test_pass_outcome_writes_trace_json(self, tmp_path):
        run_dir = _make_run_dir(tmp_path)
        from tools.report import run_report
        run_report(run_dir, "pass")
        assert (run_dir / "TRACE.json").exists()

    def test_fail_outcome_writes_failure_md(self, tmp_path):
        run_dir = _make_run_dir(tmp_path)
        from tools.report import run_report
        with pytest.raises(SystemExit) as exc_info:
            run_report(run_dir, "fail")
        assert exc_info.value.code == 1
        assert (run_dir / "FAILURE.md").exists()

    def test_failure_md_contains_required_fields(self, tmp_path):
        run_dir = _make_run_dir(tmp_path)
        from tools.report import run_report
        with pytest.raises(SystemExit):
            run_report(run_dir, "fail")
        content = (run_dir / "FAILURE.md").read_text()
        assert "failure_source" in content
        assert "last_failure_reason" in content
        assert "current_step" in content
        assert "loop_count" in content

    def test_trace_json_outcome_pass(self, tmp_path):
        run_dir = _make_run_dir(tmp_path)
        from tools.report import run_report
        run_report(run_dir, "pass")
        trace = json.loads((run_dir / "TRACE.json").read_text())
        assert trace["outcome"] == "pass"

    def test_trace_json_outcome_fail(self, tmp_path):
        run_dir = _make_run_dir(tmp_path)
        from tools.report import run_report
        with pytest.raises(SystemExit):
            run_report(run_dir, "fail")
        trace = json.loads((run_dir / "TRACE.json").read_text())
        assert trace["outcome"] == "fail"
