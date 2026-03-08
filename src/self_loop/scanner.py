"""Scanner: use audit agent to find improvement candidates in the codebase."""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path

from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.litellm_model import LitellmModel
from minisweagent.config import get_config_from_spec

from self_loop.dedup import compute_fingerprint
from self_loop.schema.scan_result import IssueCandidate, ScanResult
from tools.log import get_logger

log = get_logger(__name__)


def scan_codebase(
    repo_path: str,
    repo_github_url: str,
    scanner_model: str,
    open_issues: list[dict],
    cost_limit: float = 1.0,
    step_limit: int = 50,
) -> ScanResult:
    """Run the audit agent and return structured scan results."""
    start = time.monotonic()

    todos = _scan_todos(repo_path)
    git_log = _get_git_log(repo_path)
    open_issues_text = _format_open_issues(open_issues)

    scan_output_path = f"/tmp/scan_{uuid.uuid4().hex}.json"

    config_path = Path(__file__).parent.parent / "mswea_config_audit.yaml"
    config = get_config_from_spec(config_path)

    # Override model and limits
    config.setdefault("model", {})["model_name"] = scanner_model
    config.setdefault("agent", {})["step_limit"] = step_limit
    config.setdefault("agent", {})["cost_limit"] = cost_limit
    config.setdefault("environment", {})["cwd"] = repo_path

    # Inject pre-seeded context into instance_template
    agent_cfg = config.get("agent", {})
    instance_template: str = agent_cfg.get("instance_template", "")
    instance_template = instance_template.replace("{{todos}}", todos)
    instance_template = instance_template.replace("{{open_issues}}", open_issues_text)
    instance_template = instance_template.replace("{{git_log}}", git_log)
    instance_template = instance_template.replace("{{scan_output_path}}", scan_output_path)
    agent_cfg["instance_template"] = instance_template
    config["agent"] = agent_cfg

    env = LocalEnvironment(cwd=repo_path)
    model = LitellmModel(model_name=scanner_model)
    agent = DefaultAgent(model=model, env=env, **{k: v for k, v in agent_cfg.items()
                                                   if k not in ("system_template", "instance_template",
                                                                "step_limit", "cost_limit")})
    # Re-apply limits
    agent.step_limit = step_limit
    agent.cost_limit = cost_limit

    full_prompt = instance_template
    try:
        agent.run(full_prompt)
    except Exception as e:
        log.warning(f"Scanner agent raised exception: {e}")

    # Check for accidental source modifications
    diff = subprocess.run(
        ["git", "diff", "--stat"],
        cwd=repo_path, capture_output=True, text=True,
    )
    if diff.stdout.strip():
        log.warning("Scanner modified source files! Reverting...")
        subprocess.run(["git", "checkout", "--", "."], cwd=repo_path)

    candidates = _parse_scan_output(scan_output_path)
    duration = time.monotonic() - start

    return ScanResult(
        candidates=candidates,
        scan_cost_usd=0.0,  # cost tracking via litellm callbacks if configured
        scan_duration_s=round(duration, 2),
    )


def _scan_todos(repo_path: str) -> str:
    """Grep for TODO/FIXME/HACK/XXX in src/."""
    result = subprocess.run(
        ["grep", "-rn", r"TODO\|FIXME\|HACK\|XXX", "src/"],
        cwd=repo_path, capture_output=True, text=True,
    )
    return result.stdout[:3000] if result.stdout else "(none found)"


def _get_git_log(repo_path: str) -> str:
    result = subprocess.run(
        ["git", "log", "--oneline", "-20"],
        cwd=repo_path, capture_output=True, text=True,
    )
    return result.stdout.strip() or "(no commits)"


def _format_open_issues(open_issues: list[dict]) -> str:
    if not open_issues:
        return "(none)"
    lines = []
    for issue in open_issues:
        lines.append(f"- #{issue.get('number', '?')}: {issue.get('title', '')} ({issue.get('url', '')})")
    return "\n".join(lines)


def _parse_scan_output(scan_output_path: str) -> list[IssueCandidate]:
    """Read the JSON output file written by the scanner agent."""
    path = Path(scan_output_path)
    if not path.exists():
        log.warning(f"Scanner output file not found: {scan_output_path}")
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            log.warning(f"Scanner output is not a list: {type(data)}")
            return []
        candidates: list[IssueCandidate] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            # Ensure required fields
            candidate = IssueCandidate(
                title=str(item.get("title", ""))[:80],
                body=str(item.get("body", "")),
                category=str(item.get("category", "code_quality")),
                priority=str(item.get("priority", "low")),
                affected_files=item.get("affected_files", []),
                fingerprint=str(item.get("fingerprint", "")),
                evidence=str(item.get("evidence", "")),
            )
            # Compute fingerprint if not set
            if not candidate["fingerprint"]:
                candidate["fingerprint"] = compute_fingerprint(candidate)
            candidates.append(candidate)
        log.info(f"Scanner produced {len(candidates)} candidates")
        return candidates
    except Exception as e:
        log.warning(f"Failed to parse scanner output: {e}")
        return []
    finally:
        # Clean up temp file
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
