import hashlib
import hmac
import json
import os

from fastapi.testclient import TestClient
from server import app, _jobs, _jobs_lock
from unittest.mock import patch

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}


@patch("server._run_pipeline_job")
def test_submit_issue(mock_run_job):
    # To avoid background thread actually running and failing,
    # we mock the background runner.

    response = client.post(
        "/issue",
        json={
            "issue_url": "https://github.com/owner/repo/issues/2",
            "repo_url": "https://github.com/owner/repo",
        },
    )

    assert response.status_code == 202
    data = response.json()
    assert data["issue_url"] == "https://github.com/owner/repo/issues/2"
    assert "/status?issue_url=" in data["status_url"]

    # Wait briefly or just check the mock
    import time

    time.sleep(0.1)  # give thread a moment to start
    mock_run_job.assert_called_once()


def test_get_status_not_found():
    response = client.get("/status?issue_url=https://not/found")
    assert response.status_code == 404


def test_get_status_found():
    with _jobs_lock:
        _jobs["https://found"] = {
            "status": "running",
            "run_dir": "/tmp/dir",
            "outcome": None,
            "error": None,
        }

    response = client.get("/status?issue_url=https://found")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"

    with _jobs_lock:
        del _jobs["https://found"]


# ---------------------------------------------------------------------------
# Webhook tests
# ---------------------------------------------------------------------------

_ISSUE_URL = "https://github.com/owner/repo/issues/42"
_REPO_URL = "https://github.com/owner/repo"

_LABELED_PAYLOAD = {
    "action": "labeled",
    "label": {"name": "agent"},
    "issue": {"html_url": _ISSUE_URL},
    "repository": {"html_url": _REPO_URL},
}

_OPENED_PAYLOAD = {
    "action": "opened",
    "issue": {"html_url": _ISSUE_URL},
    "repository": {"html_url": _REPO_URL},
}

_COMMENT_PAYLOAD = {
    "action": "created",
    "comment": {"body": "/fix the bug"},
    "issue": {"html_url": _ISSUE_URL},
    "repository": {"html_url": _REPO_URL},
}


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post_webhook(payload: dict, event: str, secret: str = "", env: dict | None = None):
    body = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": event}
    if secret:
        headers["X-Hub-Signature-256"] = _sign(body, secret)
    # Always clear WEBHOOK_SECRET unless the caller is explicitly testing signing
    merged = {"WEBHOOK_SECRET": "", **(env or {})}
    with patch.dict(os.environ, merged, clear=False):
        return client.post("/webhook/github", content=body, headers=headers)


@patch("server._run_pipeline_job")
def test_webhook_labeled_triggers(mock_run):
    with patch.dict(os.environ, {"WEBHOOK_LABEL": "agent", "WEBHOOK_ON_OPEN": "false", "WEBHOOK_ON_COMMENT": "false"}):
        resp = _post_webhook(_LABELED_PAYLOAD, "issues")
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    assert resp.json()["issue_url"] == _ISSUE_URL


@patch("server._run_pipeline_job")
def test_webhook_labeled_wrong_label_ignored(mock_run):
    resp = _post_webhook(_LABELED_PAYLOAD, "issues", env={"WEBHOOK_LABEL": "other"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


@patch("server._run_pipeline_job")
def test_webhook_opened_ignored_by_default(mock_run):
    resp = _post_webhook(_OPENED_PAYLOAD, "issues", env={"WEBHOOK_ON_OPEN": "false"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


@patch("server._run_pipeline_job")
def test_webhook_opened_triggers_when_enabled(mock_run):
    resp = _post_webhook(_OPENED_PAYLOAD, "issues", env={"WEBHOOK_ON_OPEN": "true", "WEBHOOK_LABEL": "agent"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


@patch("server._run_pipeline_job")
def test_webhook_comment_ignored_by_default(mock_run):
    resp = _post_webhook(_COMMENT_PAYLOAD, "issue_comment", env={"WEBHOOK_ON_COMMENT": "false"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


@patch("server._run_pipeline_job")
def test_webhook_comment_triggers_when_enabled(mock_run):
    resp = _post_webhook(_COMMENT_PAYLOAD, "issue_comment", env={"WEBHOOK_ON_COMMENT": "true"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


@patch("server._run_pipeline_job")
def test_webhook_comment_wrong_prefix_ignored(mock_run):
    payload = {**_COMMENT_PAYLOAD, "comment": {"body": "not a fix command"}}
    resp = _post_webhook(payload, "issue_comment", env={"WEBHOOK_ON_COMMENT": "true"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_webhook_invalid_signature_rejected():
    secret = "correct-secret"
    body = json.dumps(_LABELED_PAYLOAD).encode()
    bad_sig = "sha256=badhash"
    with patch.dict(os.environ, {"WEBHOOK_SECRET": secret}):
        resp = client.post(
            "/webhook/github",
            content=body,
            headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": bad_sig},
        )
    assert resp.status_code == 403


@patch("server._run_pipeline_job")
def test_webhook_valid_signature_accepted(mock_run):
    secret = "my-secret"
    resp = _post_webhook(
        _LABELED_PAYLOAD, "issues",
        secret=secret,
        env={"WEBHOOK_SECRET": secret, "WEBHOOK_LABEL": "agent"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
