"""Integration-level tests for the self-loop orchestrator (heavily mocked)."""

from unittest.mock import patch


from self_loop.schema.loop_config import SelfLoopConfig
from self_loop.schema.scan_result import IssueCandidate, ScanResult


def _make_config(**kwargs) -> SelfLoopConfig:
    defaults: SelfLoopConfig = {
        "repo_local_path": "/repo",
        "repo_github_url": "https://github.com/owner/repo",
        "self_loop_branch": "self-loop",
        "max_iterations": 3,
        "max_total_budget_usd": 30.0,
        "per_run_budget_usd": 3.0,
        "per_run_max_steps": 10,
        "scanner_model": "test-scanner",
        "fix_model": "test-fix",
        "state_file": "/tmp/test-state.json",
        "dry_run": False,
        "min_issue_priority": "low",
        "guidelines_path": None,
    }
    defaults.update(kwargs)
    return defaults


def _make_candidate(priority="high") -> IssueCandidate:
    return IssueCandidate(
        title="Fix error handling in pipeline",
        body="## Problem\n...",
        category="error_handling",
        priority=priority,
        affected_files=["src/pipeline.py"],
        fingerprint="abc123def456",
        evidence="src/pipeline.py:50: bare except",
    )


def _make_scan_result(candidates=None) -> ScanResult:
    return ScanResult(
        candidates=candidates or [_make_candidate()],
        scan_cost_usd=0.1,
        scan_duration_s=5.0,
    )


@patch("self_loop.loop.commit_state_to_branch")
@patch("self_loop.loop.auto_merge_pr")
@patch("self_loop.loop.wait_for_ci")
@patch("self_loop.loop.run_self_loop_pipeline")
@patch("self_loop.loop.create_issue")
@patch("self_loop.loop.filter_candidates")
@patch("self_loop.loop.scan_codebase")
@patch("self_loop.loop.list_open_issues")
@patch("self_loop.loop.load_state")
@patch("self_loop.loop.save_state")
@patch("self_loop.loop.sync_self_loop_branch")
@patch("self_loop.loop.ensure_self_loop_branch")
@patch("self_loop.loop._sanity_check")
def test_dry_run_terminates(
    mock_sanity, mock_ensure, mock_sync, mock_save, mock_load,
    mock_list_issues, mock_scan, mock_filter, mock_create, mock_pipeline,
    mock_wait_ci, mock_merge, mock_commit,
):
    mock_sanity.return_value = True
    mock_load.return_value = {
        "total_iterations": 0,
        "total_cost_usd": 0.0,
        "seen_fingerprints": [],
        "iterations": [],
        "termination_reason": None,
    }
    mock_list_issues.return_value = []
    mock_scan.return_value = _make_scan_result()
    mock_filter.return_value = [_make_candidate()]

    from self_loop.loop import self_loop_run
    config = _make_config(dry_run=True, max_iterations=1)
    reason = self_loop_run(config)

    assert reason == "dry_run_complete"
    mock_create.assert_not_called()
    mock_pipeline.assert_not_called()


@patch("self_loop.loop.commit_state_to_branch")
@patch("self_loop.loop.auto_merge_pr")
@patch("self_loop.loop.wait_for_ci")
@patch("self_loop.loop.run_self_loop_pipeline")
@patch("self_loop.loop.create_issue")
@patch("self_loop.loop.filter_candidates")
@patch("self_loop.loop.scan_codebase")
@patch("self_loop.loop.list_open_issues")
@patch("self_loop.loop.load_state")
@patch("self_loop.loop.save_state")
@patch("self_loop.loop.sync_self_loop_branch")
@patch("self_loop.loop.ensure_self_loop_branch")
@patch("self_loop.loop._sanity_check")
def test_no_candidates_terminates_after_threshold(
    mock_sanity, mock_ensure, mock_sync, mock_save, mock_load,
    mock_list_issues, mock_scan, mock_filter, mock_create, mock_pipeline,
    mock_wait_ci, mock_merge, mock_commit,
):
    mock_sanity.return_value = True
    mock_load.return_value = {
        "total_iterations": 0,
        "total_cost_usd": 0.0,
        "seen_fingerprints": [],
        "iterations": [],
        "termination_reason": None,
    }
    mock_list_issues.return_value = []
    mock_scan.return_value = _make_scan_result(candidates=[])
    mock_filter.return_value = []

    from self_loop.loop import self_loop_run
    config = _make_config(max_iterations=10)
    reason = self_loop_run(config)

    assert reason == "no_candidates"
    mock_create.assert_not_called()


@patch("self_loop.loop.commit_state_to_branch")
@patch("self_loop.loop.auto_merge_pr")
@patch("self_loop.loop.wait_for_ci")
@patch("self_loop.loop.run_self_loop_pipeline")
@patch("self_loop.loop.create_issue")
@patch("self_loop.loop.filter_candidates")
@patch("self_loop.loop.scan_codebase")
@patch("self_loop.loop.list_open_issues")
@patch("self_loop.loop.load_state")
@patch("self_loop.loop.save_state")
@patch("self_loop.loop.sync_self_loop_branch")
@patch("self_loop.loop.ensure_self_loop_branch")
@patch("self_loop.loop._sanity_check")
def test_budget_exhausted_terminates(
    mock_sanity, mock_ensure, mock_sync, mock_save, mock_load,
    mock_list_issues, mock_scan, mock_filter, mock_create, mock_pipeline,
    mock_wait_ci, mock_merge, mock_commit,
):
    mock_sanity.return_value = True
    mock_load.return_value = {
        "total_iterations": 0,
        "total_cost_usd": 28.0,  # Only 2.0 left, per_run is 3.0
        "seen_fingerprints": [],
        "iterations": [],
        "termination_reason": None,
    }
    mock_list_issues.return_value = []

    from self_loop.loop import self_loop_run
    config = _make_config(max_total_budget_usd=30.0, per_run_budget_usd=3.0)
    reason = self_loop_run(config)

    assert reason == "budget_exhausted"
    mock_scan.assert_not_called()


@patch("self_loop.loop.commit_state_to_branch")
@patch("self_loop.loop.auto_merge_pr")
@patch("self_loop.loop.wait_for_ci")
@patch("self_loop.loop.run_self_loop_pipeline")
@patch("self_loop.loop.create_issue")
@patch("self_loop.loop.filter_candidates")
@patch("self_loop.loop.scan_codebase")
@patch("self_loop.loop.list_open_issues")
@patch("self_loop.loop.load_state")
@patch("self_loop.loop.save_state")
@patch("self_loop.loop.sync_self_loop_branch")
@patch("self_loop.loop.ensure_self_loop_branch")
@patch("self_loop.loop._sanity_check")
def test_sanity_check_failure_terminates(
    mock_sanity, mock_ensure, mock_sync, mock_save, mock_load,
    mock_list_issues, mock_scan, mock_filter, mock_create, mock_pipeline,
    mock_wait_ci, mock_merge, mock_commit,
):
    mock_sanity.return_value = False
    mock_load.return_value = {
        "total_iterations": 0,
        "total_cost_usd": 0.0,
        "seen_fingerprints": [],
        "iterations": [],
        "termination_reason": None,
    }

    from self_loop.loop import self_loop_run
    config = _make_config()
    reason = self_loop_run(config)

    assert reason == "codebase_broken"
    mock_scan.assert_not_called()


@patch("self_loop.loop.commit_state_to_branch")
@patch("self_loop.loop.auto_merge_pr")
@patch("self_loop.loop.wait_for_ci")
@patch("self_loop.loop.run_self_loop_pipeline")
@patch("self_loop.loop.create_issue")
@patch("self_loop.loop.filter_candidates")
@patch("self_loop.loop.scan_codebase")
@patch("self_loop.loop.list_open_issues")
@patch("self_loop.loop.load_state")
@patch("self_loop.loop.save_state")
@patch("self_loop.loop.sync_self_loop_branch")
@patch("self_loop.loop.ensure_self_loop_branch")
@patch("self_loop.loop._sanity_check")
def test_successful_iteration_merges_pr(
    mock_sanity, mock_ensure, mock_sync, mock_save, mock_load,
    mock_list_issues, mock_scan, mock_filter, mock_create, mock_pipeline,
    mock_wait_ci, mock_merge, mock_commit,
):
    mock_sanity.return_value = True
    state = {
        "total_iterations": 0,
        "total_cost_usd": 0.0,
        "seen_fingerprints": [],
        "iterations": [],
        "termination_reason": None,
    }
    mock_load.return_value = state
    mock_list_issues.return_value = []
    mock_scan.return_value = _make_scan_result()
    mock_filter.return_value = [_make_candidate()]
    mock_create.return_value = "https://github.com/owner/repo/issues/10"
    mock_pipeline.return_value = ("pass", "submitted", "https://github.com/owner/repo/pull/11")
    mock_wait_ci.return_value = "pass"
    mock_merge.return_value = True

    from self_loop.loop import self_loop_run
    config = _make_config(max_iterations=1)
    reason = self_loop_run(config)

    assert reason == "max_iterations_reached"
    mock_merge.assert_called_once()
    mock_commit.assert_called()
