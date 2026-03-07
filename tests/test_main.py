import argparse
import sys
from unittest.mock import patch
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
    )

    _run_subcommand(args)

    mock_run_pipeline.assert_called_once_with(
        issue_url="https://github.com/owner/repo/issues/1",
        guidelines_path=str(guideline_path),
        local_path=str(tmp_path),
        model_name=None,
        max_steps=10,
    )


@patch("uvicorn.run")
def test_serve_subcommand(mock_run):
    args = argparse.Namespace(host="0.0.0.0", port=8080)
    _serve_subcommand(args)
    mock_run.assert_called_once_with(
        "server:app", host="0.0.0.0", port=8080, reload=False
    )


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
