"""Pipeline orchestrator: sequences setup, mini-swe-agent, and report."""

from __future__ import annotations

import os
import sys
import platform
import subprocess
from typing import Any
from pathlib import Path

from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.environments.extra.bubblewrap import BubblewrapEnvironment
from minisweagent.models.litellm_model import LitellmModel
from minisweagent.config import get_config_from_spec

from schema.config import AgentConfig
from schema.issue import Issue
from tools.log import get_logger
from tools.setup import run_setup, _run_hash
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

    issue = run_setup(issue_url, local_path=local_path)
    log.debug("Setup complete")

    agent_config = AgentConfig(model_name=model_name, max_steps=max_steps)
    log.debug(f"AgentConfig created: {dict(agent_config)}")
    outcome = "fail"
    try:
        outcome = _run_pipeline_steps(issue, guidelines, agent_config)
        log.debug(f"Pipeline steps finished with outcome={outcome!r}")
        if outcome == "pass":
            pr_url = _push_pr(issue)
            log.debug(f"PR created/updated: {pr_url}")
            _watch_ci(issue, pr_url)
    except Exception as e:
        log.debug(f"Pipeline steps raised exception: {e!r}")
    finally:
        _run_report(issue, outcome, agent_config)


def _run_pipeline_steps(
    issue: Issue, guidelines: str, agent_config: AgentConfig | dict[str, Any]
) -> str:
    log.debug("_run_pipeline_steps")

    try:
        current_os = platform.system().lower()
        agent_cwd = issue['dir']
        log.debug(f"Initializing environment for OS={current_os!r}, cwd={agent_cwd}")
        if current_os == "linux":
            env = BubblewrapEnvironment(cwd=str(agent_cwd))
            log.debug(f"Using BubblewrapEnvironment with cwd={agent_cwd}")
        else:
            env = LocalEnvironment(cwd=str(agent_cwd))
            log.debug(f"Using LocalEnvironment with cwd={agent_cwd}")

        config = _get_config_for_os()
        config.setdefault("environment", {})["cwd"] = str(agent_cwd)
        log.debug(f"Config loaded; setting environment.cwd={issue['dir']}")

        if agent_config.get("model_name") is not None:
            config.setdefault("model", {})["model_name"] = agent_config["model_name"]
            log.debug(f"Override model_name={agent_config['model_name']!r}")
        if agent_config.get("max_steps") is not None:
            config.setdefault("agent", {})["step_limit"] = agent_config["max_steps"]
            log.debug(f"Override step_limit={agent_config['max_steps']}")

        effective_model = config.get("model", {}).get("model_name", MODEL_NAME)
        log.debug(f"Effective model name: {effective_model!r}")
        model = LitellmModel(
            model_name=effective_model,
        )
        log.debug("LitellmModel initialized")

        agent_kwargs = config.get("agent", {})
        agent = DefaultAgent(model=model, env=env, **agent_kwargs)

        issue_content = issue["desc"]

        prompt = [issue_content]

        if guidelines:
            log.debug("Injecting guidelines into prompt")
            prompt.append(f"\nContribution guidelines:\n{guidelines}")

        full_prompt = "\n".join(prompt)
        log.debug(f"Prompt assembled: {len(full_prompt)} chars total")

        from opentelemetry import trace as otel_trace
        tracer = otel_trace.get_tracer("gh-issue-to-pr")
        with tracer.start_as_current_span("agent.run") as span:
            span.set_attribute("run_id", issue['dir'].name)
            result = agent.run(full_prompt)
        log.debug(f"Agent run result: {result!r}")
        return "pass"

    except Exception as e:
        log.error(f"Agent execution failed: {e}", exc_info=True)
        return "fail"


def _push_pr(issue: Issue) -> str:
    repo_dir = issue["dir"]

    # Push branch
    push = subprocess.run(
        ["git", "push", "origin", "HEAD"],
        cwd=repo_dir, capture_output=True, text=True,
    )
    if push.returncode != 0:
        raise RuntimeError(f"git push failed: {push.stderr}")
    log.debug("Branch pushed")

    # If it is a PR, comment on it.
    if "/pull/" in issue["url"]:
        # Post comment
        comment = f"I have addressed the issue. The changes have been pushed to the branch `agent/{_run_hash(issue['url'])}`."
        _post_pr_comment(issue["url"], repo_dir, comment)
        return issue["url"]
    else:
        # Create new PR
        commit_title = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=repo_dir, capture_output=True, text=True,
        ).stdout.strip()

        pr = subprocess.run(
            ["gh", "pr", "create",
             "--title", "[agent] " + commit_title,
             "--body", f"Closes {issue['url']}"],
            cwd=repo_dir, capture_output=True, text=True,
        )
        if pr.returncode != 0:
            raise RuntimeError(f"gh pr create failed: {pr.stderr}")

        return pr.stdout.strip()


def _post_pr_comment(pr_url: str, repo_dir: Path, comment: str) -> None:
    pr = subprocess.run(
        ["gh", "pr", "comment", pr_url, "--body", comment],
        cwd=repo_dir, capture_output=True, text=True,
    )
    if pr.returncode != 0:
        log.error(f"gh pr comment failed: {pr.stderr}")


def _watch_ci(issue: Issue, pr_url: str) -> None:
    repo_dir = issue["dir"]
    log.info(f"Watching CI status for PR: {pr_url}")
    
    # Run checks and watch
    result = subprocess.run(
        ["gh", "pr", "checks", pr_url, "--watch"],
        cwd=repo_dir, capture_output=True, text=True
    )
    
    if result.returncode == 0:
        log.info("CI passed!")
    else:
        log.error("CI failed!")
        # Get failed checks
        failed_checks_result = subprocess.run(
            ["gh", "pr", "checks", pr_url, "--json", "name,state", "--jq", '.[] | select(.state=="fail") | .name'],
            cwd=repo_dir, capture_output=True, text=True
        )
        
        failed_checks = failed_checks_result.stdout.strip().split("\n")
        failed_checks = [c for c in failed_checks if c]
        
        if failed_checks:
            comment = f"The following CI checks failed: {', '.join(failed_checks)}. Please check the logs."
            _post_pr_comment(pr_url, repo_dir, comment)
        else:
            _post_pr_comment(pr_url, repo_dir, "CI failed (some checks might be canceled or failed). Please check the logs.")


def _run_report(
    issue: Issue, outcome: str, agent_config: AgentConfig
) -> None:
    run_dir = issue["dir"]
    close_trace(run_dir, outcome, issue["url"], agent_config)

    if outcome == "failure":
        sys.exit(1)
