"""Tests for tools.setup."""

import os
from pathlib import Path
from unittest.mock import patch

# Spoof GITHUB_TOKEN so tools.setup can be imported
os.environ["GITHUB_TOKEN"] = "dummy"

from tools.setup import _run_hash, _clone_repo


def test_run_hash_deterministic():
    url = "https://github.com/owner/repo/issues/42"
    assert _run_hash(url) == _run_hash(url)
    assert len(_run_hash(url)) == 8


def test_run_hash_different_urls():
    url1 = "https://github.com/owner/repo/issues/1"
    url2 = "https://github.com/owner/repo/issues/2"
    assert _run_hash(url1) != _run_hash(url2)


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
