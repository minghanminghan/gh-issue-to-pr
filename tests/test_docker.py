"""Tests for tools/docker.py and Docker-related shell routing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.docker import _docker_available, install_project_deps, start_container, stop_container
from tools.shell import HOST_BINARIES, SANDBOX_BINARIES, execute_cli


class TestDockerAvailable:
    def test_returns_true_when_docker_on_path(self):
        with patch("shutil.which", return_value="/usr/bin/docker"):
            assert _docker_available() is True

    def test_returns_false_when_docker_not_found(self):
        with patch("shutil.which", return_value=None):
            assert _docker_available() is False


class TestStartContainer:
    def test_returns_none_when_docker_unavailable(self, tmp_path):
        with patch("tools.docker._docker_available", return_value=False):
            result = start_container(tmp_path)
        assert result is None

    def test_warns_to_stderr_when_docker_unavailable(self, tmp_path, capsys):
        with patch("tools.docker._docker_available", return_value=False):
            start_container(tmp_path)
        captured = capsys.readouterr()
        assert "Warning" in captured.err

    def test_returns_container_id_on_success(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "abc123def456\n"
        with patch("tools.docker._docker_available", return_value=True):
            with patch("subprocess.run", return_value=mock_result):
                result = start_container(tmp_path)
        assert result == "abc123def456"

    def test_raises_on_docker_run_failure(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "image not found"
        with patch("tools.docker._docker_available", return_value=True):
            with patch("subprocess.run", return_value=mock_result):
                with pytest.raises(RuntimeError, match="docker run failed"):
                    start_container(tmp_path)

    def test_cmd_includes_volume_mount(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "container123\n"
        with patch("tools.docker._docker_available", return_value=True):
            with patch("subprocess.run", return_value=mock_result) as mock_run:
                start_container(tmp_path)
        cmd = mock_run.call_args[0][0]
        assert "--volume" in cmd
        assert "/workspace" in " ".join(cmd)

    def test_cmd_includes_workdir(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "container123\n"
        with patch("tools.docker._docker_available", return_value=True):
            with patch("subprocess.run", return_value=mock_result) as mock_run:
                start_container(tmp_path)
        cmd = mock_run.call_args[0][0]
        assert "--workdir" in cmd

    def test_cmd_includes_sleep_infinity(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "container123\n"
        with patch("tools.docker._docker_available", return_value=True):
            with patch("subprocess.run", return_value=mock_result) as mock_run:
                start_container(tmp_path)
        cmd = mock_run.call_args[0][0]
        assert "sleep" in cmd
        assert "infinity" in cmd

    def test_custom_image_used_in_cmd(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "cid\n"
        with patch("tools.docker._docker_available", return_value=True):
            with patch("subprocess.run", return_value=mock_result) as mock_run:
                start_container(tmp_path, image="node:20-slim")
        cmd = mock_run.call_args[0][0]
        assert "node:20-slim" in cmd

    def test_cmd_includes_detach_and_rm(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "cid\n"
        with patch("tools.docker._docker_available", return_value=True):
            with patch("subprocess.run", return_value=mock_result) as mock_run:
                start_container(tmp_path)
        cmd = mock_run.call_args[0][0]
        assert "--detach" in cmd
        assert "--rm" in cmd


class TestStopContainer:
    def test_calls_docker_stop(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            stop_container("abc123")
        cmd = mock_run.call_args[0][0]
        assert cmd == ["docker", "stop", "abc123"]

    def test_empty_string_is_noop(self):
        with patch("subprocess.run") as mock_run:
            stop_container("")
        mock_run.assert_not_called()

    def test_none_is_noop(self):
        with patch("subprocess.run") as mock_run:
            stop_container(None)
        mock_run.assert_not_called()

    def test_silently_ignores_errors(self):
        with patch("subprocess.run", side_effect=Exception("docker gone")):
            # Must not raise
            stop_container("some_id")


class TestInstallProjectDeps:
    def test_skips_when_no_pyproject_toml(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            install_project_deps("cid", tmp_path)
        mock_run.assert_not_called()

    def test_runs_pip_install_when_pyproject_toml_exists(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'")
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            install_project_deps("cid", tmp_path)
        cmd = mock_run.call_args[0][0]
        assert "docker" in cmd
        assert "exec" in cmd
        assert "pip" in cmd
        assert "install" in cmd

    def test_warns_on_pip_failure_does_not_raise(self, tmp_path, capsys):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'")
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "no such package"
        with patch("subprocess.run", return_value=mock_result):
            install_project_deps("cid", tmp_path)  # must not raise
        captured = capsys.readouterr()
        assert "Warning" in captured.err


class TestSandboxBinarySets:
    def test_sandbox_binaries_contains_code_tools(self):
        assert "python" in SANDBOX_BINARIES
        assert "pytest" in SANDBOX_BINARIES
        assert "ruff" in SANDBOX_BINARIES
        assert "mypy" in SANDBOX_BINARIES
        assert "node" in SANDBOX_BINARIES
        assert "cargo" in SANDBOX_BINARIES
        assert "npm" in SANDBOX_BINARIES

    def test_sandbox_binaries_excludes_host_tools(self):
        assert "git" not in SANDBOX_BINARIES
        assert "gh" not in SANDBOX_BINARIES

    def test_host_binaries_contains_vcs_tools(self):
        assert "git" in HOST_BINARIES
        assert "gh" in HOST_BINARIES

    def test_host_binaries_excludes_code_tools(self):
        assert "python" not in HOST_BINARIES
        assert "pytest" not in HOST_BINARIES


class TestSandboxBinaryRouting:
    def _make_run(self, cmd, allowlist, cwd, container_id=None):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            execute_cli(cmd, allowlist, cwd=cwd, container_id=container_id)
        return mock_run.call_args[0][0]

    def test_sandbox_binary_wrapped_in_docker_exec(self, tmp_path):
        cmd = self._make_run("python --version", [("python", "")], tmp_path, container_id="cid123")
        assert cmd[0] == "docker"
        assert "exec" in cmd
        assert "cid123" in cmd
        assert "python" in cmd

    def test_pytest_wrapped_in_docker_exec(self, tmp_path):
        cmd = self._make_run("pytest tests/", [("pytest", "")], tmp_path, container_id="cid123")
        assert cmd[0] == "docker"
        assert "pytest" in cmd

    def test_ruff_wrapped_in_docker_exec(self, tmp_path):
        cmd = self._make_run("ruff check .", [("ruff", "check")], tmp_path, container_id="cid123")
        assert cmd[0] == "docker"
        assert "ruff" in cmd

    def test_git_not_wrapped(self, tmp_path):
        cmd = self._make_run("git status", [("git", "status")], tmp_path, container_id="cid123")
        assert cmd[0] == "git"
        assert "docker" not in cmd

    def test_gh_not_wrapped(self, tmp_path):
        cmd = self._make_run("gh pr view", [("gh", "pr")], tmp_path, container_id="cid123")
        assert cmd[0] == "gh"
        assert "docker" not in cmd

    def test_no_container_id_runs_directly(self, tmp_path):
        cmd = self._make_run("pytest --version", [("pytest", "")], tmp_path, container_id=None)
        assert cmd[0] == "pytest"
        assert "docker" not in cmd

    def test_docker_exec_includes_workdir_flag(self, tmp_path):
        cmd = self._make_run("python --version", [("python", "")], tmp_path, container_id="cid")
        assert "--workdir" in cmd
        assert "/workspace" in cmd


class TestContainerIdInState:
    def test_container_id_defaults_to_none(self):
        from schemas.state import PipelineState
        s = PipelineState(repo_url="r", local_dir="/t", issue_url="i")
        assert s.container_id is None

    def test_container_id_round_trips_through_state_json(self, tmp_path):
        from schemas.state import PipelineState
        from tools.state import read_state, write_state
        run_dir = tmp_path
        s = PipelineState(
            repo_url="r",
            local_dir=str(tmp_path),
            issue_url="i",
            container_id="abc123",
        )
        write_state(run_dir, s)
        loaded = read_state(run_dir)
        assert loaded.container_id == "abc123"

    def test_existing_state_without_container_id_loads_as_none(self, tmp_path):
        import json
        from schemas.state import PipelineState
        from tools.state import read_state
        # Write a STATE.json without container_id (simulates old format)
        state_data = {
            "repo_url": "r",
            "local_dir": str(tmp_path),
            "issue_url": "i",
        }
        (tmp_path / "STATE.json").write_text(json.dumps(state_data))
        loaded = read_state(tmp_path)
        assert loaded.container_id is None


class TestReportStopsContainer:
    def _make_run_dir(self, tmp_path, container_id=None):
        from schemas.state import FailureSource, PipelineState, Step
        from tools.state import write_state
        run_dir = tmp_path / ".agent" / "abc12345"
        run_dir.mkdir(parents=True)
        state = PipelineState(
            repo_url="https://github.com/x/y",
            local_dir=str(run_dir),
            issue_url="https://github.com/x/y/issues/1",
            current_step=Step.report,
            failure_source=FailureSource.exec,
            last_failure_reason="err",
            loop_count=1,
            container_id=container_id,
        )
        write_state(run_dir, state)
        return run_dir

    def test_stop_container_called_on_pass(self, tmp_path):
        run_dir = self._make_run_dir(tmp_path, container_id="cid_to_stop")
        with patch("tools.report.stop_container") as mock_stop:
            from tools.report import run_report
            run_report(run_dir, "pass")
        mock_stop.assert_called_once_with("cid_to_stop")

    def test_stop_container_called_on_fail(self, tmp_path):
        run_dir = self._make_run_dir(tmp_path, container_id="cid_to_stop")
        with patch("tools.report.stop_container") as mock_stop:
            from tools.report import run_report
            with pytest.raises(SystemExit):
                run_report(run_dir, "fail")
        mock_stop.assert_called_once_with("cid_to_stop")

    def test_no_container_id_no_stop_call(self, tmp_path):
        run_dir = self._make_run_dir(tmp_path, container_id=None)
        with patch("tools.report.stop_container") as mock_stop:
            from tools.report import run_report
            run_report(run_dir, "pass")
        mock_stop.assert_not_called()
