"""Pipeline orchestrator: sequences setup, mini-swe-agent, and report."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.litellm_model import LitellmModel
from minisweagent.config import get_config_from_spec

# from schema.state import Step
from tools.logger import log_event, log_tool_call
from tools.setup import run_setup
from tools.trace import close_trace, add_span, Span
import logging


MODEL_NAME = os.getenv("MODEL_NAME")
if not MODEL_NAME:
    raise ValueError("MODEL_NAME environment variable not set")


def run_pipeline(
    issue_url: str,
    guidelines_path: str | None = None,
    local_path: str | None = None,
    config_path: str | None = None,
    max_steps: int | None = None,
) -> Path:
    """
    Main pipeline entry point.

    Steps:
      0 (setup) -> 1 (mini-swe-agent) -> 2 (report)

    Returns run_dir.
    """
    guidelines = ""
    if guidelines_path:
        try:
            guidelines = Path(guidelines_path).read_text(encoding="utf-8")
        except Exception as e:
            print(f"Warning: could not read guidelines file: {e}", file=sys.stderr)

    # ------------------------------------------------------------------ #
    # Step 0: Setup (deterministic, not an agent)
    # ------------------------------------------------------------------ #
    run_dir = run_setup(issue_url, local_path=local_path, config_path=config_path, max_steps=max_steps)
    log_event(run_dir, "pipeline_start", {"issue_url": issue_url})

    outcome = "fail"
    try:
        outcome = _run_pipeline_steps(run_dir, guidelines, config_path)
    except Exception as e:
        log_event(run_dir, "pipeline_exception", {"error": str(e)})
    finally:
        # Step 2: Report - always runs
        _run_report(run_dir, outcome)

    return run_dir


class AgentTrackingHandler(logging.Handler):
    """
    Hooks into mini-swe-agent's logger to record spans and tool calls
    for the pipeline's observability trace.
    """
    def __init__(self, run_dir: Path):
        super().__init__()
        self.run_dir = run_dir

    def emit(self, record):
        # mini-swe-agent emits lists of message dicts to agent.logger.debug
        if not isinstance(record.msg, (list, tuple)):
            return
            
        for msg in record.msg:
            if not isinstance(msg, dict):
                continue
                
            role = msg.get("role")
            extra = msg.get("extra", {})
            
            # An assistant message contains model execution stats and planned actions
            if role == "assistant" and "cost" in extra:
                actions = extra.get("actions", [])
                tools_called = [a.get("command", "unknown") for a in actions]
                
                span = Span(
                    agent="mini-swe-agent",
                    start_time=extra.get("timestamp", 0),  # In real implementation we'd format this
                    end_time=extra.get("timestamp", 0),    # LitellmModel doesn't track start/end explicitly in msg
                    tokens_in=extra.get("usage", {}).get("prompt_tokens", 0),
                    tokens_out=extra.get("usage", {}).get("completion_tokens", 0),
                    cost_usd=extra.get("cost", 0.0),
                    tools_called=tools_called,
                )
                
                # Format timestamps
                from datetime import datetime, timezone
                now_iso = datetime.now(timezone.utc).isoformat()
                span.start_time = now_iso
                span.end_time = now_iso
                
                add_span(self.run_dir, span)
                
            # A tool message contains the result of an action
            elif role == "tool" and "raw_output" in extra:
                log_tool_call(
                    run_dir=self.run_dir,
                    agent="mini-swe-agent",
                    tool="bash",
                    args_summary="bash command execution", # We'd need to extract original command, simplified for now
                    ok=extra.get("returncode", 0) == 0,
                )


def _run_pipeline_steps(run_dir: Path, guidelines: str, config_path: str | None = None) -> str:
    """Inner pipeline loop using mini-swe-agent. Returns 'pass' or 'fail'."""
    repo_root = run_dir.parent.parent

    # ---------------------------------------------------------------- #
    # Step 1: Execute agent
    # ---------------------------------------------------------------- #
    log_event(run_dir, "step_start", {"step": "agent"})

    try:
        # 1. Initialize the environment pointing to the cloned repository
        env = LocalEnvironment(repo_path=str(repo_root))

        # 2. Initialize the model (using config or default)
        model = LitellmModel(model_name=MODEL_NAME)

        # 3. Instantiate the agent using config
        config_file = run_dir / "config.yaml"
        if config_file.exists():
            config = get_config_from_spec(str(config_file))
        elif config_path:
            config = get_config_from_spec(config_path)
        else:
            config = get_config_from_spec("default")
            
        agent_kwargs = config.get("agent", {})
        agent = DefaultAgent(model=model, env=env, **agent_kwargs)

        # Hook up the tracking handler to the agent's logger
        handler = AgentTrackingHandler(run_dir)
        agent.logger.addHandler(handler)
        agent.logger.setLevel(logging.DEBUG)

        # 4. Construct the prompt
        issue_path = run_dir / "ISSUE.md"
        issue_content = issue_path.read_text(encoding="utf-8")
        
        # TODO: pass additional context to the agent (budget, max steps, etc. from args)
        prompt = [
            f"Here is an issue to resolve in this repository:\n\n{issue_content}",
            "\nPlease explore the repository, write code to fix the issue, and verify your changes by running tests.",
            "Once you are confident the issue is fixed, please commit the changes and run `gh pr create` to open a pull request.",
        ]
        
        if guidelines:
            prompt.insert(1, f"\nHere are the contribution guidelines to follow:\n{guidelines}")

        # 5. Run the agent
        print(f"Running mini-swe-agent with model {MODEL_NAME}...")
        # (Assuming run() takes a string description or prompt - standard for swe-agent patterns)
        # Based on snippet from readme: agent.run("Write a sudoku game")
        agent.run("\n".join(prompt))
        
        # Consider any run that doesn't explicitly crash as a pass for the pipeline outcome 
        # (observability tools will capture the specific SWE trajectory)
        return "pass"

    except Exception as e:
        log_event(run_dir, "agent_failure", {"error": str(e)})
        return "fail"


def _run_report(run_dir: Path, outcome: str) -> None:
    """
    Post-flight

    - outcome == "pass": closes trace, writes TRACE.json, exits zero
    - outcome == "fail": closes trace, writes TRACE.json, exits non-zero

    Always runs — even if earlier steps raised an exception.
    """
    run_dir = Path(run_dir)

    # Close trace (writes TRACE.json)
    close_trace(run_dir, outcome)

    if outcome == "failure":
        sys.exit(1)