import json
from pathlib import Path
from unittest.mock import MagicMock

from tools.trace import close_trace


def _make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / ".agent"
    run_dir.mkdir()
    return run_dir


def test_close_trace_writes_file(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    close_trace(run_dir, "pass", "https://github.com/owner/repo/issues/1", MagicMock())
    assert (run_dir / "TRACE.json").exists()


def test_close_trace_fields(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    close_trace(run_dir, "pass", "https://github.com/owner/repo/issues/1", MagicMock())
    data = json.loads((run_dir / "TRACE.json").read_text())
    assert data["outcome"] == "pass"
    assert data["issue_url"] == "https://github.com/owner/repo/issues/1"
    assert data["run_id"] == ".agent"
    assert "end_time" in data
    assert data["human_feedback"] is None


def test_close_trace_fail_outcome(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    close_trace(run_dir, "fail", "https://github.com/owner/repo/issues/1", MagicMock())
    data = json.loads((run_dir / "TRACE.json").read_text())
    assert data["outcome"] == "fail"
