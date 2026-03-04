"""LLM-as-judge scorer for individual agent outputs (Phase 5)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypedDict

import litellm

MODEL = os.environ.get("LLM_MODEL", "anthropic/claude-opus-4-6")
_, _PROVIDER, _, _ = litellm.get_llm_provider(MODEL)

_RUBRICS: dict[str, list[str]] = {
    "plan": [
        "All referenced files in FILES.md actually exist in the repository.",
        "Each step in PLAN.md is unambiguous and actionable.",
        "Each step includes a runnable bash verification command.",
    ],
    "execute": [
        "All FILES.md entries were addressed in the implementation.",
        "CHANGES.md justifications match each edit made.",
        "No files outside FILES.md were modified.",
    ],
    "validate": [
        "All PLAN.md verification steps were actually run.",
        "Each pass/fail verdict has concrete evidence.",
        "The failure classification is correct and consistent with the evidence.",
    ],
    "test": [
        "No existing test files were modified without explicit justification in TEST.md.",
        "New tests are syntactically valid Python.",
        "All tests pass after any additions.",
    ],
    "summary": [
        "The PR description accurately reflects the changes in CHANGES.md.",
        "The PR is linked to the original issue.",
        "The CI status is correctly reported.",
    ],
}


class EvalResult(TypedDict):
    agent: str
    scores: list[int]  # 0 or 1 per rubric criterion
    overall: float  # 0.0 – 1.0
    justifications: list[str]


def evaluate_agent(agent_name: str, run_dir: Path) -> EvalResult:
    """
    Run LLM-as-judge evaluation for a single agent output.

    Reads the relevant markdown artifacts from run_dir and scores against
    the rubric criteria for the given agent.
    """
    run_dir = Path(run_dir)
    rubric = _RUBRICS.get(agent_name)
    if rubric is None:
        raise ValueError(f"No rubric defined for agent '{agent_name}'")

    artifacts = _load_artifacts(agent_name, run_dir)
    prompt = _build_eval_prompt(agent_name, rubric, artifacts)

    kwargs = dict(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    if _PROVIDER == "anthropic":
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": 2048}

    response = litellm.completion(**kwargs)
    text = response.choices[0].message.content or ""
    return _parse_eval_response(agent_name, rubric, text)


def _load_artifacts(agent_name: str, run_dir: Path) -> str:
    artifact_map = {
        "plan": ["ISSUE.md", "PLAN.md", "FILES.md"],
        "execute": ["PLAN.md", "FILES.md", "CHANGES.md"],
        "validate": ["PLAN.md", "CHANGES.md", "VALIDATE.md"],
        "test": ["FILES.md", "TEST.md"],
        "summary": ["CHANGES.md", "TEST.md", "SUMMARY.md"],
    }
    files = artifact_map.get(agent_name, [])
    parts = []
    for fname in files:
        path = run_dir / fname
        if path.exists():
            parts.append(f"### {fname}\n\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def _build_eval_prompt(agent_name: str, rubric: list[str], artifacts: str) -> str:
    criteria = "\n".join(f"{i+1}. {c}" for i, c in enumerate(rubric))
    return f"""You are evaluating the output of the '{agent_name}' agent in an automated
GitHub issue → pull request pipeline.

## Evaluation criteria (score each 0 or 1)

{criteria}

## Agent artifacts

{artifacts}

## Instructions

For each criterion, output a JSON object with this exact structure:
{{
  "scores": [0_or_1, ...],  // one per criterion in order
  "justifications": ["reason1", ...]  // one per criterion
}}

Output ONLY the JSON object, nothing else.
"""


def _parse_eval_response(agent_name: str, rubric: list[str], text: str) -> EvalResult:
    try:
        # Extract JSON from the response
        start = text.find("{")
        end = text.rfind("}") + 1
        data = json.loads(text[start:end])
        scores = [int(bool(s)) for s in data.get("scores", [])]
        justifications = data.get("justifications", [])
        # Pad if needed
        while len(scores) < len(rubric):
            scores.append(0)
        while len(justifications) < len(rubric):
            justifications.append("")
    except Exception:
        scores = [0] * len(rubric)
        justifications = [f"Failed to parse eval response: {text[:200]}"] * len(rubric)

    overall = sum(scores) / len(scores) if scores else 0.0
    return EvalResult(
        agent=agent_name,
        scores=scores,
        overall=overall,
        justifications=justifications,
    )
