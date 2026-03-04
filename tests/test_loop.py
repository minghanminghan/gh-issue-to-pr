"""Tests for loop controller: global loop, local loop, failure classification routing."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.base import AgentResult
from schemas.state import FailureSource, Step
from tools.state import init_run, read_state, write_state


def _make_ok_result(**kwargs) -> AgentResult:
    defaults = dict(ok=True, output="done", tokens_in=100, tokens_out=50, cost_usd=0.001)
    defaults.update(kwargs)
    return AgentResult(**defaults)


def _make_fail_result(failure_source="exec", failure_reason="error") -> AgentResult:
    return AgentResult(
        ok=False, output="", tokens_in=10, tokens_out=5, cost_usd=0.0001,
        failure_source=failure_source, failure_reason=failure_reason,
    )


def _setup_run_dir(tmp_path: Path) -> tuple[Path, Path]:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    issue_url = "https://github.com/x/y/issues/1"
    run_dir = init_run("https://github.com/x/y", issue_url, repo_root)
    state = read_state(run_dir)
    state.branch_name = "agent/abc12345"
    state.current_step = Step.plan
    write_state(run_dir, state)
    (run_dir / "ISSUE.md").write_text("# Issue")
    return repo_root, run_dir


class TestLoopController:
    @patch("pipeline.run_setup")
    @patch("pipeline.run_plan_agent")
    @patch("pipeline.run_execute_agent")
    @patch("pipeline.run_validate_agent")
    @patch("pipeline.run_test_agent")
    @patch("pipeline.run_summary_agent")
    @patch("pipeline.run_report")
    def test_global_loop_count_increments_on_plan_invalid(
        self, mock_report, mock_summary, mock_test,
        mock_validate, mock_execute, mock_plan, mock_setup, tmp_path
    ):
        _, run_dir = _setup_run_dir(tmp_path)
        mock_setup.return_value = run_dir

        call_count = [0]

        def plan_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                # Simulate validate failing with plan_invalid
                state = read_state(run_dir)
                state.failure_source = FailureSource.validate
                state.last_failure_reason = "plan is wrong"
                state.loop_count = call_count[0]
                write_state(run_dir, state)
                return _make_ok_result()
            return _make_ok_result()

        mock_plan.side_effect = plan_side_effect
        mock_execute.return_value = _make_ok_result()

        def validate_side_effect(*args, **kwargs):
            state = read_state(run_dir)
            if state.loop_count < 2:
                state.failure_source = FailureSource.validate
                state.last_failure_reason = "plan_invalid"
                write_state(run_dir, state)
                return _make_fail_result("validate", "plan_invalid")
            return _make_ok_result()

        mock_validate.side_effect = validate_side_effect
        mock_test.return_value = _make_ok_result()
        mock_summary.return_value = _make_ok_result()

        from pipeline import run_pipeline
        run_pipeline("https://github.com/x/y", "https://github.com/x/y/issues/1")

        # Plan was called multiple times due to loop-backs
        assert mock_plan.call_count >= 2

    @patch("pipeline.run_setup")
    @patch("pipeline.run_plan_agent")
    @patch("pipeline.run_execute_agent")
    @patch("pipeline.run_validate_agent")
    @patch("pipeline.run_test_agent")
    @patch("pipeline.run_summary_agent")
    @patch("pipeline.run_report")
    def test_max_global_loops_routes_to_report_fail(
        self, mock_report, mock_summary, mock_test,
        mock_validate, mock_execute, mock_plan, mock_setup, tmp_path
    ):
        _, run_dir = _setup_run_dir(tmp_path)
        mock_setup.return_value = run_dir

        def plan_side_effect(*args, **kwargs):
            state = read_state(run_dir)
            state.failure_source = FailureSource.validate
            state.last_failure_reason = "always invalid"
            write_state(run_dir, state)
            return _make_ok_result()

        mock_plan.side_effect = plan_side_effect
        mock_execute.return_value = _make_ok_result()
        mock_validate.return_value = _make_fail_result("validate", "plan_invalid")
        mock_test.return_value = _make_ok_result()
        mock_summary.return_value = _make_ok_result()

        from pipeline import run_pipeline
        run_pipeline("https://github.com/x/y", "https://github.com/x/y/issues/1")

        mock_report.assert_called_once()
        outcome = mock_report.call_args[0][1]
        assert outcome == "fail"

    @patch("pipeline.run_setup")
    @patch("pipeline.run_plan_agent")
    @patch("pipeline.run_execute_agent")
    @patch("pipeline.run_validate_agent")
    @patch("pipeline.run_test_agent")
    @patch("pipeline.run_summary_agent")
    @patch("pipeline.run_report")
    def test_local_loop_count_resets_on_global_loop(
        self, mock_report, mock_summary, mock_test,
        mock_validate, mock_execute, mock_plan, mock_setup, tmp_path
    ):
        _, run_dir = _setup_run_dir(tmp_path)
        mock_setup.return_value = run_dir

        global_loop_calls = [0]

        def plan_side_effect(*args, **kwargs):
            global_loop_calls[0] += 1
            return _make_ok_result()

        mock_plan.side_effect = plan_side_effect
        mock_execute.return_value = _make_ok_result()
        # Validate fails once with plan_invalid, then passes
        validate_calls = [0]

        def validate_side_effect(*args, **kwargs):
            validate_calls[0] += 1
            state = read_state(run_dir)
            if state.loop_count == 0:
                state.failure_source = FailureSource.validate
                state.last_failure_reason = "plan invalid"
                state.local_loop_count = 0
                state.loop_count = 1
                write_state(run_dir, state)
                return _make_fail_result("validate", "plan_invalid")
            return _make_ok_result()

        mock_validate.side_effect = validate_side_effect
        mock_test.return_value = _make_ok_result()
        mock_summary.return_value = _make_ok_result()

        from pipeline import run_pipeline
        run_pipeline("https://github.com/x/y", "https://github.com/x/y/issues/1")

        # After global loop-back, local_loop_count should be 0
        state = read_state(run_dir)
        assert state.local_loop_count == 0
