"""Self-loop entry point: continuously improve the codebase."""

from __future__ import annotations

import subprocess
import sys

from self_loop.branch import (
    ensure_self_loop_branch,
    sync_self_loop_branch,
    auto_merge_pr,
    commit_state_to_branch,
)
from self_loop.budget import BudgetTracker
from self_loop.dedup import filter_candidates
from self_loop.github import list_open_issues, create_issue, wait_for_ci
from self_loop.pipeline import run_self_loop_pipeline
from self_loop.scanner import scan_codebase
from self_loop.schema.loop_config import SelfLoopConfig
from self_loop.schema.loop_state import LoopState
from self_loop.state import load_state, save_state, record_iteration
from tools.log import get_logger

log = get_logger(__name__)

_TERMINATION_CONSECUTIVE_FAILURES = 3


def self_loop_run(config: SelfLoopConfig) -> str:
    """Run the self-improvement loop. Returns termination reason."""
    repo_path = config["repo_local_path"]
    repo_url = config["repo_github_url"]
    branch = config["self_loop_branch"]
    state_file = config["state_file"]
    dry_run = config["dry_run"]

    budget = BudgetTracker(
        max_total_usd=config["max_total_budget_usd"],
        per_run_usd=config["per_run_budget_usd"],
    )

    # Ensure self-loop branch exists
    log.info(f"Ensuring branch {branch!r} exists")
    ensure_self_loop_branch(repo_path, branch)

    state: LoopState = load_state(state_file)
    budget.load(state["total_cost_usd"])

    consecutive_failures = 0
    termination_reason = "unknown"

    try:
        for iteration in range(1, config["max_iterations"] + 1):
            log.info(f"=== Self-loop iteration {iteration}/{config['max_iterations']} ===")

            # Budget check
            if not budget.can_afford_next_run():
                termination_reason = "budget_exhausted"
                log.info(f"Terminating: {termination_reason}")
                break

            # Sync branch to clean state
            sync_self_loop_branch(repo_path, branch)

            # Sanity check: make sure codebase is importable
            if not _sanity_check(repo_path):
                termination_reason = "codebase_broken"
                log.error(f"Terminating: {termination_reason}")
                break

            state = load_state(state_file)
            open_issues = list_open_issues(repo_url)
            open_issue_titles = [i["title"] for i in open_issues]

            # Scan for candidates
            log.info("Scanning codebase for improvements...")
            scan_result = scan_codebase(
                repo_path=repo_path,
                repo_github_url=repo_url,
                scanner_model=config["scanner_model"],
                open_issues=open_issues,
                cost_limit=1.0,
                step_limit=50,
            )

            # Filter candidates
            candidates = filter_candidates(
                candidates=scan_result["candidates"],
                seen_fingerprints=state["seen_fingerprints"],
                open_issue_titles=open_issue_titles,
                min_priority=config["min_issue_priority"],
            )

            if not candidates:
                log.info("No viable candidates found after filtering")
                record_iteration(
                    state, iteration, None, None,
                    "skipped", "no_candidates", scan_result["scan_cost_usd"],
                )
                save_state(state, state_file)

                if state["termination_reason"] is None:
                    consecutive_failures += 1
                    if consecutive_failures >= _TERMINATION_CONSECUTIVE_FAILURES:
                        termination_reason = "no_candidates"
                        break
                continue

            # Pick best candidate (already sorted by priority)
            best = candidates[0]
            log.info(f"Selected candidate: [{best['priority']}] {best['title']!r}")

            if dry_run:
                log.info("[dry-run] Would create issue and run pipeline. Skipping.")
                for c in candidates:
                    print(f"  [{c['priority']}] {c['title']}")
                    print(f"    category: {c['category']}")
                    print(f"    files: {', '.join(c['affected_files'])}")
                    print(f"    evidence: {c['evidence'][:120]}")
                    print()
                termination_reason = "dry_run_complete"
                break

            # Create GitHub issue
            issue_url = create_issue(
                repo_url=repo_url,
                title=best["title"],
                body=best["body"],
                labels=["self-loop"],
            )
            log.info(f"Created issue: {issue_url}")
            state["seen_fingerprints"].append(best["fingerprint"])

            # Run fix pipeline
            log.info("Running fix pipeline...")
            outcome, reason, pr_url = run_self_loop_pipeline(
                issue_url=issue_url,
                repo_local_path=repo_path,
                config=config,
            )
            log.info(f"Pipeline outcome: {outcome!r}, reason: {reason!r}")

            # After pipeline runs, sync back to self-loop branch
            sync_self_loop_branch(repo_path, branch)

            ci_status = "skipped"
            merged = False

            if outcome == "pass" and pr_url:
                # Wait for CI
                log.info(f"Waiting for CI on {pr_url}")
                ci_status = wait_for_ci(pr_url, repo_path)

                if ci_status == "pass":
                    merged = auto_merge_pr(pr_url, repo_path)
                    if merged:
                        sync_self_loop_branch(repo_path, branch)
                        consecutive_failures = 0
                    else:
                        log.warning("Auto-merge failed")
                        consecutive_failures += 1
                else:
                    log.warning(f"CI failed for {pr_url}; not merging")
                    consecutive_failures += 1
            else:
                consecutive_failures += 1

            record_iteration(
                state, iteration, issue_url, pr_url,
                outcome, reason, 0.0, best["fingerprint"],
            )
            save_state(state, state_file)
            commit_state_to_branch(repo_path, state_file, branch)

            if consecutive_failures >= _TERMINATION_CONSECUTIVE_FAILURES:
                termination_reason = "consecutive_failures"
                log.error(f"Terminating: {termination_reason}")
                break
        else:
            termination_reason = "max_iterations_reached"

    except KeyboardInterrupt:
        termination_reason = "interrupted"
        log.info("Interrupted by user")

    state["termination_reason"] = termination_reason
    save_state(state, state_file)
    log.info(f"Self-loop finished: {termination_reason}")
    return termination_reason


def _sanity_check(repo_path: str) -> bool:
    """Verify codebase is importable (import pipeline; import server)."""
    result = subprocess.run(
        [sys.executable, "-c", "import pipeline; import server"],
        cwd=repo_path + "/src" if not repo_path.endswith("/src") else repo_path,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.error(f"Sanity check failed: {result.stderr}")
        return False
    return True
