"""Tests for branch management."""

from unittest.mock import patch, MagicMock, call

from self_loop.branch import auto_merge_pr, commit_state_to_branch


def _mock_run(stdout="", returncode=0, stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


@patch("subprocess.run")
def test_auto_merge_pr_success(mock_run):
    mock_run.return_value = _mock_run(returncode=0)
    result = auto_merge_pr("https://github.com/o/r/pull/1", "/repo")
    assert result is True


@patch("subprocess.run")
def test_auto_merge_pr_failure(mock_run):
    mock_run.return_value = _mock_run(returncode=1, stderr="error")
    result = auto_merge_pr("https://github.com/o/r/pull/1", "/repo")
    assert result is False


@patch("subprocess.run")
def test_commit_state_no_changes(mock_run):
    # git add succeeds, git diff --cached returns 0 (no changes)
    mock_run.side_effect = [
        _mock_run(returncode=0),  # git add
        _mock_run(returncode=0),  # git diff --cached (no changes = rc 0)
    ]
    # Should return early without committing
    commit_state_to_branch("/repo", "self-loop/STATE.json")
    assert mock_run.call_count == 2


@patch("subprocess.run")
def test_commit_state_with_changes(mock_run):
    mock_run.side_effect = [
        _mock_run(returncode=0),  # git add
        _mock_run(returncode=1),  # git diff --cached (has changes = rc 1)
        _mock_run(returncode=0),  # git commit
        _mock_run(returncode=0),  # git push
    ]
    commit_state_to_branch("/repo", "self-loop/STATE.json")
    assert mock_run.call_count == 4
