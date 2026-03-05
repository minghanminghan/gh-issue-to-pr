import json
from unittest.mock import patch, MagicMock
from pathlib import Path
import pytest
from datetime import datetime, timezone

from tools.trace import open_trace, add_span, close_trace, Span, _open_traces

def test_open_trace(tmp_path):
    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    
    open_trace(run_dir)
    key = str(run_dir)
    assert key in _open_traces
    assert _open_traces[key]["run_id"] == "run1"
    assert "start_time" in _open_traces[key]
    assert _open_traces[key]["spans"] == []
    
    # cleanup
    del _open_traces[key]

def test_add_span(tmp_path):
    run_dir = tmp_path / "run2"
    
    span = Span(
        agent="test-agent",
        start_time="2026-03-05T00:00:00Z",
        end_time="2026-03-05T00:01:00Z",
        tokens_in=10,
        tokens_out=20,
        cost_usd=0.01,
        tools_called=["test_tool"]
    )
    
    add_span(run_dir, span)
    key = str(run_dir)
    assert key in _open_traces
    assert len(_open_traces[key]["spans"]) == 1
    assert _open_traces[key]["spans"][0]["agent"] == "test-agent"
    
    # cleanup
    del _open_traces[key]

@patch("tools.trace._export_to_phoenix")
def test_close_trace(mock_export, tmp_path):
    run_dir = tmp_path / "run3"
    run_dir.mkdir()
    
    state_file = run_dir / "STATE.json"
    state_file.write_text(json.dumps({"issue_url": "test-url", "loop_count": 2}))
    
    span = Span(
        agent="test-agent",
        start_time="2026-03-05T00:00:00Z",
        end_time="2026-03-05T00:01:00Z",
        tokens_in=100,
        tokens_out=200,
        cost_usd=1.5,
        tools_called=["test_tool"]
    )
    add_span(run_dir, span)
    
    with patch.dict("os.environ", {"PHOENIX_COLLECTOR_ENDPOINT": "http://localhost:4318"}):
        close_trace(run_dir, "pass")
        
    key = str(run_dir)
    assert key not in _open_traces
    
    trace_file = run_dir / "TRACE.json"
    assert trace_file.exists()
    
    trace_data = json.loads(trace_file.read_text())
    assert trace_data["issue_url"] == "test-url"
    assert trace_data["outcome"] == "pass"
    assert trace_data["total_tokens_in"] == 100
    assert trace_data["total_tokens_out"] == 200
    assert trace_data["total_cost_usd"] == 1.5
    assert trace_data["loop_count"] == 2
    
    mock_export.assert_called_once()
    assert mock_export.call_args[0][1] == "http://localhost:4318"
