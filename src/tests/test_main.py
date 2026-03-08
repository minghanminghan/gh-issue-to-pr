import argparse
import os
import sys
from unittest.mock import MagicMock, patch
import pytest

from main import _run_subcommand, _serve_subcommand, main


def test_run_subcommand_invalid_url():
    args = argparse.Namespace(
        issue_url="invalid_url",
        local_path=None,
        guidelines=None,
        model_name=None,
        max_steps=None,
        budget=2.0,
    )
    with pytest.raises(SystemExit) as exc:
        _run_subcommand(args)
    assert exc.value.code == 2


def test_run_subcommand_invalid_local_path():
    args = argparse.Namespace(
        issue_url="https://github.com/owner/repo/issues/1",
        local_path="invalid/path",
        guidelines=None,
        model_name=None,
        max_steps=None,
        budget=2.0,
    )
    with pytest.raises(SystemExit) as exc:
        _run_subcommand(args)
    assert exc.value.code == 2


def test_run_subcommand_invalid_guidelines(tmp_path):
    args = argparse.Namespace(
        issue_url="https://github.com/owner/repo/issues/1",
        local_path=str(tmp_path),
        guidelines="nonexistent_guidelines.md",
        model_name=None,
        max_steps=None,
        budget=2.0,
    )
    with pytest.raises(SystemExit) as exc:
        _run_subcommand(args)
    assert exc.value.code == 2


@patch("main.run_pipeline")
def test_run_subcommand_success(mock_run_pipeline, tmp_path):
    mock_run_pipeline.return_value = tmp_path / "run_dir"

    guideline_path = tmp_path / "repo" / "CONTRIBUTING.md"
    guideline_path.parent.mkdir()
    guideline_path.touch()

    args = argparse.Namespace(
        issue_url="https://github.com/owner/repo/issues/1",
        local_path=str(tmp_path),
        guidelines=str(guideline_path),
        model_name=None,
        max_steps=10,
        budget=5.0,
        cache=False,
    )

    _run_subcommand(args)

    mock_run_pipeline.assert_called_once_with(
        issue_url="https://github.com/owner/repo/issues/1",
        guidelines_path=str(guideline_path),
        local_path=str(tmp_path),
        model_name=None,
        max_steps=10,
        cache=False,
    )


@patch("uvicorn.run")
def test_serve_subcommand_plain(mock_uvicorn):
    args = argparse.Namespace(host="0.0.0.0", port=8080, repo_url=None)
    _serve_subcommand(args)
    mock_uvicorn.assert_called_once_with("server:app", host="0.0.0.0", port=8080, reload=False)


def test_serve_subcommand_webhook_invalid_url():
    args = argparse.Namespace(
        host="127.0.0.1", port=8080,
        repo_url="not-a-github-url",
        label="agent", on_open=False, on_comment=False,
    )
    with pytest.raises(SystemExit) as exc:
        _serve_subcommand(args)
    assert exc.value.code == 2


def test_serve_subcommand_webhook_missing_secret(monkeypatch):
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    args = argparse.Namespace(
        host="127.0.0.1", port=8080,
        repo_url="https://github.com/owner/repo",
        label="agent", on_open=False, on_comment=False,
    )
    with pytest.raises(SystemExit) as exc:
        _serve_subcommand(args)
    assert exc.value.code == 2


@patch("uvicorn.run")
@patch("subprocess.run")
@patch("subprocess.Popen")
@patch("urllib.request.urlopen")
def test_serve_subcommand_webhook_mode(
    mock_urlopen, mock_popen, mock_subprocess_run, mock_uvicorn, monkeypatch
):
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")

    # Simulate ngrok tunnel response
    import json
    tunnel_data = json.dumps({"tunnels": [{"proto": "https", "public_url": "https://abc.ngrok.io"}]}).encode()
    mock_urlopen.return_value.__enter__ = lambda s: s
    mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value.read = lambda: tunnel_data

    # Simulate successful gh webhook registration
    hook_response = json.dumps({"id": 42}).encode().decode()
    mock_subprocess_run.return_value = MagicMock(returncode=0, stdout=hook_response, stderr="")

    mock_popen.return_value = MagicMock()

    args = argparse.Namespace(
        host="127.0.0.1", port=8080,
        repo_url="https://github.com/owner/repo",
        label="agent", on_open=False, on_comment=True,
    )
    _serve_subcommand(args)

    mock_uvicorn.assert_called_once_with("server:app", host="127.0.0.1", port=8080, reload=False)
    assert os.environ.get("WEBHOOK_LABEL") == "agent"
    assert os.environ.get("WEBHOOK_ON_OPEN") == "false"
    assert os.environ.get("WEBHOOK_ON_COMMENT") == "true"


@patch("main._run_subcommand")
def test_main_run(mock_run_subcommand):
    test_args = [
        "main.py",
        "run",
        "https://github.com/owner/repo/issues/1",
        "--budget",
        "5.0",
    ]
    with patch.object(sys, "argv", test_args):
        main()
        mock_run_subcommand.assert_called_once()
        args = mock_run_subcommand.call_args[0][0]
        assert args.issue_url == "https://github.com/owner/repo/issues/1"
        assert args.budget == 5.0
