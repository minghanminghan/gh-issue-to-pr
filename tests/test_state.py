"""Tests for schema validation, state I/O, and init_run."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from schemas.state import DEFAULT_READ_ONLY, PipelineState, Step
from tools.state import _run_hash, init_run, read_state, write_state


def test_pipeline_state_defaults():
    state = PipelineState(repo_url="r", local_dir="/tmp", issue_url="i")
    assert state.loop_count == 0
    assert state.local_loop_count == 0
    assert state.cost_budget_usd == 2.00
    assert state.cost_spent_usd == 0.0
    assert state.commit_sha is None
    assert state.pr_url is None
    assert state.read_only == list(DEFAULT_READ_ONLY)


def test_pipeline_state_round_trip():
    state = PipelineState(
        repo_url="https://github.com/owner/repo",
        local_dir="/tmp/run",
        issue_url="https://github.com/owner/repo/issues/1",
        branch_name="agent/abc123",
        loop_count=1,
        local_loop_count=0,
        current_step=Step.execute,
        plan_version=2,
        last_failure_reason="lint error",
        cost_spent_usd=0.42,
        commit_sha="deadbeef",
        pr_url="https://github.com/owner/repo/pull/5",
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        run_dir = Path(tmpdir)
        write_state(run_dir, state)
        loaded = read_state(run_dir)
        assert loaded.branch_name == state.branch_name
        assert loaded.loop_count == state.loop_count
        assert loaded.commit_sha == state.commit_sha
        assert loaded.pr_url == state.pr_url
        assert loaded.cost_spent_usd == state.cost_spent_usd


def test_run_hash_deterministic():
    url = "https://github.com/owner/repo/issues/42"
    assert _run_hash(url) == _run_hash(url)
    assert len(_run_hash(url)) == 8


def test_run_hash_different_urls():
    url1 = "https://github.com/owner/repo/issues/1"
    url2 = "https://github.com/owner/repo/issues/2"
    assert _run_hash(url1) != _run_hash(url2)


def test_init_run_creates_directory():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        run_dir = init_run("https://github.com/x/y", "https://github.com/x/y/issues/1", repo_root)
        assert run_dir.exists()
        assert (run_dir / "STATE.json").exists()


def test_init_run_correct_hash():
    issue_url = "https://github.com/x/y/issues/1"
    expected_hash = _run_hash(issue_url)
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        run_dir = init_run("https://github.com/x/y", issue_url, repo_root)
        assert run_dir.name == expected_hash


def test_init_run_idempotent():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        issue_url = "https://github.com/x/y/issues/1"
        run_dir1 = init_run("https://github.com/x/y", issue_url, repo_root)
        run_dir2 = init_run("https://github.com/x/y", issue_url, repo_root)
        assert run_dir1 == run_dir2
        # STATE.json should not be overwritten
        state = read_state(run_dir1)
        state.loop_count = 5
        write_state(run_dir1, state)
        run_dir3 = init_run("https://github.com/x/y", issue_url, repo_root)
        reloaded = read_state(run_dir3)
        assert reloaded.loop_count == 5  # not reset by re-init


def test_init_run_adds_gitignore_entry():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        init_run("https://github.com/x/y", "https://github.com/x/y/issues/1", repo_root)
        gitignore = repo_root / ".gitignore"
        assert gitignore.exists()
        assert ".agent/" in gitignore.read_text()


def test_init_run_does_not_duplicate_gitignore_entry():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        gitignore = repo_root / ".gitignore"
        gitignore.write_text(".agent/\n")
        init_run("https://github.com/x/y", "https://github.com/x/y/issues/1", repo_root)
        content = gitignore.read_text()
        assert content.count(".agent/") == 1


def test_init_run_state_has_correct_fields():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        repo_url = "https://github.com/x/y"
        issue_url = "https://github.com/x/y/issues/1"
        run_dir = init_run(repo_url, issue_url, repo_root)
        state = read_state(run_dir)
        assert state.repo_url == repo_url
        assert state.issue_url == issue_url
        assert str(run_dir.resolve()) == state.local_dir
        assert list(DEFAULT_READ_ONLY) == state.read_only
