"""Tests for fs and shell tool primitives."""

import tempfile
from pathlib import Path

import pytest

from tools.fs import (
    _is_read_only,
    append_file,
    create_file,
    grep,
    list_dir,
    read_file,
    write_file,
)
from tools.shell import _check_allowlist, execute_cli


# ---------------------------------------------------------------------------
# fs tests
# ---------------------------------------------------------------------------

class TestReadFile:
    def test_read_existing_file(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("hello world")
        result = read_file("hello.txt", repo_root=tmp_path)
        assert result["ok"]
        assert result["output"] == "hello world"

    def test_read_missing_file(self, tmp_path):
        result = read_file("missing.txt", repo_root=tmp_path)
        assert not result["ok"]
        assert "not found" in result["error"]


class TestWriteFile:
    def test_write_new_file(self, tmp_path):
        result = write_file("out.txt", "content", repo_root=tmp_path)
        assert result["ok"]
        assert (tmp_path / "out.txt").read_text() == "content"

    def test_write_blocked_by_read_only(self, tmp_path):
        result = write_file(
            "README.md", "overwrite!", repo_root=tmp_path,
            read_only=["README.md"]
        )
        assert not result["ok"]
        assert "read-only" in result["error"]

    def test_write_creates_parent_dirs(self, tmp_path):
        result = write_file("subdir/file.txt", "data", repo_root=tmp_path)
        assert result["ok"]
        assert (tmp_path / "subdir" / "file.txt").read_text() == "data"


class TestCreateFile:
    def test_create_new_file(self, tmp_path):
        result = create_file("new.py", "# code", repo_root=tmp_path)
        assert result["ok"]
        assert (tmp_path / "new.py").read_text() == "# code"

    def test_create_existing_file_fails(self, tmp_path):
        (tmp_path / "exists.py").write_text("old")
        result = create_file("exists.py", "new", repo_root=tmp_path)
        assert not result["ok"]
        assert "already exists" in result["error"]


class TestAppendFile:
    def test_append_to_existing_file(self, tmp_path):
        f = tmp_path / "log.txt"
        f.write_text("line1\n")
        result = append_file("log.txt", "line2\n", repo_root=tmp_path)
        assert result["ok"]
        assert f.read_text() == "line1\nline2\n"

    def test_append_to_missing_file_fails(self, tmp_path):
        result = append_file("missing.txt", "data", repo_root=tmp_path)
        assert not result["ok"]


class TestListDir:
    def test_list_directory(self, tmp_path):
        (tmp_path / "a.py").touch()
        (tmp_path / "subdir").mkdir()
        result = list_dir(".", repo_root=tmp_path)
        assert result["ok"]
        assert "a.py" in result["output"]
        assert "subdir/" in result["output"]

    def test_list_missing_dir(self, tmp_path):
        result = list_dir("nope", repo_root=tmp_path)
        assert not result["ok"]


class TestGrep:
    def test_grep_finds_pattern(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("def foo():\n    return 42\n")
        result = grep("def foo", "code.py", repo_root=tmp_path)
        assert result["ok"]
        assert "def foo" in result["output"]

    def test_grep_no_matches(self, tmp_path):
        (tmp_path / "empty.py").write_text("x = 1\n")
        result = grep("zzz_notfound", "empty.py", repo_root=tmp_path)
        assert result["ok"]
        assert "no matches" in result["output"]

    def test_grep_case_insensitive(self, tmp_path):
        f = tmp_path / "f.py"
        f.write_text("Hello World\n")
        result = grep("hello", "f.py", repo_root=tmp_path, flags="i")
        assert result["ok"]
        assert "Hello" in result["output"]

    def test_grep_invalid_regex(self, tmp_path):
        (tmp_path / "f.py").write_text("x\n")
        result = grep("[invalid", "f.py", repo_root=tmp_path)
        assert not result["ok"]
        assert "Invalid regex" in result["error"]


class TestIsReadOnly:
    def test_exact_match(self):
        assert _is_read_only("README.md", ["README.md"])

    def test_glob_pattern(self):
        assert _is_read_only(".github/CODEOWNERS", [".github/**"])

    def test_env_glob(self):
        assert _is_read_only(".env.production", [".env*"])

    def test_lock_glob(self):
        assert _is_read_only("yarn.lock", ["*.lock"])

    def test_not_read_only(self):
        assert not _is_read_only("src/main.py", ["README.md", ".github/**"])


# ---------------------------------------------------------------------------
# shell tests
# ---------------------------------------------------------------------------

class TestCheckAllowlist:
    def test_allowed_binary_with_prefix(self):
        assert _check_allowlist("pytest", "tests/", [("pytest", "tests/")])

    def test_allowed_binary_any_args(self):
        assert _check_allowlist("ruff", "check .", [("ruff", "")])

    def test_rejected_binary(self):
        assert not _check_allowlist("rm", "-rf /", [("pytest", "")])

    def test_rejected_args_prefix(self):
        assert not _check_allowlist("python", "/evil.py", [("python", "-m pytest")])

    def test_allowed_exact_prefix(self):
        assert _check_allowlist("python", "-m pytest tests/", [("python", "-m pytest")])


class TestExecuteCli:
    def test_rejects_semicolon(self, tmp_path):
        result = execute_cli("echo hello; rm -rf .", [("echo", "")], cwd=tmp_path)
        assert not result["ok"]
        assert "metacharacter" in result["error"]

    def test_rejects_pipe(self, tmp_path):
        result = execute_cli("cat file | grep foo", [("cat", "")], cwd=tmp_path)
        assert not result["ok"]
        assert "metacharacter" in result["error"]

    def test_rejects_ampersand(self, tmp_path):
        result = execute_cli("sleep 1 & echo done", [("sleep", "")], cwd=tmp_path)
        assert not result["ok"]
        assert "metacharacter" in result["error"]

    def test_rejects_dotdot_path(self, tmp_path):
        result = execute_cli("python ../evil.py", [("python", "")], cwd=tmp_path)
        assert not result["ok"]
        assert ".." in result["error"]

    def test_rejects_unlisted_binary(self, tmp_path):
        result = execute_cli("rm -rf .", [("pytest", "")], cwd=tmp_path)
        assert not result["ok"]
        assert "not in allowlist" in result["error"]

    def test_runs_allowed_command(self, tmp_path):
        # python --version should succeed
        result = execute_cli("python --version", [("python", "")], cwd=tmp_path)
        assert result["ok"]
        assert "Python" in result["output"]
