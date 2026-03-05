import pytest
from pydantic import ValidationError

from schemas.state import PipelineState, Step, FailureSource, CIStatus, DEFAULT_READ_ONLY


def test_pipeline_state_valid():
    state = PipelineState(
        repo_url="https://github.com/owner/repo",
        local_dir="/tmp/.agent/hash123",
        issue_url="https://github.com/owner/repo/issues/1"
    )
    assert state.repo_url == "https://github.com/owner/repo"
    assert state.local_dir == "/tmp/.agent/hash123"
    assert state.issue_url == "https://github.com/owner/repo/issues/1"
    assert state.issue_body == ""
    assert state.branch_name == ""
    assert state.loop_count == 0
    assert state.local_loop_count == 0
    assert state.current_step == Step.setup
    assert state.plan_version == 0
    assert state.last_failure_reason is None
    assert state.failure_source is None
    assert state.ci_status is None
    assert state.commit_sha is None
    assert state.pr_url is None
    assert state.cost_budget_usd == 2.00
    assert state.cost_spent_usd == 0.0
    assert state.read_only == DEFAULT_READ_ONLY


def test_pipeline_state_invalid_loop_count():
    with pytest.raises(ValidationError):
        PipelineState(
            repo_url="https://github.com/owner/repo",
            local_dir="/tmp/.agent/hash123",
            issue_url="https://github.com/owner/repo/issues/1",
            loop_count=-1
        )


def test_pipeline_state_invalid_local_loop_count():
    with pytest.raises(ValidationError):
        PipelineState(
            repo_url="https://github.com/owner/repo",
            local_dir="/tmp/.agent/hash123",
            issue_url="https://github.com/owner/repo/issues/1",
            local_loop_count=-1
        )


def test_pipeline_state_required_fields():
    with pytest.raises(ValidationError):
        PipelineState(
            repo_url="https://github.com/owner/repo"
        )


def test_enums():
    assert Step.setup == "setup"
    assert FailureSource.exec == "exec"
    assert CIStatus.pass_ == "pass"

