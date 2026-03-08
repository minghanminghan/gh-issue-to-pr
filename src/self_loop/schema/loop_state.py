"""LoopState and IterationRecord TypedDicts."""

from __future__ import annotations

from typing import TypedDict


class IterationRecord(TypedDict):
    iteration: int
    issue_url: str | None
    pr_url: str | None
    outcome: str          # "pass"|"fail"|"skipped"
    reason: str
    cost_usd: float
    timestamp: str        # ISO-8601


class LoopState(TypedDict):
    total_iterations: int
    total_cost_usd: float
    seen_fingerprints: list[str]
    iterations: list[IterationRecord]
    termination_reason: str | None
