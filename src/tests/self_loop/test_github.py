"""Tests for GitHub CLI helpers."""

import json
from unittest.mock import patch, MagicMock
import subprocess

from self_loop.github import list_open_issues, create_issue, wait_for_ci


def _mock_run(stdout="", returncode=0):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = ""
    return m


@patch("subprocess.run")
def test_list_open_issues_success(mock_run):
    issues = [{"number": 1, "title": "Fix bug", "url": "https://github.com/o/r/issues/1"}]
    mock_run.return_value = _mock_run(json.dumps(issues))
    result = list_open_issues("https://github.com/owner/repo")
    assert len(result) == 1
    assert result[0]["title"] == "Fix bug"


@patch("subprocess.run")
def test_list_open_issues_failure(mock_run):
    mock_run.return_value = _mock_run(returncode=1)
    result = list_open_issues("https://github.com/owner/repo")
    assert result == []


@patch("subprocess.run")
def test_list_open_issues_bad_json(mock_run):
    mock_run.return_value = _mock_run(stdout="bad json")
    result = list_open_issues("https://github.com/owner/repo")
    assert result == []


@patch("subprocess.run")
def test_create_issue_success(mock_run):
    mock_run.return_value = _mock_run("https://github.com/owner/repo/issues/5\n")
    url = create_issue("https://github.com/owner/repo", "Test title", "Test body")
    assert url == "https://github.com/owner/repo/issues/5"


@patch("subprocess.run")
def test_create_issue_failure(mock_run):
    mock_run.return_value = _mock_run(returncode=1)
    import pytest
    with pytest.raises(RuntimeError, match="gh issue create failed"):
        create_issue("https://github.com/owner/repo", "Test title", "Test body")


@patch("subprocess.run")
def test_wait_for_ci_pass(mock_run):
    mock_run.return_value = _mock_run(returncode=0)
    status = wait_for_ci("https://github.com/o/r/pull/1", "/repo")
    assert status == "pass"


@patch("subprocess.run")
def test_wait_for_ci_fail(mock_run):
    mock_run.return_value = _mock_run(returncode=1)
    status = wait_for_ci("https://github.com/o/r/pull/1", "/repo")
    assert status == "fail"
