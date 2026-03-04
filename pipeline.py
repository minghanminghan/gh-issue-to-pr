"""Pipeline orchestrator: sequences agents, manages loops, enforces stopping conditions."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agents.execute import run_execute_agent
from agents.plan import run_plan_agent
from agents.summary import run_summary_agent
from agents.test_agent import run_test_agent
from agents.validate import run_validate_agent
from schemas.state import FailureSource, Step
from tools.context import build_context_block
from tools.logger import log_event
from tools.report import run_report
from tools.setup import run_setup
from tools.state import read_state, write_state

_GLOBAL_LOOP_CAP = 3
_LOCAL_LOOP_CAP = 2


@dataclass
class StepResult:
    ok: bool
    failure_source: Optional[str] = None
    failure_reason: Optional[str] = None


def run_pipeline(
    repo_url: str,
    issue_url: str,
    guidelines_path: Optional[str] = None,
    local_path: Optional[str] = None,
) -> Path:
    """
    Main pipeline entry point.

    Steps:
      0 (setup) → 1 (plan) → 2 (execute) ↔ 3 (validate) → 4 (test) → 5 (summary) → 6 (report)

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
    run_dir = run_setup(repo_url, issue_url, local_path=local_path)
    log_event(run_dir, "pipeline_start", {"repo_url": repo_url, "issue_url": issue_url})

    outcome = "fail"
    try:
        outcome = _run_pipeline_steps(run_dir, guidelines)
    except SystemExit:
        # run_report may call sys.exit(1) — let it propagate after cleanup
        raise
    except Exception as e:
        state = read_state(run_dir)
        state.failure_source = FailureSource.unrecoverable
        state.last_failure_reason = f"Unhandled exception: {e}"
        write_state(run_dir, state)
        log_event(run_dir, "pipeline_exception", {"error": str(e)})
    finally:
        # Step 6: Report — always runs
        run_report(run_dir, outcome)

    return run_dir


def _run_pipeline_steps(run_dir: Path, guidelines: str) -> str:
    """Inner pipeline loop. Returns 'pass' or 'fail'."""
    state = read_state(run_dir)

    while True:
        state = read_state(run_dir)

        # Budget check before each agent call
        if state.cost_spent_usd >= state.cost_budget_usd:
            state.failure_source = FailureSource.budget_exceeded
            state.last_failure_reason = (
                f"Cost ${state.cost_spent_usd:.4f} exceeded budget ${state.cost_budget_usd:.2f}"
            )
            write_state(run_dir, state)
            log_event(run_dir, "budget_exceeded", {"cost": state.cost_spent_usd})
            return "fail"

        # Global loop cap
        if state.loop_count >= _GLOBAL_LOOP_CAP:
            state.failure_source = FailureSource.unrecoverable
            state.last_failure_reason = f"Reached global loop cap ({_GLOBAL_LOOP_CAP})"
            write_state(run_dir, state)
            log_event(run_dir, "global_loop_cap_reached")
            return "fail"

        # ---------------------------------------------------------------- #
        # Step 1: Plan
        # ---------------------------------------------------------------- #
        log_event(run_dir, "step_start", {"step": "plan", "loop": state.loop_count})
        context_block = build_context_block(state, run_dir) if state.loop_count > 0 else ""

        plan_result = run_plan_agent(run_dir, guidelines=guidelines, context_block=context_block)

        if not plan_result.ok:
            state = read_state(run_dir)
            state.failure_source = FailureSource.unrecoverable
            state.last_failure_reason = plan_result.failure_reason or "Plan agent failed"
            write_state(run_dir, state)
            return "fail"

        # ---------------------------------------------------------------- #
        # Steps 2+3: Execute ↔ Validate (local loop)
        # ---------------------------------------------------------------- #
        local_ok = _run_local_loop(run_dir)
        if not local_ok:
            state = read_state(run_dir)
            # local_ok=False means we should loop back to plan
            if state.failure_source in (FailureSource.unrecoverable, FailureSource.budget_exceeded):
                return "fail"
            # Global loop back
            state.loop_count += 1
            state.local_loop_count = 0
            state.current_step = Step.plan
            write_state(run_dir, state)
            log_event(run_dir, "global_loop_back", {"loop_count": state.loop_count})
            continue

        # ---------------------------------------------------------------- #
        # Step 4: Test
        # ---------------------------------------------------------------- #
        state = read_state(run_dir)
        if state.cost_spent_usd >= state.cost_budget_usd:
            state.failure_source = FailureSource.budget_exceeded
            state.last_failure_reason = "Budget exceeded before test step"
            write_state(run_dir, state)
            return "fail"

        log_event(run_dir, "step_start", {"step": "test"})
        test_result = run_test_agent(run_dir)

        if not test_result.ok:
            state = read_state(run_dir)
            fs = state.failure_source
            if fs == FailureSource.unrecoverable:
                return "fail"
            # Test failures loop back to plan
            state.loop_count += 1
            state.local_loop_count = 0
            state.current_step = Step.plan
            write_state(run_dir, state)
            log_event(run_dir, "global_loop_back", {"loop_count": state.loop_count, "reason": "test_failure"})
            continue

        # ---------------------------------------------------------------- #
        # Step 5: Summary
        # ---------------------------------------------------------------- #
        state = read_state(run_dir)
        if state.cost_spent_usd >= state.cost_budget_usd:
            state.failure_source = FailureSource.budget_exceeded
            state.last_failure_reason = "Budget exceeded before summary step"
            write_state(run_dir, state)
            return "fail"

        log_event(run_dir, "step_start", {"step": "summary"})
        summary_result = run_summary_agent(run_dir)

        if not summary_result.ok:
            state = read_state(run_dir)
            if state.failure_source == FailureSource.ci:
                # CI failure → global loop back (already incremented in summary agent)
                log_event(run_dir, "global_loop_back", {"reason": "ci_failure"})
                continue
            return "fail"

        # ---------------------------------------------------------------- #
        # All steps passed
        # ---------------------------------------------------------------- #
        state = read_state(run_dir)
        log_event(run_dir, "pipeline_complete", {"pr_url": state.pr_url})
        return "pass"


def _run_local_loop(run_dir: Path) -> bool:
    """
    Run the execute ↔ validate local loop.

    Returns True if validation passed, False if we must escalate to global loop.
    """
    state = read_state(run_dir)
    state.local_loop_count = 0
    write_state(run_dir, state)

    while True:
        state = read_state(run_dir)

        # Budget check
        if state.cost_spent_usd >= state.cost_budget_usd:
            state.failure_source = FailureSource.budget_exceeded
            state.last_failure_reason = "Budget exceeded in local execute/validate loop"
            write_state(run_dir, state)
            return False

        # ---------------------------------------------------------------- #
        # Step 2: Execute
        # ---------------------------------------------------------------- #
        context_block = ""
        if state.failure_source in (FailureSource.exec,) or str(state.failure_source) == "spec_deviation":
            context_block = build_context_block(state, run_dir)

        log_event(run_dir, "step_start", {"step": "execute", "local_loop": state.local_loop_count})
        execute_result = run_execute_agent(run_dir, context_block=context_block)

        if not execute_result.ok:
            state = read_state(run_dir)
            state.failure_source = FailureSource.unrecoverable
            state.last_failure_reason = execute_result.failure_reason or "Execute agent failed"
            write_state(run_dir, state)
            return False

        # ---------------------------------------------------------------- #
        # Step 3: Validate
        # ---------------------------------------------------------------- #
        log_event(run_dir, "step_start", {"step": "validate", "local_loop": state.local_loop_count})
        validate_result = run_validate_agent(run_dir)

        if validate_result.ok:
            log_event(run_dir, "validation_passed")
            return True

        # Validation failed — classify and decide routing
        state = read_state(run_dir)
        fs = state.failure_source

        if fs == FailureSource.unrecoverable:
            return False

        # Check if we can continue the local loop
        if state.local_loop_count < _LOCAL_LOOP_CAP - 1:
            state.local_loop_count += 1
            write_state(run_dir, state)
            log_event(run_dir, "local_loop_retry", {"local_loop_count": state.local_loop_count})

            # For plan_invalid failures, escalate immediately
            if fs == FailureSource.validate:
                return False

            # minor / spec_deviation: retry execute
            continue
        else:
            # Local loop cap reached — escalate
            log_event(run_dir, "local_loop_cap_reached", {"local_loop_count": state.local_loop_count})
            state.failure_source = FailureSource.validate
            state.last_failure_reason = (
                f"Local loop cap reached after {state.local_loop_count + 1} attempts: "
                f"{state.last_failure_reason}"
            )
            write_state(run_dir, state)
            return False
