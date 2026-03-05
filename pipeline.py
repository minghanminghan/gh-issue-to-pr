"""Pipeline orchestrator: sequences setup, mini-swe-agent, and report."""

from __future__ import annotations

import os
import sys
import platform
import logging
from typing import Any
from pathlib import Path
from datetime import datetime, timezone

from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.environments.extra.bubblewrap import BubblewrapEnvironment
from minisweagent.models.litellm_model import LitellmModel
from minisweagent.config import get_config_from_spec

from schema.config import AgentConfig
from tools.logger import log_event
from tools.setup import run_setup
from tools.trace import close_trace, add_span, Span


_OS_CONFIG_MAP = {
    "linux":   "mswea_config_bash.yaml",
    "darwin":  "mswea_config_bash.yaml",
    "windows": "mswea_config_powershell.yaml",
}


def _get_config_for_os() -> dict:
    os_name = platform.system().lower()
    config_file = _OS_CONFIG_MAP.get(os_name)
    if config_file is None:
        print(
            f"Error: unrecognized OS '{platform.system()}'. Supported: Linux, Darwin, Windows.",
            file=sys.stderr,
        )
        sys.exit(3)
    return get_config_from_spec(Path(config_file))


MODEL_NAME = os.getenv("MODEL_NAME")
if not MODEL_NAME:
    raise ValueError("MODEL_NAME environment variable not set")


log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

# Configure minisweagent loggers
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
logger = logging.getLogger("agent") # litellm_model
logger.setLevel(logging.INFO)
logger.addHandler(handler)


def run_pipeline(
    issue_url: str,
    guidelines_path: str | None,
    local_path: str | None,
    model_name: str | None,
    max_steps: int | None,
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

    # Step 0: Setup (deterministic, not an agent)
    run_dir = run_setup(issue_url, local_path=local_path)
    log_event(run_dir, "pipeline_start", {"issue_url": issue_url})

    agent_config = AgentConfig(model_name=model_name, max_steps=max_steps)
    outcome = "fail"
    try:
        outcome = _run_pipeline_steps(run_dir, guidelines, agent_config)
    except Exception as e:
        log_event(run_dir, "pipeline_exception", {"error": str(e)})
    finally:
        # Step 2: Report - always runs
        _run_report(run_dir, outcome, issue_url, agent_config)

    return run_dir


def _run_pipeline_steps(run_dir: Path, guidelines: str, agent_config: AgentConfig | dict[str, Any]) -> str:
    """Inner pipeline loop using mini-swe-agent. Returns 'pass' or 'fail'."""

    # Step 1: Execute agent
    log_event(run_dir, "step_start", {"step": "agent"})

    try:
        # 1. Initialize the environment pointing to the cloned repository
        if platform.system().lower() == "linux":
            env = BubblewrapEnvironment(cwd=str(run_dir))
        else:
            env = LocalEnvironment(cwd=str(run_dir))

        # 2. Load config and apply overrides
        config = _get_config_for_os()
        config.setdefault("environment", {})["cwd"] = str(run_dir)

        if agent_config.get("model_name") is not None:
            config.setdefault("model", {})["model_name"] = agent_config["model_name"]
        if agent_config.get("max_steps") is not None:
            config.setdefault("agent", {})["step_limit"] = agent_config["max_steps"]

        # 3. Initialize the model (prefer config model_name over env var)
        effective_model = config.get("model", {}).get("model_name", MODEL_NAME)
        model = LitellmModel(model_name=effective_model)
        

        agent_kwargs = config.get("agent", {})
        agent = DefaultAgent(model=model, env=env, **agent_kwargs)

        # 4. Construct the prompt
        issue_path = run_dir / "ISSUE.md"
        issue_content = issue_path.read_text(encoding="utf-8")
        
        prompt = [
            f"Here is an issue to resolve in this repository:\n\n{issue_content}",
            "\nPlease explore the repository, write code to fix the issue, and verify your changes by running tests.",
            "Once you are confident the issue is fixed, please commit the changes and run `gh pr create` to open a pull request.",
        ]
        
        if guidelines:
            prompt.insert(1, f"\nHere are the contribution guidelines to follow:\n{guidelines}")

        # 5. Run the agent
        result = agent.run("\n".join(prompt))
        log.debug(f"Agent run result: {result}")

        # Extract stats from serialized trajectory
        data = agent.serialize()
        model_stats = data["info"]["model_stats"]
        log.debug(f"Model stats: instance_cost={model_stats['instance_cost']}, api_calls={model_stats['api_calls']}")

        for idx, message in enumerate(data["messages"]):
            role = message.get("role")
            if role != "assistant":
                continue
            
            # extra = message.get("extra", {})
            # thought = message.get("content")
            # # edge case: thought might be stored in provider_specific_fields
            # # e.g. gemini: provider_specific_fields['thought_signature']

            # actions = extra.get("actions", [])
            # commands = [action.get("command") for action in actions if "command" in action]
            # usage = extra.get("usage", {})
            # # 'usage': {
            #     # 'completion_tokens': 42,
            #     # 'prompt_tokens': 8448,
            #     # 'total_tokens': 8490,
            #     # 'completion_tokens_details': {
            #         # 'reasoning_tokens': 11, 'text_tokens': 31
            #     # },
            #     # 'prompt_tokens_details': {
            #         # 'audio_tokens': None,
            #         # 'cached_tokens': None,
            #         # 'text_tokens': 8448,
            #         # 'image_tokens': None
            #     # },
            #     # 'cache_read_input_tokens': None
            # # }

            # ts = extra.get("timestamp")
            # ts_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""
            # add_span(run_dir, Span(
            #     message_number=idx,
            #     agent=effective_model,
            #     thought=thought,
            #     commands=commands,
            #     start_time=ts_iso,
            #     end_time=ts_iso,
            #     usage=usage,
            #     cost_usd=extra.get("cost", 0.0),
            #     tools_called=[a["command"] for a in extra.get("actions", [])],
            # ))
            add_span(run_dir, message)
        # Consider any run that doesn't explicitly crash as a pass for the pipeline outcome 
        # (observability tools will capture the specific SWE trajectory)
        return "pass"

    except Exception as e:
        log.error(f"Agent execution failed: {e}", exc_info=True)
        log_event(run_dir, "agent_failure", {"error": str(e)})
        return "fail"


def _run_report(run_dir: Path, outcome: str, issue_url: str, agent_config: AgentConfig) -> None:
    """
    Post-flight

    - outcome == "pass": closes trace, writes TRACE.json, exits zero
    - outcome == "fail": closes trace, writes TRACE.json, exits non-zero

    Always runs — even if earlier steps raised an exception.
    """
    run_dir = Path(run_dir)

    # Close trace (writes TRACE.json)
    close_trace(run_dir, outcome, issue_url, agent_config)

    if outcome == "failure":
        sys.exit(1)