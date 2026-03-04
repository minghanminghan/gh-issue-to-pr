# Concerns and Mitigations

This document tracks the operational concerns for the gh-issue-to-pr pipeline and their current mitigation status.

## Execution Safety

**Concern:** The Execute agent can write arbitrary files to the repository.

**Mitigation:**
- Writes are scoped to files listed in FILES.md (checked before each write)
- `read_only` list in STATE.json prevents modification of sensitive files
- The `execute_cli` allowlist restricts shell commands to a safe set
- Shell metacharacters (`;`, `|`, `&`, `$(`, backtick) are rejected before execution

**Current status:** Partial mitigation. FILES.md scoping enforced at tool level; execute_cli is allowlisted but not fully sandboxed (no container isolation).

---

## Cost Runaway

**Concern:** Multi-loop runs on large repos can consume significant API budget.

**Mitigation:**
- `cost_budget_usd` cap in STATE.json (default $2.00)
- Budget is checked before every agent call
- Budget exhaustion routes to Report step (writes FAILURE.md with `budget_exceeded`)
- Cost is tracked per-span in TRACE.json for post-run analysis

**Current status:** Soft guardrail. The cap prevents runaway costs but does not account for mid-agent cost spikes before a call completes.

---

## GitHub Rate Limits

**Concern:** Issue fetch and CI polling hit the GitHub API.

**Mitigation:**
- Uses `gh` CLI (handles auth, rate limiting, and retry internally)
- CI polling is done via `gh pr checks` which respects rate limits

**Current status:** Delegated to gh CLI. No additional retry logic beyond gh's built-in handling.

---

## Branch Conflicts

**Concern:** If `agent/<hash>` already exists from a prior failed run, Setup must decide whether to reset it or fail loudly.

**Mitigation:**
- Setup checks for existing branch via `git branch --list <branch_name>`
- If branch already exists, raises `RuntimeError` with a clear error message
- User must manually delete the branch or use a different issue URL

**Current status:** Fails loudly. No automatic branch recovery; manual intervention required.

---

## Secrets Exposure

**Concern:** `.env*` and credential files could be accidentally modified.

**Mitigation:**
- Default `read_only` list includes `.env*`, `.github/**`, and `*.lock`
- execute_cli allowlist prevents running arbitrary shell commands
- `.gitignore` entry for `.agent/` prevents run artifacts from being committed

**Current status:** Best-effort protection. The allowlist reduces but does not eliminate risk from clever prompt injection.

---

## Test Suite Absence

**Concern:** Some repos have no tests. The Test agent must handle this gracefully.

**Mitigation:**
- Test agent system prompt includes instructions for the no-tests case
- Agent writes TEST.md noting the absence and proceeds to commit without test changes
- No failure is raised for missing test suites

**Current status:** Handled via prompt instruction. Relies on agent following instructions correctly.

---

## Large Repo Context Limits

**Concern:** The Plan agent may hit context limits when scanning a large repository.

**Mitigation:**
- Plan agent uses FILES.md scoping to focus exploration
- `grep` and `list_dir` tools allow targeted exploration without reading entire files
- Thinking/reasoning mode (enabled for Anthropic models via LiteLLM's `thinking` parameter) allows the model to use context efficiently

**Current status:** Mitigated for typical repos. Very large repos (>10K files) may still cause issues; chunked grep strategies have not been implemented.
