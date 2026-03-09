"""Pipeline orchestrator: sequences setup, mini-swe-agent, and report."""

from __future__ import annotations

import json
import os
import shutil
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
from opentelemetry import trace as otel_trace

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


_DEFAULT_CI_RETRIES = 3
_DEFAULT_BUDGET_USD = 2.0

MODEL_NAME = os.getenv("MODEL_NAME")


def _env_int(key: str) -> int | None:
    val = os.getenv(key)
    if val is None:
        return None
    try:
        return int(val)
    except ValueError:
        log.warning(f"Invalid value for env var {key}={val!r}; ignoring")
        return None


def _env_float(key: str) -> float | None:
    val = os.getenv(key)
    if val is None:
        return None
    try:
        return float(val)
    except ValueError:
        log.warning(f"Invalid value for env var {key}={val!r}; ignoring")
        return None
if not MODEL_NAME:
    raise ValueError("MODEL_NAME environment variable not set")


def run_pipeline(
    issue_url: str,
    guidelines_path: str | None,
    local_path: str | None,
    model_name: str | None,
    max_steps: int | None,
    budget: float | None = None,
    model_api_key: str | None = None,
    model_endpoint: str | None = None,
    cache: bool = False,
    ci_retries: int | None = None,
) -> tuple[str, str]:
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

    issue = run_setup(issue_url, local_path=local_path)
    log.debug("Setup complete")

    # Resolve config precedence: cli/api value > env var > hardcoded default
    resolved_max_steps = max_steps if max_steps is not None else _env_int("MAX_STEPS")
    env_budget = _env_float("PIPELINE_BUDGET")
    resolved_budget = budget if budget is not None else (env_budget if env_budget is not None else _DEFAULT_BUDGET_USD)
    env_ci_retries = _env_int("CI_RETRIES")
    resolved_ci_retries = ci_retries if ci_retries is not None else (env_ci_retries if env_ci_retries is not None else _DEFAULT_CI_RETRIES)
    log.debug(f"Resolved config: max_steps={resolved_max_steps!r}, budget={resolved_budget!r}, ci_retries={resolved_ci_retries!r}")

    agent_config = AgentConfig(
        model_name=model_name,
        max_steps=resolved_max_steps,
        budget=resolved_budget,
        model_api_key=model_api_key,
        model_endpoint=model_endpoint,
        ci_retries=resolved_ci_retries,
    )
    log.debug(f"AgentConfig created: {dict(agent_config)}")
    max_ci_retries = resolved_ci_retries
    outcome = "fail"
    reason = "unknown"
    pr_url: str | None = None
    try:
        ci_feedback: str | None = None
        for ci_attempt in range(max_ci_retries + 1):
            if ci_attempt > 0:
                log.info(f"Re-running agent to fix CI failure (attempt {ci_attempt + 1}/{max_ci_retries + 1})")
            outcome, reason = _run_pipeline_steps(issue, guidelines, agent_config, ci_feedback=ci_feedback)
            log.debug(f"Pipeline steps finished: outcome={outcome!r}, reason={reason!r}")
            if outcome != "pass":
                break  # agent hit limits or errored; don't retry
            pr_url = _push_pr(issue, existing_pr_url=pr_url)
            log.debug(f"PR created/updated: {pr_url}")
            ci_passed, ci_feedback = _watch_ci(issue, pr_url)
            if ci_passed:
                break
            if ci_attempt < max_ci_retries:
                _post_pr_comment(pr_url, issue["dir"], f"CI failed — retrying with agent (attempt {ci_attempt + 2}/{max_ci_retries + 1}).")
            else:
                log.warning(f"CI still failing after {max_ci_retries} retries; giving up")
                _post_pr_comment(pr_url, issue["dir"], "CI is still failing after maximum retries. Manual review needed.")
    except Exception as e:
        log.debug(f"Pipeline raised exception: {e!r}")
    finally:
        _run_report(issue, outcome, agent_config)

    if outcome == "pass" and not cache and local_path is None:
        run_dir = issue["dir"]
        shutil.rmtree(run_dir, ignore_errors=True)
        log.info(f"Deleted run directory: {run_dir}")
    return outcome, reason


def _run_pipeline_steps(
    issue: Issue, guidelines: str, agent_config: AgentConfig | dict[str, Any],
    ci_feedback: str | None = None,
) -> tuple[str, str]:
    """Inner pipeline loop using mini-swe-agent. Returns (outcome, reason)."""

    # Step 1: Execute agent
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
        if agent_config.get("budget") is not None:
            config.setdefault("agent", {})["cost_limit"] = agent_config["budget"]
            log.debug(f"Override cost_limit={agent_config['budget']}")

        effective_model = config.get("model", {}).get("model_name", MODEL_NAME)
        log.debug(f"Effective model name: {effective_model!r}")
        model_kwargs: dict[str, Any] = {}
        if agent_config.get("model_api_key"):
            model_kwargs["api_key"] = agent_config["model_api_key"]
        if agent_config.get("model_endpoint"):
            model_kwargs["api_base"] = agent_config["model_endpoint"]
        model = LitellmModel(model_name=effective_model, model_kwargs=model_kwargs)
        log.debug("LitellmModel initialized")

        agent_kwargs = config.get("agent", {})
        agent = DefaultAgent(model=model, env=env, **agent_kwargs)

        issue_content = issue["desc"]

        prompt = [issue_content]

        if guidelines:
            log.debug("Injecting guidelines into prompt")
            prompt.append(f"\nContribution guidelines:\n{guidelines}")

        if ci_feedback:
            log.debug("Injecting CI failure feedback into prompt")
            prompt.append(
                f"\nThe previous changes you made caused CI to fail. "
                f"Fix the code to make CI pass. CI failure details:\n{ci_feedback}"
            )

        full_prompt = "\n".join(prompt)
        log.debug(f"Prompt assembled: {len(full_prompt)} chars total")

        tracer = otel_trace.get_tracer("gh-issue-to-pr")
        with tracer.start_as_current_span("agent.run") as span:
            span.set_attribute("run_id", issue['dir'].name)
            result = agent.run(full_prompt)
        log.debug(f"Agent run result: {result!r}")
        exit_status = result.get("exit_status", "unknown")
        if exit_status == "Submitted":
            return "pass", "submitted"
        elif exit_status == "LimitsExceeded":
            return "fail", "limits_exceeded"
        else:
            return "fail", exit_status.lower()

    except Exception as e:
        log.error(f"Agent execution failed: {e}", exc_info=True)
        log.debug(f"Agent fail details: type={type(e).__name__}, args={e.args!r}")
        # log_event(run_dir, "agent_fail", {"error": str(e)})
        return "fail", "exception"


def _push_pr(issue: Issue, existing_pr_url: str | None = None) -> str:
    repo_dir = issue["dir"]

    # On retry the agent may have amended commits, so use --force-with-lease
    push_cmd = ["git", "push", "origin", "HEAD"]
    if existing_pr_url:
        push_cmd.append("--force-with-lease")
    push = subprocess.run(push_cmd, cwd=repo_dir, capture_output=True, text=True)
    if push.returncode != 0:
        raise RuntimeError(f"git push failed: {push.stderr}")
    log.debug("Branch pushed")

    if existing_pr_url:
        log.debug(f"Updated existing PR: {existing_pr_url}")
        return existing_pr_url

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


def _watch_ci(issue: Issue, pr_url: str) -> tuple[bool, str]:
    """Watch CI and return (passed, failure_details)."""
    repo_dir = issue["dir"]
    log.info(f"Watching CI status for PR: {pr_url}")

    result = subprocess.run(
        ["gh", "pr", "checks", pr_url, "--watch"],
        cwd=repo_dir, capture_output=True, text=True,
    )

    if result.returncode == 0:
        log.info("CI passed!")
        return True, ""

    log.error("CI failed!")
    failure_details = _get_ci_failure_details(pr_url, repo_dir)
    return False, failure_details


def _get_ci_failure_details(pr_url: str, repo_dir: Path) -> str:
    """Return a summary of failed CI checks and their log output."""
    parts: list[str] = []

    # Failed check names
    checks_result = subprocess.run(
        ["gh", "pr", "checks", pr_url, "--json", "name,state"],
        cwd=repo_dir, capture_output=True, text=True,
    )
    if checks_result.returncode == 0:
        try:
            checks = json.loads(checks_result.stdout)
            failed = [
                c["name"] for c in checks
                if c.get("state") in ("FAILURE", "fail", "failure", "ERROR", "error")
            ]
            if failed:
                parts.append("Failed checks: " + ", ".join(failed))
        except (json.JSONDecodeError, KeyError):
            pass

    # Failed job log output via HEAD commit SHA
    sha_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir, capture_output=True, text=True,
    )
    if sha_result.returncode == 0:
        sha = sha_result.stdout.strip()
        run_list = subprocess.run(
            ["gh", "run", "list", "--commit", sha, "--limit", "1", "--json", "databaseId"],
            cwd=repo_dir, capture_output=True, text=True,
        )
        if run_list.returncode == 0:
            try:
                runs = json.loads(run_list.stdout)
                if runs:
                    run_id = runs[0]["databaseId"]
                    log_result = subprocess.run(
                        ["gh", "run", "view", str(run_id), "--log-failed"],
                        cwd=repo_dir, capture_output=True, text=True,
                    )
                    if log_result.returncode == 0 and log_result.stdout.strip():
                        output = log_result.stdout
                        if len(output) > 8000:
                            output = "...(truncated)...\n" + output[-8000:]
                        parts.append(f"Failed job logs:\n{output}")
            except (json.JSONDecodeError, KeyError, IndexError):
                pass

    return "\n\n".join(parts) if parts else "CI checks failed (no details available)"


def _run_report(
    issue: Issue, outcome: str, agent_config: AgentConfig
) -> None:
    run_dir = issue["dir"]
    close_trace(run_dir, outcome, issue["url"], agent_config)

    if outcome == "fail":
        sys.exit(1)
