"""ScanResult and IssueCandidate TypedDicts."""

from __future__ import annotations

from typing import TypedDict


class IssueCandidate(TypedDict):
    title: str               # Max 80 chars, starts with a verb
    body: str                # GitHub issue markdown body
    category: str            # "test_coverage"|"failing_test"|"error_handling"|
                             # "todo_fixme"|"performance"|"code_quality"|"docs"
    priority: str            # "critical"|"high"|"medium"|"low"
    affected_files: list[str]
    fingerprint: str         # SHA256[:12] of category+files+title_norm
    evidence: str            # Quote/snippet justifying the issue


class ScanResult(TypedDict):
    candidates: list[IssueCandidate]
    scan_cost_usd: float
    scan_duration_s: float
