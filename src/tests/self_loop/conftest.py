"""Test configuration: set required env vars before any imports."""

import os

os.environ.setdefault("MODEL_NAME", "test-model")
os.environ.setdefault("GITHUB_TOKEN", "test-token")
