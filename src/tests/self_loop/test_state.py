"""Tests for state persistence."""

import json
import os
import tempfile

import pytest

from self_loop.state import load_state, save_state, record_iteration


def test_load_state_missing_file():
    state = load_state("/nonexistent/STATE.json")
    assert state["total_iterations"] == 0
    assert state["seen_fingerprints"] == []
    assert state["iterations"] == []


def test_save_and_load_roundtrip(tmp_path):
    state_file = str(tmp_path / "STATE.json")
    state = load_state(state_file)
    state["total_iterations"] = 3
    state["total_cost_usd"] = 1.5
    save_state(state, state_file)

    loaded = load_state(state_file)
    assert loaded["total_iterations"] == 3
    assert loaded["total_cost_usd"] == 1.5


def test_record_iteration_updates_state(tmp_path):
    state_file = str(tmp_path / "STATE.json")
    state = load_state(state_file)

    record_iteration(
        state, 1,
        issue_url="https://github.com/owner/repo/issues/1",
        pr_url="https://github.com/owner/repo/pull/2",
        outcome="pass",
        reason="submitted",
        cost_usd=0.5,
        fingerprint="abc123",
    )

    assert state["total_iterations"] == 1
    assert state["total_cost_usd"] == 0.5
    assert "abc123" in state["seen_fingerprints"]
    assert len(state["iterations"]) == 1
    assert state["iterations"][0]["outcome"] == "pass"


def test_record_iteration_no_duplicate_fingerprints(tmp_path):
    state_file = str(tmp_path / "STATE.json")
    state = load_state(state_file)

    record_iteration(state, 1, None, None, "pass", "submitted", 0.5, "abc123")
    record_iteration(state, 2, None, None, "pass", "submitted", 0.5, "abc123")

    assert state["seen_fingerprints"].count("abc123") == 1


def test_load_state_corrupted_file(tmp_path):
    state_file = str(tmp_path / "STATE.json")
    with open(state_file, "w") as f:
        f.write("not valid json{{")

    state = load_state(state_file)
    assert state["total_iterations"] == 0
