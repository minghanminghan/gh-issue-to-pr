from fastapi.testclient import TestClient
from server import app, _jobs, _jobs_lock
import pytest

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}

from unittest.mock import patch

@patch("server._run_pipeline_job")
def test_submit_issue(mock_run_job):
    # To avoid background thread actually running and failing, 
    # we mock the background runner.
    
    response = client.post("/issue", json={
        "issue_url": "https://github.com/owner/repo/issues/2",
        "repo_url": "https://github.com/owner/repo"
    })
    
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
            "error": None
        }
    
    response = client.get("/status?issue_url=https://found")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    
    with _jobs_lock:
        del _jobs["https://found"]
