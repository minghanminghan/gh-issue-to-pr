"""Tests for read-only enforcement in file operations."""

import tempfile
from pathlib import Path

import pytest

from tools.fs import append_file, create_file, write_file


class TestReadOnlyEnforcement:
    def test_write_blocked_for_github_glob(self, tmp_path):
        (tmp_path / ".github").mkdir()
        result = write_file(
            ".github/CODEOWNERS", "* @owner",
            repo_root=tmp_path,
            read_only=[".github/**"],
        )
        assert not result["ok"]
        assert "read-only" in result["error"]

    def test_write_allowed_for_unlisted_path(self, tmp_path):
        result = write_file(
            "src/main.py", "# code",
            repo_root=tmp_path,
            read_only=[".github/**", "README.md"],
        )
        assert result["ok"]

    def test_append_blocked_for_read_only_path(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET=123\n")
        result = append_file(
            ".env", "MORE=456\n",
            repo_root=tmp_path,
            read_only=[".env*"],
        )
        assert not result["ok"]
        assert "read-only" in result["error"]

    def test_create_blocked_for_read_only_path(self, tmp_path):
        result = create_file(
            "LICENSE", "MIT",
            repo_root=tmp_path,
            read_only=["LICENSE"],
        )
        assert not result["ok"]
        assert "read-only" in result["error"]

    def test_lock_file_blocked(self, tmp_path):
        (tmp_path / "yarn.lock").write_text("{}")
        result = write_file(
            "yarn.lock", "{}",
            repo_root=tmp_path,
            read_only=["*.lock"],
        )
        assert not result["ok"]

    def test_env_variants_blocked(self, tmp_path):
        for filename in [".env", ".env.local", ".env.production"]:
            f = tmp_path / filename
            f.write_text("x=1")
            result = write_file(
                filename, "x=2",
                repo_root=tmp_path,
                read_only=[".env*"],
            )
            assert not result["ok"], f"Expected {filename} to be blocked"

    def test_contributing_md_blocked(self, tmp_path):
        (tmp_path / "CONTRIBUTING.md").write_text("# Contributing")
        result = write_file(
            "CONTRIBUTING.md", "# Hacked",
            repo_root=tmp_path,
            read_only=["CONTRIBUTING.md"],
        )
        assert not result["ok"]

    def test_empty_read_only_allows_everything(self, tmp_path):
        result = write_file("README.md", "# Test", repo_root=tmp_path, read_only=[])
        assert result["ok"]
