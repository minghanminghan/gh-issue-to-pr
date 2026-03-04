"""Tests for the FastAPI server (server.py)."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import server
from server import app

client = TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Synchronous thread helper: makes Thread.start() execute target immediately
# ---------------------------------------------------------------------------

class _SyncThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args

    def start(self):
        if self._target:
            self._target(*self._args)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_returns_200(self):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_ok_status(self):
        response = client.get("/health")
        assert response.json()["status"] == "ok"

    def test_health_returns_version(self):
        response = client.get("/health")
        assert response.json()["version"] == "0.1.0"


# ---------------------------------------------------------------------------
# POST /issue — input validation
# ---------------------------------------------------------------------------

class TestIssueEndpointValidation:
    def test_empty_body_returns_422(self):
        response = client.post("/issue", json={})
        assert response.status_code == 422

    def test_missing_repo_url_returns_422(self):
        response = client.post("/issue", json={"issue_url": "https://github.com/x/y/issues/1"})
        assert response.status_code == 422

    def test_missing_issue_url_returns_422(self):
        response = client.post("/issue", json={"repo_url": "https://github.com/x/y"})
        assert response.status_code == 422

    def test_budget_zero_returns_422(self):
        response = client.post("/issue", json={
            "issue_url": "https://github.com/x/y/issues/1",
            "repo_url": "https://github.com/x/y",
            "budget": 0,
        })
        assert response.status_code == 422

    def test_budget_negative_returns_422(self):
        response = client.post("/issue", json={
            "issue_url": "https://github.com/x/y/issues/1",
            "repo_url": "https://github.com/x/y",
            "budget": -1.0,
        })
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /issue — 202 Accepted response
# ---------------------------------------------------------------------------

class TestIssueEndpoint202:
    _BASE = {
        "issue_url": "https://github.com/x/y/issues/202test",
        "repo_url": "https://github.com/x/y",
    }

    def test_returns_202(self):
        with patch("server.threading.Thread", _SyncThread), \
             patch("server.run_pipeline", return_value=None):
            response = client.post("/issue", json=self._BASE)
        assert response.status_code == 202

    def test_response_has_issue_url(self):
        with patch("server.threading.Thread", _SyncThread), \
             patch("server.run_pipeline", return_value=None):
            response = client.post("/issue", json=self._BASE)
        assert response.json()["issue_url"] == self._BASE["issue_url"]

    def test_response_has_status_url(self):
        with patch("server.threading.Thread", _SyncThread), \
             patch("server.run_pipeline", return_value=None):
            response = client.post("/issue", json=self._BASE)
        data = response.json()
        assert "status_url" in data
        assert self._BASE["issue_url"] in data["status_url"]

    def test_already_running_returns_202_no_new_thread(self):
        issue_url = "https://github.com/x/y/issues/already_running"
        with patch.dict(server._jobs, {
            issue_url: {"status": "running", "run_dir": None, "outcome": None, "error": None}
        }):
            with patch("server.threading.Thread") as mock_thread_cls:
                response = client.post("/issue", json={
                    "issue_url": issue_url,
                    "repo_url": "https://github.com/x/y",
                })
        mock_thread_cls.assert_not_called()
        assert response.status_code == 202

    def test_already_queued_returns_202_no_new_thread(self):
        issue_url = "https://github.com/x/y/issues/already_queued"
        with patch.dict(server._jobs, {
            issue_url: {"status": "queued", "run_dir": None, "outcome": None, "error": None}
        }):
            with patch("server.threading.Thread") as mock_thread_cls:
                response = client.post("/issue", json={
                    "issue_url": issue_url,
                    "repo_url": "https://github.com/x/y",
                })
        mock_thread_cls.assert_not_called()
        assert response.status_code == 202


# ---------------------------------------------------------------------------
# POST /issue — job outcomes via synchronous thread
# ---------------------------------------------------------------------------

class TestIssueEndpointJobOutcomes:
    def _make_run_dir(self, tmp_path):
        from schemas.state import PipelineState, Step
        from tools.state import init_run, write_state
        run_dir = init_run(
            "https://github.com/x/y",
            "https://github.com/x/y/issues/outcome_test",
            tmp_path,
        )
        state = PipelineState(
            repo_url="https://github.com/x/y",
            local_dir=str(run_dir),
            issue_url="https://github.com/x/y/issues/outcome_test",
            pr_url="https://github.com/x/y/pull/99",
            cost_spent_usd=0.42,
            loop_count=1,
            current_step=Step.report,
        )
        write_state(run_dir, state)
        return run_dir

    def test_pass_outcome_stored(self, tmp_path):
        run_dir = self._make_run_dir(tmp_path)
        issue_url = "https://github.com/x/y/issues/outcome_test"
        with patch("server.threading.Thread", _SyncThread), \
             patch("server.run_pipeline", return_value=run_dir):
            client.post("/issue", json={"issue_url": issue_url, "repo_url": "https://github.com/x/y"})
        with server._jobs_lock:
            job = server._jobs.get(issue_url)
        assert job is not None
        assert job["outcome"] == "pass"
        assert job["status"] == "completed"

    def test_system_exit_stored_as_failed(self):
        issue_url = "https://github.com/x/y/issues/sysexit_test"
        with patch("server.threading.Thread", _SyncThread), \
             patch("server.run_pipeline", side_effect=SystemExit(1)):
            client.post("/issue", json={"issue_url": issue_url, "repo_url": "https://github.com/x/y"})
        with server._jobs_lock:
            job = server._jobs.get(issue_url)
        assert job["outcome"] == "fail"
        assert job["status"] == "failed"

    def test_exception_stored_as_failed_with_error(self):
        issue_url = "https://github.com/x/y/issues/exception_test"
        with patch("server.threading.Thread", _SyncThread), \
             patch("server.run_pipeline", side_effect=RuntimeError("Boom")):
            client.post("/issue", json={"issue_url": issue_url, "repo_url": "https://github.com/x/y"})
        with server._jobs_lock:
            job = server._jobs.get(issue_url)
        assert job["outcome"] == "fail"
        assert "Boom" in (job["error"] or "")


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------

class TestStatusEndpoint:
    def test_unknown_issue_returns_404(self):
        response = client.get("/status", params={"issue": "https://github.com/unknown/issues/999"})
        assert response.status_code == 404

    def test_queued_job_returns_queued_status(self):
        issue_url = "https://github.com/x/y/issues/status_queued"
        with patch.dict(server._jobs, {
            issue_url: {"status": "queued", "run_dir": None, "outcome": None, "error": None}
        }):
            response = client.get("/status", params={"issue": issue_url})
        assert response.status_code == 200
        assert response.json()["status"] == "queued"

    def test_running_job_returns_running_status(self):
        issue_url = "https://github.com/x/y/issues/status_running"
        with patch.dict(server._jobs, {
            issue_url: {"status": "running", "run_dir": None, "outcome": None, "error": None}
        }):
            response = client.get("/status", params={"issue": issue_url})
        assert response.json()["status"] == "running"

    def test_completed_job_returns_state(self, tmp_path):
        from schemas.state import PipelineState, Step
        from tools.state import init_run, write_state
        run_dir = init_run("https://github.com/x/y", "https://github.com/x/y/issues/status3", tmp_path)
        state = PipelineState(
            repo_url="https://github.com/x/y",
            local_dir=str(run_dir),
            issue_url="https://github.com/x/y/issues/status3",
            pr_url="https://github.com/x/y/pull/55",
            current_step=Step.report,
        )
        write_state(run_dir, state)
        issue_url = "https://github.com/x/y/issues/status3"
        with patch.dict(server._jobs, {
            issue_url: {"status": "completed", "run_dir": str(run_dir), "outcome": "pass", "error": None}
        }):
            response = client.get("/status", params={"issue": issue_url})
        data = response.json()
        assert data["status"] == "completed"
        assert data["outcome"] == "pass"
        assert data["state"] is not None
        assert data["state"]["pr_url"] == "https://github.com/x/y/pull/55"

    def test_failed_job_returns_error(self):
        issue_url = "https://github.com/x/y/issues/status_failed"
        with patch.dict(server._jobs, {
            issue_url: {"status": "failed", "run_dir": None, "outcome": "fail", "error": "Something broke"}
        }):
            response = client.get("/status", params={"issue": issue_url})
        data = response.json()
        assert data["status"] == "failed"
        assert data["outcome"] == "fail"
        assert "Something broke" in (data["error"] or "")

    def test_status_response_has_issue_url(self):
        issue_url = "https://github.com/x/y/issues/status_fields"
        with patch.dict(server._jobs, {
            issue_url: {"status": "queued", "run_dir": None, "outcome": None, "error": None}
        }):
            response = client.get("/status", params={"issue": issue_url})
        assert response.json()["issue_url"] == issue_url


# ---------------------------------------------------------------------------
# POST /issue — guidelines handling
# ---------------------------------------------------------------------------

class TestIssueEndpointGuidelinesHandling:
    _ISSUE_URL = "https://github.com/x/y/issues/guidelines_test"

    def _capture_pipeline_kwargs(self, tmp_path):
        from tools.state import init_run
        run_dir = init_run("https://github.com/x/y", self._ISSUE_URL, tmp_path)
        captured = {}

        def capture(*args, **kwargs):
            captured.update(kwargs)
            return run_dir

        return run_dir, captured, capture

    def test_guidelines_string_written_to_tempfile(self, tmp_path):
        _, captured, capture = self._capture_pipeline_kwargs(tmp_path)
        with patch("server.threading.Thread", _SyncThread), \
             patch("server.run_pipeline", side_effect=capture):
            client.post("/issue", json={
                "issue_url": self._ISSUE_URL,
                "repo_url": "https://github.com/x/y",
                "guidelines": "# My guidelines\n- Do this",
            })
        gp = captured.get("guidelines_path")
        assert gp is not None
        assert gp.endswith(".md")

    def test_tempfile_is_cleaned_up_after_job(self, tmp_path):
        _, captured, capture = self._capture_pipeline_kwargs(tmp_path)
        with patch("server.threading.Thread", _SyncThread), \
             patch("server.run_pipeline", side_effect=capture):
            client.post("/issue", json={
                "issue_url": self._ISSUE_URL,
                "repo_url": "https://github.com/x/y",
                "guidelines": "some content",
            })
        gp = captured.get("guidelines_path")
        assert gp is None or not Path(gp).exists()

    def test_null_guidelines_passes_none_to_pipeline(self, tmp_path):
        _, captured, capture = self._capture_pipeline_kwargs(tmp_path)
        with patch("server.threading.Thread", _SyncThread), \
             patch("server.run_pipeline", side_effect=capture):
            client.post("/issue", json={
                "issue_url": self._ISSUE_URL,
                "repo_url": "https://github.com/x/y",
            })
        assert captured.get("guidelines_path") is None

    def test_local_path_forwarded_to_pipeline(self, tmp_path):
        _, captured, capture = self._capture_pipeline_kwargs(tmp_path)
        with patch("server.threading.Thread", _SyncThread), \
             patch("server.run_pipeline", side_effect=capture):
            client.post("/issue", json={
                "issue_url": self._ISSUE_URL,
                "repo_url": "https://github.com/x/y",
                "local_path": "/some/path",
            })
        assert captured.get("local_path") == "/some/path"


# ---------------------------------------------------------------------------
# main.py subcommand structure
# ---------------------------------------------------------------------------

class TestMainSubcommands:
    def test_serve_subcommand_calls_uvicorn(self):
        from main import _serve_subcommand
        with patch("uvicorn.run") as mock_uvicorn:
            args = argparse.Namespace(host="0.0.0.0", port=8080)
            _serve_subcommand(args)
        mock_uvicorn.assert_called_once_with(
            "server:app",
            host="0.0.0.0",
            port=8080,
            reload=False,
        )

    def test_main_help_exits_zero(self):
        from main import main
        with patch("sys.argv", ["main.py", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

    def test_main_no_args_exits_zero(self):
        from main import main
        with patch("sys.argv", ["main.py"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

    def test_serve_help_exits_zero(self):
        from main import main
        with patch("sys.argv", ["main.py", "serve", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

    def test_run_help_exits_zero(self):
        from main import main
        with patch("sys.argv", ["main.py", "run", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0
