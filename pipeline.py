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
    log.debug(f"Detected OS: {os_name!r}")
    config_file = _OS_CONFIG_MAP.get(os_name)
    if config_file is None:
        print(
            f"Error: unrecognized OS '{platform.system()}'. Supported: Linux, Darwin, Windows.",
            file=sys.stderr,
        )
        sys.exit(3)
    log.debug(f"Loading OS config from: {config_file}")
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
    log.debug(f"run_pipeline called: issue_url={issue_url!r}, guidelines_path={guidelines_path!r}, local_path={local_path!r}, model_name={model_name!r}, max_steps={max_steps!r}")

    guidelines = ""
    if guidelines_path:
        log.debug(f"Reading guidelines from: {guidelines_path}")
        try:
            guidelines = Path(guidelines_path).read_text(encoding="utf-8")
            log.debug(f"Guidelines loaded: {len(guidelines)} chars")
        except Exception as e:
            print(f"Warning: could not read guidelines file: {e}", file=sys.stderr)
    else:
        log.debug("No guidelines path provided; skipping guidelines")

    # Step 0: Setup (deterministic, not an agent)
    log.debug("Starting setup step (run_setup)")
    run_dir = run_setup(issue_url, local_path=local_path)
    log.debug(f"Setup complete; run_dir={run_dir}")
    log_event(run_dir, "pipeline_start", {"issue_url": issue_url})

    agent_config = AgentConfig(model_name=model_name, max_steps=max_steps)
    log.debug(f"AgentConfig created: {dict(agent_config)}")
    outcome = "fail"
    try:
        log.debug("Starting pipeline steps (_run_pipeline_steps)")
        outcome = _run_pipeline_steps(run_dir, guidelines, agent_config)
        log.debug(f"Pipeline steps finished with outcome={outcome!r}")
    except Exception as e:
        log.debug(f"Pipeline steps raised exception: {e!r}")
        log_event(run_dir, "pipeline_exception", {"error": str(e)})
    finally:
        # Step 2: Report - always runs
        log.debug(f"Running report step with outcome={outcome!r}")
        _run_report(run_dir, outcome, issue_url, agent_config)

    return run_dir


def _run_pipeline_steps(run_dir: Path, guidelines: str, agent_config: AgentConfig | dict[str, Any]) -> str:
    """Inner pipeline loop using mini-swe-agent. Returns 'pass' or 'fail'."""

    # Step 1: Execute agent
    log.debug(f"_run_pipeline_steps: run_dir={run_dir}, guidelines_len={len(guidelines)}, agent_config={dict(agent_config)}")
    log_event(run_dir, "step_start", {"step": "agent"})

    try:
        # 1. Initialize the environment pointing to the cloned repository
        current_os = platform.system().lower()
        log.debug(f"Initializing environment for OS={current_os!r}, cwd={run_dir}")
        if current_os == "linux":
            env = BubblewrapEnvironment(cwd=str(run_dir))
            log.debug("Using BubblewrapEnvironment (Linux sandbox)")
        else:
            env = LocalEnvironment(cwd=str(run_dir))
            log.debug("Using LocalEnvironment")

        # 2. Load config and apply overrides
        config = _get_config_for_os()
        config.setdefault("environment", {})["cwd"] = str(run_dir)
        log.debug(f"Config loaded; setting environment.cwd={run_dir}")

        if agent_config.get("model_name") is not None:
            config.setdefault("model", {})["model_name"] = agent_config["model_name"]
            log.debug(f"Override model_name={agent_config['model_name']!r}")
        if agent_config.get("max_steps") is not None:
            config.setdefault("agent", {})["step_limit"] = agent_config["max_steps"]
            log.debug(f"Override step_limit={agent_config['max_steps']}")

        # 3. Initialize the model (prefer config model_name over env var)
        effective_model = config.get("model", {}).get("model_name", MODEL_NAME)
        log.debug(f"Effective model name: {effective_model!r}")
        model = LitellmModel(model_name=effective_model)
        log.debug("LitellmModel initialized")

        agent_kwargs = config.get("agent", {})
        log.debug(f"DefaultAgent kwargs: {agent_kwargs}")
        agent = DefaultAgent(model=model, env=env, **agent_kwargs)
        log.debug("DefaultAgent initialized")

        # 4. Construct the prompt
        issue_path = run_dir / "ISSUE.md"
        log.debug(f"Reading issue from: {issue_path}")
        issue_content = issue_path.read_text(encoding="utf-8")
        log.debug(f"Issue content loaded: {len(issue_content)} chars")

        prompt = [
            f"Here is an issue to resolve in this repository:\n\n{issue_content}",
            "\nPlease explore the repository, write code to fix the issue, and verify your changes by running tests.",
            "Once you are confident the issue is fixed, please commit the changes and run `gh pr create` to open a pull request.",
        ]

        if guidelines:
            log.debug("Injecting guidelines into prompt")
            prompt.insert(1, f"\nHere are the contribution guidelines to follow:\n{guidelines}")

        full_prompt = "\n".join(prompt)
        log.debug(f"Prompt assembled: {len(full_prompt)} chars total")

        # 5. Run the agent
        log.debug("Invoking agent.run(prompt)")
        result = agent.run(full_prompt)
        log.debug(f"Agent run result: {result}")

        log.debug(f"agent.run() returned: {result!r}")

        # Extract stats from serialized trajectory
        log.debug("Serializing agent trajectory")
        data = agent.serialize()
        model_stats = data["info"]["model_stats"]
        log.debug(f"Model stats: instance_cost={model_stats['instance_cost']}, api_calls={model_stats['api_calls']}, total_tokens={model_stats.get('total_tokens')}")

        n_messages = len(data["messages"])
        log.debug(f"Processing {n_messages} messages from trajectory")
        for idx, message in enumerate(data["messages"]):
            role = message.get("role")
            log.debug(f"Message [{idx}/{n_messages}] role={role!r}")
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
            log.debug(f"Adding span for assistant message [{idx}]")
            add_span(run_dir, message)
        # Consider any run that doesn't explicitly crash as a pass for the pipeline outcome
        # (observability tools will capture the specific SWE trajectory)
        log.debug("Agent completed successfully; returning 'pass'")
        return "pass"

    except Exception as e:
        log.error(f"Agent execution failed: {e}", exc_info=True)
        log.debug(f"Agent failure details: type={type(e).__name__}, args={e.args!r}")
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
    log.debug(f"_run_report: run_dir={run_dir}, outcome={outcome!r}, issue_url={issue_url!r}")

    # Close trace (writes TRACE.json)
    log.debug("Closing trace (writing TRACE.json)")
    close_trace(run_dir, outcome, issue_url, agent_config)
    log.debug(f"Trace closed; TRACE.json written to {run_dir / 'TRACE.json'}")

    if outcome == "failure":
        log.debug("Outcome is 'failure'; exiting with code 1")
        sys.exit(1)