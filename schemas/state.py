from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Step(str, Enum):
    setup = "setup"
    plan = "plan"
    execute = "execute"
    validate = "validate"
    test = "test"
    summary = "summary"
    report = "report"


class FailureSource(str, Enum):
    exec = "exec"
    validate = "validate"
    test = "test"
    ci = "ci"
    budget_exceeded = "budget_exceeded"
    unrecoverable = "unrecoverable"


class CIStatus(str, Enum):
    pass_ = "pass"
    fail = "fail"


DEFAULT_READ_ONLY = [
    "README.md",
    "CONTRIBUTING.md",
    "LICENSE",
    ".github/**",
    "*.lock",
    ".env*",
]


class PipelineState(BaseModel):
    repo_url: str
    local_dir: str  # resolved path to .agent/<hash>/ for this run
    issue_url: str
    issue_body: str = ""
    branch_name: str = ""
    loop_count: int = Field(default=0, ge=0)
    local_loop_count: int = Field(default=0, ge=0)
    current_step: Step = Step.setup
    plan_version: int = 0
    last_failure_reason: Optional[str] = None
    failure_source: Optional[FailureSource] = None
    ci_status: Optional[CIStatus] = None
    commit_sha: Optional[str] = None
    pr_url: Optional[str] = None
    cost_budget_usd: float = 2.00
    cost_spent_usd: float = 0.0
    read_only: list[str] = Field(default_factory=lambda: list(DEFAULT_READ_ONLY))
