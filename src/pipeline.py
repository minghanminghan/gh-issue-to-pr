"""Pipeline orchestrator: sequences setup, mini-swe-agent, and report."""

from __future__ import annotations

import os
import sys
import platform
import subprocess
from typing import Any
from pathlib import Path

from opentelemetry import trace as otel_trace

from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.environments.extra.bubblewrap import BubblewrapEnvironment
from minisweagent.models.litellm_model import LitellmModel
from minisweagent.config import get_config_from_spec

from schema.config import AgentConfig
from schema.issue import Issue
from tools.log import get_logger
from tools.setup import run_setup
from tools.trace import close_trace


log = get_logger(__name__)


_OS_CONFIG_MAP = {
    "linux": "mswea_config_bash.yaml",
    "darwin": "mswea_config_bash.yaml",
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
    return get_config_from_spec(Path(__file__).parent / config_file)


MODEL_NAME = os.getenv("MODEL_NAME")
if not MODEL_NAME:
    raise ValueError("MODEL_NAME environment variable not set")


def run_pipeline(
    issue_url: str,
    guidelines_path: str | None,
    local_path: str | None,
    model_name: str | None,
    max_steps: int | None,
) -> None:
    """
    Main pipeline entry point.

    Steps:
      0 (setup) -> 1 (mini-swe-agent) -> 2 (report)

    Returns run_dir.
    """
    log.debug(
        f"run_pipeline called: issue_url={issue_url!r}, guidelines_path={guidelines_path!r}, local_path={local_path!r}, model_name={model_name!r}, max_steps={max_steps!r}"
    )

    guidelines = ""
    if guidelines_path:
        log.debug(f"Reading guidelines from: {guidelines_path}")
        try:
            guidelines = Path(guidelines_path).read_text(encoding="utf-8")
            log.debug(f"Guidelines loaded: {len(guidelines)} chars")
        except Exception as e:
            log.error(f"Warning: could not read guidelines file: {e}")
    else:
        log.debug("No guidelines path provided; skipping guidelines")

    # Step 0: Setup (deterministic, not an agent)
    issue = run_setup(issue_url, local_path=local_path)
    log.debug("Setup complete")
    # log_event(run_dir, "pipeline_start", {"issue_url": issue_url})

    agent_config = AgentConfig(model_name=model_name, max_steps=max_steps)
    log.debug(f"AgentConfig created: {dict(agent_config)}")
    outcome = "fail"
    try:
        outcome = _run_pipeline_steps(issue, guidelines, agent_config)
        log.debug(f"Pipeline steps finished with outcome={outcome!r}")
        if outcome == "pass":
            pr_url = _push_pr(issue)
            log.debug(f"PR created: {pr_url}")
    except Exception as e:
        log.debug(f"Pipeline steps raised exception: {e!r}")
        # log_event(run_dir, "pipeline_exception", {"error": str(e)})
    finally:
        # Step 2: Report - always runs
        _run_report(issue, outcome, agent_config)


def _run_pipeline_steps(
    issue: Issue, guidelines: str, agent_config: AgentConfig | dict[str, Any]
) -> str:
    """Inner pipeline loop using mini-swe-agent. Returns 'pass' or 'fail'."""

    # Step 1: Execute agent
    log.debug("_run_pipeline_steps")

    try:
        # 1. Initialize the environment pointing to the cloned repository
        current_os = platform.system().lower()
        agent_cwd = issue['dir']        # run/<hash>/ — the repo the agent works in
        log.debug(f"Initializing environment for OS={current_os!r}, cwd={agent_cwd}")
        if current_os == "linux":
            env = BubblewrapEnvironment(cwd=str(agent_cwd))
            log.debug(f"Using BubblewrapEnvironment with cwd={agent_cwd}")
        else:
            env = LocalEnvironment(cwd=str(agent_cwd))
            log.debug(f"Using LocalEnvironment with cwd={agent_cwd}")

        # 2. Load config and apply overrides
        config = _get_config_for_os()
        config.setdefault("environment", {})["cwd"] = str(agent_cwd)
        log.debug(f"Config loaded; setting environment.cwd={issue['dir']}")

        if agent_config.get("model_name") is not None:
            config.setdefault("model", {})["model_name"] = agent_config["model_name"]
            log.debug(f"Override model_name={agent_config['model_name']!r}")
        if agent_config.get("max_steps") is not None:
            config.setdefault("agent", {})["step_limit"] = agent_config["max_steps"]
            log.debug(f"Override step_limit={agent_config['max_steps']}")

        # 3. Initialize the model (prefer config model_name over env var)
        effective_model = config.get("model", {}).get("model_name", MODEL_NAME)
        log.debug(f"Effective model name: {effective_model!r}")
        model = LitellmModel(
            model_name=effective_model,
        )
        log.debug("LitellmModel initialized")

        agent_kwargs = config.get("agent", {})
        # log.debug(f"DefaultAgent kwargs: {agent_kwargs}")
        agent = DefaultAgent(model=model, env=env, **agent_kwargs)

        # 4. Construct the prompt
        issue_content = issue["desc"]

        prompt = [issue_content]

        if guidelines:
            log.debug("Injecting guidelines into prompt")
            prompt.append(f"\nContribution guidelines:\n{guidelines}")

        full_prompt = "\n".join(prompt)
        log.debug(f"Prompt assembled: {len(full_prompt)} chars total")

        # 5. Run the agent inside a parent span so all LiteLLM child spans.
        tracer = otel_trace.get_tracer("gh-issue-to-pr")
        with tracer.start_as_current_span("agent.run") as span:
            span.set_attribute("run_id", issue['dir'].name)
            result = agent.run(full_prompt)
        log.debug(f"Agent run result: {result!r}")
        return "pass"

    except Exception as e:
        log.error(f"Agent execution failed: {e}", exc_info=True)
        log.debug(f"Agent failure details: type={type(e).__name__}, args={e.args!r}")
        # log_event(run_dir, "agent_failure", {"error": str(e)})
        return "fail"


def _push_pr(issue: Issue) -> str:
    """Push branch and open PR. Runs outside the sandbox; returns PR URL."""
    repo_dir = issue["dir"]

    push = subprocess.run(
        ["git", "push", "origin", "HEAD"],
        cwd=repo_dir, capture_output=True, text=True,
    )
    if push.returncode != 0:
        raise RuntimeError(f"git push failed: {push.stderr}")
    log.debug("Branch pushed")

    commit_title = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=repo_dir, capture_output=True, text=True,
    ).stdout.strip()

    pr = subprocess.run(
        ["gh", "pr", "create",
         "--title", "[agent] " + commit_title,      # agent-generated PR
         "--body", f"Closes {issue['url']}"],
        cwd=repo_dir, capture_output=True, text=True,
    )
    if pr.returncode != 0:
        raise RuntimeError(f"gh pr create failed: {pr.stderr}")

    return pr.stdout.strip()


def _run_report(
    issue: Issue, outcome: str, agent_config: AgentConfig
) -> None:
    """
    Post-flight

    - outcome == "pass": closes trace, writes TRACE.json, exits zero
    - outcome == "fail": closes trace, writes TRACE.json, exits non-zero

    Always runs — even if earlier steps raised an exception.
    """
    run_dir = issue["dir"]
    log.debug(
        f"_run_report: run_dir={run_dir}, outcome={outcome!r}, issue_url={issue['url']}"
    )

    # Close trace (writes TRACE.json)
    log.debug("Closing trace (writing TRACE.json)")
    close_trace(run_dir, outcome, issue["url"], agent_config)
    log.debug(f"Trace closed; TRACE.json written to {run_dir / 'TRACE.json'}")

    if outcome == "failure":
        log.debug("Outcome is 'failure'; exiting with code 1")
        sys.exit(1)
