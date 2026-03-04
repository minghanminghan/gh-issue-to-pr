"""Tests for pipeline orchestration with stubbed agents."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agents.base import AgentResult
from schemas.state import FailureSource, PipelineState, Step
from tools.state import init_run, read_state, write_state


def _make_ok_result(**kwargs) -> AgentResult:
    defaults = dict(ok=True, output="done", tokens_in=100, tokens_out=50, cost_usd=0.001)
    defaults.update(kwargs)
    return AgentResult(**defaults)


def _make_fail_result(failure_source="exec", failure_reason="error", **kwargs) -> AgentResult:
    return AgentResult(
        ok=False,
        output="",
        tokens_in=10,
        tokens_out=5,
        cost_usd=0.0001,
        failure_source=failure_source,
        failure_reason=failure_reason,
        **kwargs,
    )


def _setup_run_dir(tmp_path: Path) -> tuple[Path, Path]:
    """Create a minimal repo + run_dir for testing."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    issue_url = "https://github.com/x/y/issues/1"
    run_dir = init_run("https://github.com/x/y", issue_url, repo_root)
    state = read_state(run_dir)
    state.branch_name = "agent/abc12345"
    state.issue_body = "Fix the bug"
    state.current_step = Step.plan
    write_state(run_dir, state)
    # Write minimal artifacts
    (run_dir / "ISSUE.md").write_text("# Issue #1: Fix bug\nFix the bug")
    return repo_root, run_dir


class TestPipelineStubs:
    """Pipeline logic tests using stubbed agent functions."""

    @patch("pipeline.run_setup")
    @patch("pipeline.run_plan_agent")
    @patch("pipeline.run_execute_agent")
    @patch("pipeline.run_validate_agent")
    @patch("pipeline.run_test_agent")
    @patch("pipeline.run_summary_agent")
    @patch("pipeline.run_report")
    def test_all_pass_runs_to_completion(
        self, mock_report, mock_summary, mock_test,
        mock_validate, mock_execute, mock_plan, mock_setup, tmp_path
    ):
        _, run_dir = _setup_run_dir(tmp_path)
        mock_setup.return_value = run_dir

        # All agents pass
        mock_plan.return_value = _make_ok_result()
        mock_execute.return_value = _make_ok_result()
        mock_validate.return_value = _make_ok_result()
        mock_test.return_value = _make_ok_result()
        mock_summary.return_value = _make_ok_result()

        from pipeline import run_pipeline
        run_pipeline("https://github.com/x/y", "https://github.com/x/y/issues/1")

        # Report must always be called
        mock_report.assert_called_once()
        # First arg to report is run_dir, second is outcome
        outcome = mock_report.call_args[0][1]
        assert outcome == "pass"

    @patch("pipeline.run_setup")
    @patch("pipeline.run_plan_agent")
    @patch("pipeline.run_execute_agent")
    @patch("pipeline.run_validate_agent")
    @patch("pipeline.run_test_agent")
    @patch("pipeline.run_summary_agent")
    @patch("pipeline.run_report")
    def test_report_runs_even_on_failure(
        self, mock_report, mock_summary, mock_test,
        mock_validate, mock_execute, mock_plan, mock_setup, tmp_path
    ):
        _, run_dir = _setup_run_dir(tmp_path)
        mock_setup.return_value = run_dir
        mock_plan.return_value = _make_fail_result(failure_source="unrecoverable")
        mock_execute.return_value = _make_ok_result()
        mock_validate.return_value = _make_ok_result()
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
    def test_budget_exceeded_routes_to_report(
        self, mock_report, mock_summary, mock_test,
        mock_validate, mock_execute, mock_plan, mock_setup, tmp_path
    ):
        _, run_dir = _setup_run_dir(tmp_path)
        mock_setup.return_value = run_dir

        # Exhaust budget before plan runs
        state = read_state(run_dir)
        state.cost_budget_usd = 0.0
        state.cost_spent_usd = 0.01
        write_state(run_dir, state)

        mock_plan.return_value = _make_ok_result()
        mock_execute.return_value = _make_ok_result()
        mock_validate.return_value = _make_ok_result()
        mock_test.return_value = _make_ok_result()
        mock_summary.return_value = _make_ok_result()

        from pipeline import run_pipeline
        run_pipeline("https://github.com/x/y", "https://github.com/x/y/issues/1")

        mock_report.assert_called_once()
        outcome = mock_report.call_args[0][1]
        assert outcome == "fail"
        state = read_state(run_dir)
        assert state.failure_source == FailureSource.budget_exceeded
