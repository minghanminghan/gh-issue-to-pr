import json
from unittest.mock import patch, MagicMock
from pathlib import Path
import pytest

from pipeline import _run_report, run_pipeline, _run_pipeline_steps
from schema.issue import Issue


def _make_issue(tmp_path: Path) -> Issue:
    repo_dir = tmp_path / "run" / "abc12345"
    repo_dir.mkdir(parents=True)
    return Issue(
        url="https://github.com/owner/repo/issues/1",
        repo="https://github.com/owner/repo",
        dir=repo_dir,
        desc="# Issue #1: Fix the bug\n\n## Description\n\nfix the bug",
    )


class TestRunReport:
    def test_writes_trace_json(self, tmp_path):
        issue = _make_issue(tmp_path)
        _run_report(issue, "pass", MagicMock())
        assert (issue["dir"] / "TRACE.json").exists()

    def test_trace_json_outcome_pass(self, tmp_path):
        issue = _make_issue(tmp_path)
        _run_report(issue, "pass", MagicMock())
        trace = json.loads((issue["dir"] / "TRACE.json").read_text())
        assert trace["outcome"] == "pass"

    def test_exits_for_failure_outcome(self, tmp_path):
        issue = _make_issue(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            _run_report(issue, "failure", MagicMock())
        assert exc_info.value.code == 1


@patch("pipeline.run_setup")
@patch("pipeline._run_pipeline_steps")
@patch("pipeline._run_report")
def test_run_pipeline(mock_report, mock_steps, mock_setup, tmp_path):
    issue = _make_issue(tmp_path)
    mock_setup.return_value = issue
    mock_steps.return_value = "pass"

    run_pipeline(
        issue_url="https://github.com/owner/repo/issues/1",
        guidelines_path=None,
        local_path=str(tmp_path),
        model_name=None,
        max_steps=None,
    )

    mock_setup.assert_called_once_with(
        "https://github.com/owner/repo/issues/1", local_path=str(tmp_path)
    )
    mock_steps.assert_called_once()
    mock_report.assert_called_once()


@patch("pipeline.platform.system", return_value="Darwin")
@patch("pipeline.otel_trace")
@patch("pipeline.LocalEnvironment")
@patch("pipeline.LitellmModel")
@patch("pipeline.get_config_from_spec")
@patch("pipeline.DefaultAgent")
def test_run_pipeline_steps(
    mock_agent_cls, mock_get_config, mock_model, mock_env, mock_otel, mock_platform, tmp_path
):
    issue = _make_issue(tmp_path)

    mock_get_config.return_value = {"agent": {}}
    mock_agent = MagicMock()
    mock_agent_cls.return_value = mock_agent

    mock_span = MagicMock()
    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
    mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
    mock_otel.get_tracer.return_value = mock_tracer
    mock_agent.run.return_value = {"exit_status": "Submitted"}

    result = _run_pipeline_steps(
        issue, guidelines="be nice", agent_config={"model_name": None, "max_steps": None}
    )

    assert result == ("pass", "submitted")
    mock_agent.run.assert_called_once()
    prompt = mock_agent.run.call_args[0][0]
    assert "fix the bug" in prompt
    assert "be nice" in prompt
