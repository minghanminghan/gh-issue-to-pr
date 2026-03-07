import json
from tools.log import log_tool_call


def test_log_tool_call(tmp_path):
    log_tool_call(
        run_dir=tmp_path,
        agent="test-agent",
        tool="test-tool",
        args_summary="ls -la",
        ok=True,
        tokens_in=10,
        tokens_out=20,
        cost_usd=0.01,
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
