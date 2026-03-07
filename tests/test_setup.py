"""Tests for tools.setup."""

import os
import tempfile
from pathlib import Path

# Spoof GITHUB_TOKEN so tools.setup can be imported
os.environ["GITHUB_TOKEN"] = "dummy"

# import pytest

from tools.setup import _run_hash, init_run, _clone_repo
from unittest.mock import patch


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
        run_dir = init_run("https://github.com/x/y/issues/1", repo_root)
        assert run_dir.exists()


def test_init_run_correct_hash():
    issue_url = "https://github.com/x/y/issues/1"
    expected_hash = _run_hash(issue_url)
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        run_dir = init_run(issue_url, repo_root)
        assert run_dir.parent.name == expected_hash


def test_init_run_idempotent():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        issue_url = "https://github.com/x/y/issues/1"
        run_dir1 = init_run(issue_url, repo_root)
        run_dir2 = init_run(issue_url, repo_root)
        assert run_dir1 == run_dir2


def test_init_run_adds_gitignore_entry():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        init_run("https://github.com/x/y/issues/1", repo_root)
        gitignore = repo_root / ".gitignore"
        assert gitignore.exists()
        assert "run/" in gitignore.read_text()


def test_init_run_does_not_duplicate_gitignore_entry():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        gitignore = repo_root / ".gitignore"
        gitignore.write_text("run/\n")
        init_run("https://github.com/x/y/issues/1", repo_root)
        content = gitignore.read_text()
        assert content.count("run/") == 1


def test_init_run_returns_correct_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        issue_url = "https://github.com/x/y/issues/1"
        run_dir = init_run(issue_url, repo_root)
        assert run_dir.name == ".agent"
        assert run_dir.parent.name == _run_hash(issue_url)


@patch("tools.setup.subprocess.run")
def test_clone_repo_collision_overwrites(mock_run, monkeypatch, tmp_path):
    mock_run.return_value.returncode = 0
    monkeypatch.chdir(tmp_path)
    run_hash = "abcdef12"
    clone_dir = Path("run") / run_hash
    clone_dir.mkdir(parents=True, exist_ok=True)
    
    _clone_repo("https://github.com/repo/issues/1", run_hash)
    
    calls = mock_run.call_args_list
    assert len(calls) == 1
    assert "clone" in calls[0][0][0]
