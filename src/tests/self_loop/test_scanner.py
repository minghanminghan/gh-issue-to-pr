"""Tests for scanner module (mocked agent)."""

import json
from pathlib import Path

from self_loop.scanner import _format_open_issues, _parse_scan_output


def test_format_open_issues_empty():
    result = _format_open_issues([])
    assert result == "(none)"


def test_format_open_issues_with_issues():
    issues = [{"number": 1, "title": "Fix bug", "url": "https://github.com/o/r/issues/1"}]
    result = _format_open_issues(issues)
    assert "#1" in result
    assert "Fix bug" in result


def test_parse_scan_output_missing_file():
    result = _parse_scan_output("/nonexistent/scan_output.json")
    assert result == []


def test_parse_scan_output_valid(tmp_path):
    candidates = [
        {
            "title": "Add error handling to pipeline",
            "body": "## Problem\nMissing error handling",
            "category": "error_handling",
            "priority": "high",
            "affected_files": ["src/pipeline.py"],
            "fingerprint": "",
            "evidence": "src/pipeline.py:50: bare except",
        }
    ]
    output_file = str(tmp_path / "scan.json")
    Path(output_file).write_text(json.dumps(candidates))

    result = _parse_scan_output(output_file)
    assert len(result) == 1
    assert result[0]["title"] == "Add error handling to pipeline"
    assert result[0]["fingerprint"] != ""  # auto-computed


def test_parse_scan_output_malformed(tmp_path):
    output_file = str(tmp_path / "scan.json")
    Path(output_file).write_text("not json{{")
    result = _parse_scan_output(output_file)
    assert result == []


def test_parse_scan_output_not_list(tmp_path):
    output_file = str(tmp_path / "scan.json")
    Path(output_file).write_text(json.dumps({"error": "no candidates"}))
    result = _parse_scan_output(output_file)
    assert result == []


def test_parse_scan_output_title_truncated(tmp_path):
    long_title = "A" * 100
    candidates = [{
        "title": long_title,
        "body": "",
        "category": "docs",
        "priority": "low",
        "affected_files": [],
        "fingerprint": "",
        "evidence": "test",
    }]
    output_file = str(tmp_path / "scan.json")
    Path(output_file).write_text(json.dumps(candidates))
    result = _parse_scan_output(output_file)
    assert len(result[0]["title"]) <= 80
