"""Tests for dedup module."""

import pytest
from self_loop.dedup import (
    compute_fingerprint,
    is_duplicate_fingerprint,
    is_similar_title,
    filter_candidates,
)
from self_loop.schema.scan_result import IssueCandidate


def _make_candidate(
    title="Add error handling to foo",
    category="error_handling",
    priority="high",
    files=None,
    fingerprint="",
    evidence="src/foo.py:42: bare except",
) -> IssueCandidate:
    return IssueCandidate(
        title=title,
        body="## Problem\nMissing error handling",
        category=category,
        priority=priority,
        affected_files=files or ["src/foo.py"],
        fingerprint=fingerprint,
        evidence=evidence,
    )


def test_compute_fingerprint_deterministic():
    c = _make_candidate()
    fp1 = compute_fingerprint(c)
    fp2 = compute_fingerprint(c)
    assert fp1 == fp2
    assert len(fp1) == 12


def test_compute_fingerprint_differs_by_category():
    c1 = _make_candidate(category="error_handling")
    c2 = _make_candidate(category="test_coverage")
    assert compute_fingerprint(c1) != compute_fingerprint(c2)


def test_compute_fingerprint_sorts_files():
    c1 = _make_candidate(files=["src/a.py", "src/b.py"])
    c2 = _make_candidate(files=["src/b.py", "src/a.py"])
    assert compute_fingerprint(c1) == compute_fingerprint(c2)


def test_is_duplicate_fingerprint():
    assert is_duplicate_fingerprint("abc123", ["abc123", "def456"])
    assert not is_duplicate_fingerprint("xyz789", ["abc123", "def456"])


def test_is_similar_title_exact():
    assert is_similar_title("Add error handling", ["Add error handling"])


def test_is_similar_title_close():
    assert is_similar_title("Add error handling to foo", ["Add error handling in foo"])


def test_is_similar_title_different():
    assert not is_similar_title("Improve test coverage", ["Add error handling in foo"])


def test_filter_candidates_removes_low_priority():
    candidates = [
        _make_candidate(priority="low", title="Improve docs"),
        _make_candidate(priority="high", title="Fix error handling"),
    ]
    result = filter_candidates(candidates, [], [], min_priority="medium")
    assert len(result) == 1
    assert result[0]["priority"] == "high"


def test_filter_candidates_removes_duplicates():
    c = _make_candidate()
    fp = compute_fingerprint(c)
    c["fingerprint"] = fp
    result = filter_candidates([c], seen_fingerprints=[fp], open_issue_titles=[], min_priority="low")
    assert result == []


def test_filter_candidates_removes_similar_titles():
    c = _make_candidate(title="Add error handling to pipeline")
    result = filter_candidates(
        [c],
        seen_fingerprints=[],
        open_issue_titles=["Add error handling in pipeline"],
        min_priority="low",
    )
    assert result == []


def test_filter_candidates_sorted_by_priority():
    candidates = [
        _make_candidate(title="Low priority", priority="low"),
        _make_candidate(title="Critical issue", priority="critical", category="failing_test"),
        _make_candidate(title="High priority", priority="high", category="test_coverage"),
    ]
    result = filter_candidates(candidates, [], [], min_priority="low")
    priorities = [r["priority"] for r in result]
    assert priorities == ["critical", "high", "low"]


def test_filter_candidates_sets_fingerprint():
    c = _make_candidate(fingerprint="")
    result = filter_candidates([c], [], [], min_priority="low")
    assert len(result) == 1
    assert result[0]["fingerprint"] != ""
