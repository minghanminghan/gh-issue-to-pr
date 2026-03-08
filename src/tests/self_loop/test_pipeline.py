"""Tests for self-loop pipeline wrapper."""

from unittest.mock import patch


def _make_issue(dir_path=None):
    from pathlib import Path
    from schema.issue import Issue
    return Issue(
        url="https://github.com/owner/repo/issues/1",
        repo="https://github.com/owner/repo",
        dir=Path(dir_path or "/tmp/test-repo"),
        desc="# Issue #1: Test\n\nTest issue body",
    )


@patch("self_loop.pipeline._run_report")
@patch("self_loop.pipeline._run_pipeline_steps")
@patch("self_loop.pipeline.run_setup")
def test_run_self_loop_pipeline_pass(mock_setup, mock_steps, mock_report, tmp_path):
    mock_setup.return_value = _make_issue(str(tmp_path))
    mock_steps.return_value = ("pass", "submitted")

    with patch("self_loop.pipeline._push_self_loop_pr") as mock_push:
        mock_push.return_value = "https://github.com/owner/repo/pull/5"
        from self_loop.pipeline import run_self_loop_pipeline
        outcome, reason, pr_url = run_self_loop_pipeline(
            issue_url="https://github.com/owner/repo/issues/1",
            repo_local_path=str(tmp_path),
            config={
                "fix_model": "test-model",
                "per_run_max_steps": 10,
                "per_run_budget_usd": 1.0,
                "self_loop_branch": "self-loop",
                "guidelines_path": None,
            },
        )
    assert outcome == "pass"
    assert pr_url == "https://github.com/owner/repo/pull/5"


@patch("self_loop.pipeline._run_report")
@patch("self_loop.pipeline._run_pipeline_steps")
@patch("self_loop.pipeline.run_setup")
def test_run_self_loop_pipeline_fail(mock_setup, mock_steps, mock_report, tmp_path):
    mock_setup.return_value = _make_issue(str(tmp_path))
    mock_steps.return_value = ("fail", "limits_exceeded")

    from self_loop.pipeline import run_self_loop_pipeline
    outcome, reason, pr_url = run_self_loop_pipeline(
        issue_url="https://github.com/owner/repo/issues/1",
        repo_local_path=str(tmp_path),
        config={
            "fix_model": "test-model",
            "per_run_max_steps": 10,
            "per_run_budget_usd": 1.0,
            "self_loop_branch": "self-loop",
            "guidelines_path": None,
        },
    )
    assert outcome == "fail"
    assert pr_url is None
