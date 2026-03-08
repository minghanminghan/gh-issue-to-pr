"""Deduplication: fingerprinting + title-similarity checks."""

from __future__ import annotations

import difflib
import hashlib
import re

from self_loop.schema.scan_result import IssueCandidate
from tools.log import get_logger

log = get_logger(__name__)

_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def compute_fingerprint(candidate: IssueCandidate) -> str:
    """Compute SHA256[:12] of category + sorted_files + normalized_title."""
    title_norm = re.sub(r"\W+", " ", candidate["title"].lower()).strip()
    sorted_files = "|".join(sorted(candidate["affected_files"]))
    raw = f"{candidate['category']}|{sorted_files}|{title_norm}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def is_duplicate_fingerprint(fingerprint: str, seen: list[str]) -> bool:
    return fingerprint in seen


def is_similar_title(title: str, existing_titles: list[str], threshold: float = 0.70) -> bool:
    """Return True if title is too similar to any existing title."""
    title_lower = title.lower()
    for existing in existing_titles:
        ratio = difflib.SequenceMatcher(None, title_lower, existing.lower()).ratio()
        if ratio > threshold:
            log.debug(f"Title similarity {ratio:.2f} > {threshold}: {title!r} ~ {existing!r}")
            return True
    return False


def filter_candidates(
    candidates: list[IssueCandidate],
    seen_fingerprints: list[str],
    open_issue_titles: list[str],
    min_priority: str = "low",
) -> list[IssueCandidate]:
    """Filter candidates by fingerprint, title similarity, and minimum priority."""
    min_rank = _PRIORITY_RANK.get(min_priority, 3)
    filtered: list[IssueCandidate] = []

    for c in candidates:
        fp = c.get("fingerprint") or compute_fingerprint(c)
        c["fingerprint"] = fp

        rank = _PRIORITY_RANK.get(c["priority"], 3)
        if rank > min_rank:
            log.debug(f"Skipping low-priority candidate: {c['title']!r} ({c['priority']})")
            continue

        if is_duplicate_fingerprint(fp, seen_fingerprints):
            log.debug(f"Skipping duplicate fingerprint {fp}: {c['title']!r}")
            continue

        if is_similar_title(c["title"], open_issue_titles):
            log.debug(f"Skipping similar title: {c['title']!r}")
            continue

        filtered.append(c)

    # Sort by priority (critical first)
    filtered.sort(key=lambda x: _PRIORITY_RANK.get(x["priority"], 3))
    return filtered
