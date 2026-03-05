import json
import logging
from unittest.mock import patch, MagicMock
from pathlib import Path
import pytest
import os

from pipeline import _run_report, run_pipeline, AgentTrackingHandler, _run_pipeline_steps


def _make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / ".agent" / "abc12345"
    run_dir.mkdir(parents=True)
    return run_dir


class TestRunReport:
    def test_pass_outcome_writes_trace_json(self, tmp_path):
        run_dir = _make_run_dir(tmp_path)
        _run_report(run_dir, "pass")
        assert (run_dir / "TRACE.json").exists()

    def test_fail_outcome_exits_early(self, tmp_path):
        run_dir = _make_run_dir(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            _run_report(run_dir, "failure")
        assert exc_info.value.code == 1

    def test_trace_json_outcome_pass(self, tmp_path):
        run_dir = _make_run_dir(tmp_path)
        _run_report(run_dir, "pass")
        trace = json.loads((run_dir / "TRACE.json").read_text())
        assert trace["outcome"] == "pass"

    def test_trace_json_outcome_fail(self, tmp_path):
        run_dir = _make_run_dir(tmp_path)
        with pytest.raises(SystemExit):
            _run_report(run_dir, "failure")
        trace = json.loads((run_dir / "TRACE.json").read_text())
        assert trace["outcome"] == "failure"


class TestAgentTrackingHandler:
    @patch("pipeline.add_span")
    def test_emit_assistant_msg(self, mock_add_span, tmp_path):
        run_dir = _make_run_dir(tmp_path)
        handler = AgentTrackingHandler(run_dir)
        
        record = logging.LogRecord(
            name="test", level=logging.DEBUG, pathname="", lineno=0,
            msg=[{
                "role": "assistant",
                "extra": {
                    "cost": 0.05,
                    "timestamp": "old_timestamp",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                    "actions": [{"command": "ls"}]
                }
            }],
            args=(), exc_info=None
        )
        handler.emit(record)
        mock_add_span.assert_called_once()
        span_arg = mock_add_span.call_args[0][1]
        assert span_arg.agent == "mini-swe-agent"
        assert span_arg.tokens_in == 10
        assert span_arg.tokens_out == 20
        assert span_arg.cost_usd == 0.05
        assert span_arg.tools_called == ["ls"]

    @patch("pipeline.log_tool_call")
    def test_emit_tool_msg(self, mock_log_tool_call, tmp_path):
        run_dir = _make_run_dir(tmp_path)
        handler = AgentTrackingHandler(run_dir)
        
        record = logging.LogRecord(
            name="test", level=logging.DEBUG, pathname="", lineno=0,
            msg=[{
                "role": "tool",
                "extra": {
                    "raw_output": "success",
                    "returncode": 0
                }
            }],
            args=(), exc_info=None
        )
        handler.emit(record)
        mock_log_tool_call.assert_called_once()
        kwargs = mock_log_tool_call.call_args[1]
        assert kwargs["run_dir"] == run_dir
        assert kwargs["agent"] == "mini-swe-agent"
        assert kwargs["tool"] == "bash"
        assert kwargs["ok"] is True


@patch("pipeline.run_setup")
@patch("pipeline._run_pipeline_steps")
@patch("pipeline._run_report")
@patch("pipeline.log_event")
def test_run_pipeline(mock_log, mock_run_report, mock_run_steps, mock_run_setup, tmp_path):
    run_dir = _make_run_dir(tmp_path)
    mock_run_setup.return_value = run_dir
    mock_run_steps.return_value = "pass"
    
    guideline_path = tmp_path / "CONTRIBUTING.md"
    guideline_path.write_text("be nice")
    
    result = run_pipeline(
        issue_url="http://github",
        guidelines_path=str(guideline_path),
        local_path=str(tmp_path)
    )
    
    assert result == run_dir
    mock_run_setup.assert_called_once_with(
        "http://github", local_path=str(tmp_path), config_path=None, max_steps=None
    )
    mock_run_steps.assert_called_once_with(run_dir, "be nice", None)
    mock_run_report.assert_called_once_with(run_dir, "pass")


@patch("pipeline.log_event")
@patch("pipeline.LocalEnvironment")
@patch("pipeline.LitellmModel")
@patch("pipeline.get_config_from_spec")
@patch("pipeline.DefaultAgent")
def test_run_pipeline_steps(mock_agent_cls, mock_get_config, mock_model, mock_env, mock_log_event, tmp_path):
    run_dir = _make_run_dir(tmp_path)
    
    (run_dir / "ISSUE.md").write_text("fix bug")
    
    mock_config = {"agent": {"some_arg": "value"}}
    mock_get_config.return_value = mock_config
    
    mock_agent = MagicMock()
    mock_agent_cls.return_value = mock_agent
    
    res = _run_pipeline_steps(run_dir, guidelines="guidelines text", config_path=None)
    
    assert res == "pass"
    mock_agent.run.assert_called_once()
    assert "guidelines text" in mock_agent.run.call_args[0][0]
    assert "fix bug" in mock_agent.run.call_args[0][0]

