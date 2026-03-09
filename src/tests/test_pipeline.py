import json
from unittest.mock import patch, MagicMock
from pathlib import Path
import pytest

from pipeline import _run_report, run_pipeline, _run_pipeline_steps, _DEFAULT_CI_RETRIES
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
            _run_report(issue, "fail", MagicMock())
        assert exc_info.value.code == 1


@patch("pipeline.run_setup")
@patch("pipeline._run_pipeline_steps", return_value=("pass", "submitted"))
@patch("pipeline._push_pr", return_value="https://github.com/owner/repo/pull/99")
@patch("pipeline._watch_ci", return_value=(True, ""))
@patch("pipeline._run_report")
def test_run_pipeline(mock_report, mock_watch, mock_push, mock_steps, mock_setup, tmp_path):
    issue = _make_issue(tmp_path)
    mock_setup.return_value = issue

    run_pipeline(
        issue_url="https://github.com/owner/repo/issues/1",
        guidelines_path=None,
        local_path=str(tmp_path),
        model_name=None,
        max_steps=None,
        cache=False,
    )

    mock_setup.assert_called_once_with(
        "https://github.com/owner/repo/issues/1", local_path=str(tmp_path)
    )
    mock_steps.assert_called_once()
    mock_report.assert_called_once()


@patch("pipeline.run_setup")
@patch("pipeline._run_pipeline_steps", return_value=("pass", "submitted"))
@patch("pipeline._push_pr", return_value="https://github.com/owner/repo/pull/99")
@patch("pipeline._watch_ci", return_value=(True, ""))
@patch("pipeline._run_report")
def test_run_pipeline_deletes_run_dir_by_default(
    mock_report, mock_watch, mock_push, mock_steps, mock_setup, tmp_path
):
    """Without --cache and no local_path, the run dir is deleted after push."""
    issue = _make_issue(tmp_path)
    run_dir = issue["dir"]
    assert run_dir.exists()
    mock_setup.return_value = issue

    run_pipeline(
        issue_url="https://github.com/owner/repo/issues/1",
        guidelines_path=None,
        local_path=None,
        model_name=None,
        max_steps=None,
        cache=False,
    )

    assert not run_dir.exists()


@patch("pipeline.run_setup")
@patch("pipeline._run_pipeline_steps", return_value=("pass", "submitted"))
@patch("pipeline._push_pr", return_value="https://github.com/owner/repo/pull/99")
@patch("pipeline._watch_ci", return_value=(True, ""))
@patch("pipeline._run_report")
def test_run_pipeline_cache_keeps_run_dir(
    mock_report, mock_watch, mock_push, mock_steps, mock_setup, tmp_path
):
    """With cache=True, the run dir is preserved after push."""
    issue = _make_issue(tmp_path)
    run_dir = issue["dir"]
    mock_setup.return_value = issue

    run_pipeline(
        issue_url="https://github.com/owner/repo/issues/1",
        guidelines_path=None,
        local_path=None,
        model_name=None,
        max_steps=None,
        cache=True,
    )

    assert run_dir.exists()


@patch("pipeline.run_setup")
@patch("pipeline._run_pipeline_steps", return_value=("pass", "submitted"))
@patch("pipeline._push_pr", return_value="https://github.com/owner/repo/pull/99")
@patch("pipeline._watch_ci", return_value=(True, ""))
@patch("pipeline._run_report")
def test_run_pipeline_local_path_never_deleted(
    mock_report, mock_watch, mock_push, mock_steps, mock_setup, tmp_path
):
    """When local_path is provided, the directory is never deleted regardless of cache."""
    issue = _make_issue(tmp_path)
    run_dir = issue["dir"]
    mock_setup.return_value = issue

    run_pipeline(
        issue_url="https://github.com/owner/repo/issues/1",
        guidelines_path=None,
        local_path=str(tmp_path),
        model_name=None,
        max_steps=None,
        cache=False,
    )

    assert run_dir.exists()


@patch("pipeline.run_setup")
@patch("pipeline._push_pr", return_value="https://github.com/owner/repo/pull/99")
@patch("pipeline._post_pr_comment")
@patch("pipeline._run_report")
def test_run_pipeline_ci_failure_retries_agent(
    mock_report, mock_comment, mock_push, mock_setup, tmp_path
):
    """CI failure on first attempt causes agent to retry with CI feedback."""
    issue = _make_issue(tmp_path)
    mock_setup.return_value = issue

    ci_details = "Tests failed: test_foo"
    steps_returns = [("pass", "submitted"), ("pass", "submitted")]
    ci_returns = [(False, ci_details), (True, "")]

    with patch("pipeline._run_pipeline_steps", side_effect=steps_returns) as mock_steps, \
         patch("pipeline._watch_ci", side_effect=ci_returns):
        run_pipeline(
            issue_url="https://github.com/owner/repo/issues/1",
            guidelines_path=None,
            local_path=str(tmp_path),
            model_name=None,
            max_steps=None,
        )

    assert mock_steps.call_count == 2
    # Second call must include the CI feedback
    _, kwargs = mock_steps.call_args_list[1]
    assert kwargs.get("ci_feedback") == ci_details or mock_steps.call_args_list[1][0][3] == ci_details


@patch("pipeline.run_setup")
@patch("pipeline._run_pipeline_steps", return_value=("pass", "submitted"))
@patch("pipeline._push_pr", return_value="https://github.com/owner/repo/pull/99")
@patch("pipeline._post_pr_comment")
@patch("pipeline._run_report")
def test_run_pipeline_ci_max_retries_stops(
    mock_report, mock_comment, mock_push, mock_steps, mock_setup, tmp_path
):
    """Agent runs at most _MAX_CI_RETRIES + 1 times when CI keeps failing."""
    issue = _make_issue(tmp_path)
    mock_setup.return_value = issue

    with patch("pipeline._watch_ci", return_value=(False, "logs")) as mock_watch:
        run_pipeline(
            issue_url="https://github.com/owner/repo/issues/1",
            guidelines_path=None,
            local_path=str(tmp_path),
            model_name=None,
            max_steps=None,
        )

    assert mock_steps.call_count == _DEFAULT_CI_RETRIES + 1
    assert mock_watch.call_count == _DEFAULT_CI_RETRIES + 1


@patch("pipeline.run_setup")
@patch("pipeline._run_pipeline_steps", return_value=("pass", "submitted"))
@patch("pipeline._push_pr", return_value="https://github.com/owner/repo/pull/99")
@patch("pipeline._post_pr_comment")
@patch("pipeline._run_report")
def test_run_pipeline_ci_retries_configurable(
    mock_report, mock_comment, mock_push, mock_steps, mock_setup, tmp_path
):
    """ci_retries parameter controls the number of CI retry attempts."""
    issue = _make_issue(tmp_path)
    mock_setup.return_value = issue
    custom_retries = 1

    with patch("pipeline._watch_ci", return_value=(False, "logs")) as mock_watch:
        run_pipeline(
            issue_url="https://github.com/owner/repo/issues/1",
            guidelines_path=None,
            local_path=str(tmp_path),
            model_name=None,
            max_steps=None,
            ci_retries=custom_retries,
        )

    assert mock_steps.call_count == custom_retries + 1
    assert mock_watch.call_count == custom_retries + 1


@patch("pipeline.run_setup")
@patch("pipeline._push_pr", return_value="https://github.com/owner/repo/pull/99")
@patch("pipeline._run_report")
def test_run_pipeline_agent_limits_stops_ci_retry(
    mock_report, mock_push, mock_setup, tmp_path
):
    """If agent hits limits on retry, no further CI attempts are made."""
    issue = _make_issue(tmp_path)
    mock_setup.return_value = issue

    steps_returns = [("pass", "submitted"), ("fail", "limits_exceeded")]
    ci_returns = [(False, "logs"), (True, "")]

    with patch("pipeline._run_pipeline_steps", side_effect=steps_returns) as mock_steps, \
         patch("pipeline._watch_ci", side_effect=ci_returns) as mock_watch, \
         patch("pipeline._post_pr_comment"):
        run_pipeline(
            issue_url="https://github.com/owner/repo/issues/1",
            guidelines_path=None,
            local_path=str(tmp_path),
            model_name=None,
            max_steps=None,
        )

    assert mock_steps.call_count == 2
    assert mock_watch.call_count == 1  # only one CI check before agent gave up


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
