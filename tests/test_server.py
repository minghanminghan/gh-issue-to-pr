"""Tests for the FastAPI server (server.py)."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from server import app

client = TestClient(app, raise_server_exceptions=False)


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
# POST /issue — pass outcome
# ---------------------------------------------------------------------------

class TestIssueEndpointPassOutcome:
    def _make_run_dir(self, tmp_path):
        from schemas.state import PipelineState, Step
        from tools.state import init_run, write_state
        run_dir = init_run(
            "https://github.com/x/y",
            "https://github.com/x/y/issues/1",
            tmp_path,
        )
        state = PipelineState(
            repo_url="https://github.com/x/y",
            local_dir=str(run_dir),
            issue_url="https://github.com/x/y/issues/1",
            pr_url="https://github.com/x/y/pull/99",
            cost_spent_usd=0.42,
            loop_count=1,
            current_step=Step.report,
        )
        write_state(run_dir, state)
        return run_dir

    def test_pass_returns_200(self, tmp_path):
        run_dir = self._make_run_dir(tmp_path)
        with patch("server.run_pipeline", return_value=run_dir):
            response = client.post("/issue", json={
                "issue_url": "https://github.com/x/y/issues/1",
                "repo_url": "https://github.com/x/y",
            })
        assert response.status_code == 200

    def test_pass_outcome_field(self, tmp_path):
        run_dir = self._make_run_dir(tmp_path)
        with patch("server.run_pipeline", return_value=run_dir):
            response = client.post("/issue", json={
                "issue_url": "https://github.com/x/y/issues/1",
                "repo_url": "https://github.com/x/y",
            })
        assert response.json()["outcome"] == "pass"

    def test_pass_pr_url_populated(self, tmp_path):
        run_dir = self._make_run_dir(tmp_path)
        with patch("server.run_pipeline", return_value=run_dir):
            response = client.post("/issue", json={
                "issue_url": "https://github.com/x/y/issues/1",
                "repo_url": "https://github.com/x/y",
            })
        assert response.json()["pr_url"] == "https://github.com/x/y/pull/99"

    def test_pass_cost_and_loop_count(self, tmp_path):
        run_dir = self._make_run_dir(tmp_path)
        with patch("server.run_pipeline", return_value=run_dir):
            response = client.post("/issue", json={
                "issue_url": "https://github.com/x/y/issues/1",
                "repo_url": "https://github.com/x/y",
            })
        data = response.json()
        assert data["cost_spent_usd"] == pytest.approx(0.42)
        assert data["loop_count"] == 1

    def test_pass_response_has_run_dir(self, tmp_path):
        run_dir = self._make_run_dir(tmp_path)
        with patch("server.run_pipeline", return_value=run_dir):
            response = client.post("/issue", json={
                "issue_url": "https://github.com/x/y/issues/1",
                "repo_url": "https://github.com/x/y",
            })
        assert response.json()["run_dir"] != ""

    def test_pass_response_schema_keys(self, tmp_path):
        run_dir = self._make_run_dir(tmp_path)
        with patch("server.run_pipeline", return_value=run_dir):
            response = client.post("/issue", json={
                "issue_url": "https://github.com/x/y/issues/1",
                "repo_url": "https://github.com/x/y",
            })
        assert set(response.json().keys()) >= {"run_dir", "outcome", "pr_url", "cost_spent_usd", "loop_count"}


# ---------------------------------------------------------------------------
# POST /issue — fail outcome (SystemExit interception)
# ---------------------------------------------------------------------------

class TestIssueEndpointFailOutcome:
    def test_system_exit_returns_200_not_500(self):
        with patch("server.run_pipeline", side_effect=SystemExit(1)):
            response = client.post("/issue", json={
                "issue_url": "https://github.com/x/y/issues/1",
                "repo_url": "https://github.com/x/y",
            })
        assert response.status_code == 200

    def test_system_exit_outcome_is_fail(self):
        with patch("server.run_pipeline", side_effect=SystemExit(1)):
            response = client.post("/issue", json={
                "issue_url": "https://github.com/x/y/issues/1",
                "repo_url": "https://github.com/x/y",
            })
        assert response.json()["outcome"] == "fail"

    def test_system_exit_pr_url_is_none(self):
        with patch("server.run_pipeline", side_effect=SystemExit(1)):
            response = client.post("/issue", json={
                "issue_url": "https://github.com/x/y/issues/1",
                "repo_url": "https://github.com/x/y",
            })
        assert response.json()["pr_url"] is None

    def test_unexpected_exception_returns_500(self):
        with patch("server.run_pipeline", side_effect=RuntimeError("Boom")):
            response = client.post("/issue", json={
                "issue_url": "https://github.com/x/y/issues/1",
                "repo_url": "https://github.com/x/y",
            })
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# POST /issue — guidelines handling
# ---------------------------------------------------------------------------

class TestIssueEndpointGuidelinesHandling:
    def _capture_pipeline_kwargs(self, tmp_path):
        from tools.state import init_run
        run_dir = init_run("https://github.com/x/y", "https://github.com/x/y/issues/1", tmp_path)
        captured = {}

        def capture(*args, **kwargs):
            captured.update(kwargs)
            return run_dir

        return run_dir, captured, capture

    def test_guidelines_string_written_to_tempfile(self, tmp_path):
        run_dir, captured, capture = self._capture_pipeline_kwargs(tmp_path)
        with patch("server.run_pipeline", side_effect=capture):
            client.post("/issue", json={
                "issue_url": "https://github.com/x/y/issues/1",
                "repo_url": "https://github.com/x/y",
                "guidelines": "# My guidelines\n- Do this",
            })
        gp = captured.get("guidelines_path")
        assert gp is not None
        assert gp.endswith(".md")

    def test_tempfile_is_cleaned_up_after_request(self, tmp_path):
        run_dir, captured, capture = self._capture_pipeline_kwargs(tmp_path)
        with patch("server.run_pipeline", side_effect=capture):
            client.post("/issue", json={
                "issue_url": "https://github.com/x/y/issues/1",
                "repo_url": "https://github.com/x/y",
                "guidelines": "some content",
            })
        gp = captured.get("guidelines_path")
        # Tempfile should be deleted after the request completes
        assert gp is None or not Path(gp).exists()

    def test_null_guidelines_passes_none_to_pipeline(self, tmp_path):
        run_dir, captured, capture = self._capture_pipeline_kwargs(tmp_path)
        with patch("server.run_pipeline", side_effect=capture):
            client.post("/issue", json={
                "issue_url": "https://github.com/x/y/issues/1",
                "repo_url": "https://github.com/x/y",
            })
        assert captured.get("guidelines_path") is None

    def test_local_path_forwarded_to_pipeline(self, tmp_path):
        run_dir, captured, capture = self._capture_pipeline_kwargs(tmp_path)
        with patch("server.run_pipeline", side_effect=capture):
            client.post("/issue", json={
                "issue_url": "https://github.com/x/y/issues/1",
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
