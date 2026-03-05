import json
from pathlib import Path
from tools.logger import log_tool_call, log_event

def test_log_tool_call(tmp_path):
    log_tool_call(
        run_dir=tmp_path,
        agent="test-agent",
        tool="test-tool",
        args_summary="ls -la",
        ok=True,
        tokens_in=10,
        tokens_out=20,
        cost_usd=0.01
    )
    
    log_file = tmp_path / "RUN.log"
    assert log_file.exists()
    
    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    
    data = json.loads(lines[0])
    assert data["agent"] == "test-agent"
    assert data["tool"] == "test-tool"
    assert data["args_summary"] == "ls -la"
    assert data["ok"] is True
    assert data["tokens_in"] == 10
    assert data["tokens_out"] == 20
    assert data["cost_usd"] == 0.01
    assert "timestamp" in data

def test_log_event(tmp_path):
    log_event(
        run_dir=tmp_path,
        event="test-event",
        data={"key": "value"}
    )
    
    log_file = tmp_path / "RUN.log"
    assert log_file.exists()
    
    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    
    data = json.loads(lines[0])
    assert data["event"] == "test-event"
    assert data["key"] == "value"
    assert "timestamp" in data
