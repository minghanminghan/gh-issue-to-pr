# Implementation Plan

## Phase 1: Project Scaffold & Shared Infrastructure

The foundation everything else depends on. No agents yet — just the data model, file I/O, and tool primitives.

### Steps

**1.1 — Project structure**
- Create directory layout: `agents/`, `tools/`, `schemas/`, `tests/`, `runs/`
- Create `main.py` as the pipeline entry point (accepts issue URL + repo path as args)
- Create `requirements.txt` with `anthropic`, `pytest`, `pydantic`
- Verification: `python main.py --help` runs without error

**1.2 — Shared state schema**
- Define `schemas/state.py` with a Pydantic model for STATE.json
  - Fields: `repo_url`, `local_dir`, `issue_url`, `issue_body`, `branch_name`, `loop_count`, `local_loop_count`, `current_step`, `plan_version`, `last_failure_reason`, `failure_source`, `ci_status`, `commit_sha`, `pr_url`, `cost_budget_usd`, `cost_spent_usd`, `read_only`
  - `local_dir`: the resolved path to `.agent/<hash>/` for this run; agents use this as their working root
  - `local_loop_count`: tracks exec↔validate cycles within a single global iteration; default 0
  - `current_step` enum: `[setup, plan, execute, validate, test, summary, report]`
  - `commit_sha: str | None` (default None; written by Test agent on success)
  - `pr_url: str | None` (default None; written by Summary agent on first PR creation)
  - `cost_budget_usd: float` (default 2.00)
  - `cost_spent_usd: float` (default 0.0; updated after each agent call)
  - `read_only: list[str]` (default populated by `init_run`; see 1.3)
- Implement `read_state()` and `write_state()` in `tools/state.py`
- Verification: round-trip serialize/deserialize a STATE.json with all fields populated

**1.3 — Agent working directory**
- Pipeline writes all run artifacts to `<repo_root>/.agent/<hash>/` where `<hash>` is the first 8 hex characters of the SHA-256 of the issue URL
- Directory contains: ISSUE.md, STATE.json, PLAN.md, FILES.md, CHANGES.md, VALIDATE.md, TEST.md, SUMMARY.md, RUN.log, TRACE.json (written by Report step), FAILURE.md (written by Report step on failure only)
- `init_run(repo_url, issue_url) -> run_dir` in `tools/state.py`: creates the directory, adds `.agent/` to `.gitignore` if not already present, writes initial STATE.json
- `init_run()` populates default `read_only` entries: `["README.md", "CONTRIBUTING.md", "LICENSE", ".github/**", "*.lock", ".env*"]`
- Verification: calling `init_run()` produces the correct hash path, `.gitignore` contains `.agent/`, STATE.json is initialized with correct `repo_url`, `issue_url`, `local_dir`, and default `read_only` entries

**1.4 — Tool primitives**
- Implement tool functions in `tools/fs.py`: `read_file`, `write_file`, `append_file`, `create_file`, `list_dir`, `grep`
- Implement tool functions in `tools/shell.py`: `execute_cli(cmd, allowlist)` where allowlist is a list of `(binary, args_prefix)` tuples (not binary name strings)
  - Full binary list: `python`, `node`, `cargo`, `npm`, `pytest`, `ruff`, `mypy`, `git` (scoped args), `gh` (scoped args)
  - Reject shell metacharacters before execution: `;`, `|`, `&`, `$(`, backtick
  - Reject `..` path components in any argument
  - Enforce CWD = repo root for all execute_cli calls
- Each tool returns a typed result: `{ok: bool, output: str, error: str | None}`
- Verification: unit test each tool function with a temp directory; confirm `python /evil.py` and `pytest; rm -rf .` are both rejected by the allowlist

**1.5 — Tool manifest per agent**
- Define a `ToolManifest` dataclass: a named list of tool functions an agent is allowed to call
- Implement a `build_tools(manifest) -> list[dict]` that converts the manifest to Claude SDK tool-use schema format
- Verification: each agent's manifest produces valid Anthropic tool schemas (validate against SDK expectations)

**1.6 — Setup utilities**
- Implement `tools/setup.py`: `run_setup(repo_url, issue_url) -> run_dir`
  - Fetch issue via `gh issue view <url> --json title,body,comments`; write to ISSUE.md in run dir
  - Clone repo if not already local, or verify local path is clean (no uncommitted changes)
  - Create branch `agent/<hash>`; if it already exists, force-delete it and log to stderr before recreating
  - Populate STATE.json with `issue_body` and `branch_name` alongside all fields from `init_run`
- Verification: calling `run_setup()` produces ISSUE.md with issue content, branch `agent/<hash>` exists, STATE.json has `branch_name` and `issue_body`

### Phase 1 Tests
- `tests/test_state.py`: schema validation, read/write round-trip, init_run idempotency, hash collision (same issue URL always produces same path), `.gitignore` mutation, default `read_only` entries present
- `tests/test_tools.py`: each fs and shell tool with success and failure cases; verify allowlist blocks disallowed commands; verify shell metacharacter and `..` rejection
- `tests/test_setup.py`: branch creation, duplicate branch force-reset (log + recreate), ISSUE.md content written, STATE.json fields `branch_name` and `issue_body` populated
- `tests/test_manifests.py`: each agent's manifest produces correct tool schemas with no extra tools

---

## Phase 2: Pipeline Orchestrator & Loop Controller

The Python control flow that sequences agents, manages loops, and enforces stopping conditions. Still no agent LLM calls yet.

### Steps

**2.1 — Pipeline runner**
- Implement `pipeline.py` with `run_pipeline(repo_url, issue_url, guidelines_path)`; calls `run_setup()` first to produce `run_dir`, then passes it through all steps
- `guidelines_path`: content is read and injected into the Plan agent system prompt
- Sequential step execution: setup → plan → execute → validate → test → summary → report
- Each agent step is a function call that takes `run_dir` and returns `StepResult(ok, failure_source, failure_reason)`
- Report step always runs last regardless of outcome; it is never skipped
- Verification: stub all 5 agent steps to return `ok=True`; confirm pipeline runs end-to-end, report runs, and TRACE.json is written

**2.2 — Loop controller**
- On `StepResult(ok=False)`, route by failure classification:
  - `minor` → do not increment `loop_count`; hand off to local exec↔validate cycle (see 2.3)
  - `spec_deviation` → skip Plan; loop back to Execute only; inject VALIDATE.md via context injection (see 2.6)
  - `plan_invalid` → increment `loop_count`, set `failure_source` and `last_failure_reason`, return to Plan
  - `unrecoverable` → set `failure_source` and `last_failure_reason` in STATE.json; route to Report step (Report writes FAILURE.md and exits non-zero)
- Check cost budget before every agent call AND mid-agent-stream after each API response: if `cost_spent_usd + running_cost >= cost_budget_usd`, set `failure_source = budget_exceeded` and route to Report step
- FAILURE.md is always written by Report, never inline by the orchestrator
- Verification: stub step 2 to always fail with `plan_invalid`; confirm pipeline routes to Report after 3 loops, FAILURE.md is written with correct fields; test `budget_exceeded` path with `cost_budget_usd=0`

**2.3 — Local loop (execute ↔ validate)**
- `minor` failures increment `local_loop_count`; allow up to 2 retries (3 total attempts) per global iteration before escalating
- Reset `local_loop_count` to 0 on every global loop-back
- When `local_loop_count >= _LOCAL_LOOP_CAP` (cap=2), escalate by reclassifying as `plan_invalid` and triggering global loop
- Verification: stub validate to emit `minor` twice then pass; confirm global `loop_count` stays at 0; stub validate to emit `minor` three times; confirm `loop_count` increments on escalation

**2.4 — Read-only enforcement**
- Before any `write_file`, `append_file`, or `create_file` call, check the repo-relative path against `state.read_only` globs (not just exact filenames)
- If blocked, return an error result instead of writing
- Verification: populate `read_only` with `.github/**`; confirm `write_file` to `.github/CODEOWNERS` is rejected; confirm write to an unlisted path succeeds

**2.5 — Structured logging**
- Implement `tools/logger.py`: writes JSON lines to `<local_dir>/RUN.log`
- Log every tool call: `{timestamp, agent, tool, args_summary, ok, tokens_in, tokens_out, cost_usd}`
- After each agent call completes, update `cost_spent_usd` in STATE.json (cumulative sum)
- Verification: run a stubbed pipeline and confirm RUN.log contains one entry per tool call with correct fields; confirm STATE.json `cost_spent_usd` increases after each agent call

**2.6 — Context injection**
- Implement `build_context_block(state, run_dir) -> str` in `tools/context.py`
- Produces a structured block prepended to the agent's user prompt on loop-back:
  ```
  ## Previous Attempt (loop N)
  Failure source: <failure_source>
  Failure reason: <last_failure_reason>

  ### Relevant artifacts
  <contents of VALIDATE.md or TEST.md or SUMMARY.md, whichever triggered the loop>
  ```
- Plan agent receives this block when `loop_count > 0`
- Execute agent receives it when `failure_source == spec_deviation`
- Truncate block to 4000 tokens (oldest content first) if over limit
- Verification: mock a loop-back state with each failure type; confirm correct artifact is injected and truncation fires at limit

**2.7 — Report step**
- Implement `run_report(run_dir, outcome)` in `tools/report.py`
- On `pass`: reads STATE.json (`pr_url`, `ci_status`, `cost_spent_usd`, `loop_count`); closes trace (delegates to Phase 4 trace module once built; stub in Phase 2)
- On `fail`: writes FAILURE.md containing `failure_source`, `last_failure_reason`, `current_step`, `loop_count`; closes trace (stub in Phase 2); exits non-zero
- Exports trace to Arize Phoenix via OTLP if `PHOENIX_COLLECTOR_ENDPOINT` is set (stub in Phase 2; wired in Phase 4)
- Verification: call with a pass outcome; confirm no FAILURE.md written; call with a fail outcome; confirm FAILURE.md contains all four fields and process exits non-zero

### Phase 2 Tests
- `tests/test_pipeline.py`: full pipeline with all stubs passing; report runs last on success; report runs last on failure; FAILURE.md written only by report (never inline); exit on `budget_exceeded` routes to report
- `tests/test_loop.py`: global loop counter, local loop counter reset on global loop-back, correct STATE.json at each transition; each failure classification routes to correct next step; max loops routes to report
- `tests/test_report.py`: pass outcome produces no FAILURE.md; fail outcome produces FAILURE.md with correct fields; non-zero exit on fail
- `tests/test_readonly.py`: glob-based blocking (`.github/**`), write allowed for unlisted paths, `append_file` also blocked for read-only paths
- `tests/test_context.py`: "Previous Attempt" block format; correct artifact per failure type; 4000-token truncation

---

## Phase 3: Agent Implementation (Claude SDK)

Implement the 5 agents as real LLM calls. Each agent gets its scoped tool manifest and system prompt.

### Steps

**3.1 — Base agent wrapper**
- Implement `agents/base.py`: `run_agent(system_prompt, user_prompt, tools, run_dir) -> AgentResult`
- Handles the Claude SDK call, tool-use loop (model calls tool → execute → feed result back), and returns final text output + token counts
- After each call, calculate `cost_usd` from token counts using Claude API pricing and update `cost_spent_usd` in STATE.json
- Verification: call with a simple "list files in this directory" prompt and a `list_dir` tool; confirm it uses the tool, returns a result, and STATE.json `cost_spent_usd` is updated

**3.2 — Plan agent**
- System prompt: role, repo guidelines (injected from `guidelines_path`), output format for PLAN.md and FILES.md
- Tools: `list_dir`, `read_file`, `write_file` (scoped to PLAN.md and FILES.md only), `grep`
- Inputs: ISSUE.md (written by Setup) is read and injected into user prompt before repo exploration
- On loop-back: "Previous Attempt" block is injected by orchestrator via `build_context_block` (see 2.6)
- Output:
  - PLAN.md: sequential steps; each step includes a runnable bash verification command (e.g. `ruff check .`, `pytest tests/`)
  - FILES.md: one entry per line in format `- path/to/file.py: <one-line rationale>`
- Plan agent may extend `state.read_only` if it identifies files that must not change; writes updated STATE.json after planning
- Verification: run against a real small repo and a toy issue; confirm PLAN.md steps each include a bash command, FILES.md matches expected format

**3.3 — Execute agent**
- System prompt: role, plan execution instructions, CHANGES.md output format
- Tools: `read_file`, `write_file`, `create_file`, `grep`, `execute_cli` (allowlisted)
- Reads PLAN.md and FILES.md as input context; may only write files listed in FILES.md (read-only enforcement in 2.4 enforces this)
- CHANGES.md format: one entry per file changed: `### path/to/file.py` followed by `- Step N: <justification>`
- Verification: run against the toy issue plan; confirm only FILES.md-listed files are modified and CHANGES.md entries match the format

**3.4 — Validate agent**
- System prompt: role, spec-checking instructions, VALIDATE.md output format, failure classification taxonomy
- Tools: `read_file`, `write_file` (VALIDATE.md only), `execute_cli` (lint and build commands from PLAN.md verification steps only), `grep`
- Reads PLAN.md and CHANGES.md; runs the runnable bash verification command from each PLAN.md step
- Classify and set `failure_source` in STATE.json: `minor` (lint/compile/trivial), `spec_deviation` (code doesn't match spec), `plan_invalid` (plan is fundamentally wrong)
- Writes VALIDATE.md: pass/fail per plan step with evidence; failure classification with rationale
- Verification: introduce a deliberate lint error; confirm validate agent catches it, sets correct `failure_source`, and writes evidence to VALIDATE.md

**3.5 — Test agent**
- System prompt: role, append-only test instructions, TEST.md output format, failure classification taxonomy
- Tools: `read_file`, `append_file` (test files only — no overwrite), `execute_cli` (test runner; git add + git commit on success), `grep`
- Runs existing tests; appends new tests if coverage gaps found; writes TEST.md with results
- Test edits (not additions) are permitted only if the issue explicitly changes a public interface; agent must write justification in TEST.md before editing
- Test deletions are never permitted; agent must classify as `plan_invalid` and loop back if any deletion is needed
- Test agent classifies failures and sets `failure_source` in STATE.json (same taxonomy as Validate)
- On all tests passing: stage and commit all changes (source files from FILES.md + any appended test files)
  - Commit message: `agent: <issue title> (#<issue number>)`
  - Write resulting `commit_sha` to STATE.json
- If repo has no tests: write TEST.md noting absence; proceed to commit without test changes
- Verification: confirm no existing test files are overwritten (only appended); confirm `commit_sha` appears in STATE.json on success; confirm absence of tests is handled gracefully

**3.6 — Summary agent**
- System prompt: role, PR formatting instructions
- Tools: `read_file`, `list_dir`, `grep`, `execute_cli` (git rebase, git push --force-with-lease, gh pr create/view/checks)
- Reads all prior markdown files (ISSUE.md, PLAN.md, CHANGES.md, VALIDATE.md, TEST.md)
- Squash all commits on `agent/<hash>` into one via `git rebase --autosquash origin/main` (or `git reset --soft $(git merge-base HEAD origin/main)` + commit)
- Conditional PR logic:
  - If `state.pr_url` is None: create PR via `gh pr create`; write URL to STATE.json
  - If `state.pr_url` exists (loop-back): force-push squashed commit via `git push --force-with-lease`; do not create a new PR
- After push: poll CI status via `gh pr checks <pr_url>`; if CI fails, write failure report to SUMMARY.md and classify as `ci` failure (global loop)
- Final SUMMARY.md on success: PR URL, CI status, total loop count, total cost
- Verification: run in a test repo; confirm force-push path is taken on second run; confirm `--force-with-lease` is used (not `--force`); confirm final SUMMARY.md contains all four fields

### Phase 3 Tests
- `tests/test_agents.py`: for each agent, mock the Claude SDK response and tool calls; verify correct tools are invoked, outputs are written to correct files, forbidden tools are never called; for Plan and Execute agents, mock a loop-back state and confirm "Previous Attempt" block appears in prompt with correct artifact
- `tests/test_agent_integration.py`: run each agent against a fixture repo with a known issue; assert output file structure and content format; confirm commit SHA in STATE.json after Test agent succeeds; confirm force-push path in Summary agent on second run

---

## Phase 4: Observability

Wire up tracing across the full pipeline run.

### Steps

**4.1 — Span model**
- Define a `Span` dataclass: `agent`, `start_time`, `end_time`, `tokens_in`, `tokens_out`, `cost_usd`, `tools_called`, `files_read`, `files_written`, `outcome`
- `cost_usd` is calculated from token counts using Claude API pricing
- Each `run_agent()` call creates and closes a span
- Verification: run a single agent call; confirm a valid span is produced with a non-zero `cost_usd`

**4.2 — Trace aggregation**
- Implement `tools/trace.py`: `open_trace(run_dir)` called by `run_setup()`; `close_trace(run_dir, outcome)` called by `run_report()`
- Trace lifecycle: opens at Setup start, closes at Report
- `close_trace()` writes TRACE.json: `{run_id, issue_url, total_tokens, total_cost_usd, loop_count, outcome, human_feedback: null, spans[]}`
- `total_cost_usd` sums `cost_usd` across all spans
- Wire `run_report()` (from 2.7) to call `close_trace()` instead of its Phase 2 stub
- Verification: run stubbed pipeline end-to-end; confirm TRACE.json is written by report (not earlier), contains one span per agent call, correct `total_cost_usd`, and `outcome` matches actual result

**4.3 — External export**
- Wire `run_report()` to export via `arize-phoenix-otel` OTLP exporter after `close_trace()` if `PHOENIX_COLLECTOR_ENDPOINT` is set (replaces Phase 2 stub)
- Add `arize-phoenix-otel` to `requirements.txt`
- Verification: mock the OTLP exporter; confirm it is called with the correct trace payload when `PHOENIX_COLLECTOR_ENDPOINT` is set and not called when absent

### Phase 4 Tests
- `tests/test_trace.py`: span creation, aggregation into TRACE.json, cost calculation, export gating; trace opens in setup and closes in report; TRACE.json not present before report runs

---

## Phase 5: End-to-End Integration & Hardening

Run the full pipeline on real repos and fix what breaks.

### Steps

**5.1 — Fixture repo**
- Create or identify a small public Python repo with open issues, a test suite, and CI
- Write 3–5 curated fixture issues with known correct solutions (ground truth diffs)
- Verification: manually verify fixture issues are reproducible and ground truth diffs are correct

**5.2 — Full pipeline run**
- Run the pipeline end-to-end against each fixture issue
- Log run results: which step failed, loop count, final outcome
- Verification: at least 1 fixture issue produces a valid PR without manual intervention

**5.3 — Edge case hardening**
- Test: issue with no relevant files found (plan agent should fail gracefully, not write an empty plan)
- Test: execute agent produces code that doesn't compile (validate catches it, loops back correctly)
- Test: test agent finds zero existing tests (writes TEST.md noting absence, commits without test changes, does not crash)
- Test: test deletion required (test agent classifies as `plan_invalid`, loops back correctly)
- Test: CI fails on first PR attempt (summary loops back correctly; force-push used on second attempt, no new PR created)
- Test: branch `agent/<hash>` already exists when setup runs (force-deleted and recreated with log message)
- Test: `cost_budget_usd` exceeded mid-run (FAILURE.md written with `budget_exceeded`, non-zero exit)
- Verification: each edge case exits cleanly with a populated FAILURE.md or loops correctly per spec

**5.4 — Cost and latency baseline**
- Record tokens in/out, wall time, and estimated cost for each fixture issue run
- Write results to `runs/benchmarks.md`
- Verification: benchmarks.md exists and contains a row per fixture run

**5.5 — Evaluation**
- Implement LLM-as-judge scorer in `tools/eval.py`; rubrics per agent:
  - Plan: (1) all referenced files exist, (2) each step is unambiguous and actionable, (3) verification commands are runnable bash commands
  - Execute: (1) all FILES.md entries addressed, (2) CHANGES.md justifications match edits, (3) no files outside FILES.md modified
  - Validate: (1) all PLAN.md verification steps run, (2) each pass/fail verdict has evidence, (3) failure classification is correct
  - Test: (1) no existing tests modified without justification, (2) new tests are syntactically valid, (3) all tests pass after appends
  - Summary: (1) PR description reflects CHANGES.md, (2) PR is linked to issue, (3) CI status correctly reported
- SWE-bench and SWE-bench Verified: stretch evaluation targets; use the external SWE-bench harness (not implemented in this repo); document setup instructions in `docs/swebench.md`
- Human feedback: `human_feedback` field in TRACE.json (null by default); annotate with binary pass/fail after human PR review

**5.6 — Concerns documentation**
- Add `docs/concerns.md` documenting each concern with its mitigation and current status:
  - Execution safety, cost runaway, GitHub rate limits, branch conflicts, secrets exposure, test suite absence, large repo context limits

### Phase 5 Tests
- `tests/test_e2e.py`: run pipeline against each fixture issue in a sandboxed temp directory; assert TRACE.json exists, STATE.json shows correct final step, no test files were modified, `commit_sha` is set, PR URL is set
- Regression: re-run after any agent prompt change to confirm fixture outcomes don't degrade

---

## Phase 6: Web Server

Expose the pipeline via HTTP. The pipeline runs asynchronously in a background thread; callers poll for status.

### Steps

**6.1 — `pyproject.toml`**
- Add `fastapi>=0.110.0` and `uvicorn[standard]>=0.29.0` to runtime dependencies

**6.2 — `server.py`** (project root)
- In-memory job registry `_jobs: dict[str, dict]` keyed by `issue_url`
- `POST /issue`: validate → register job as `queued` → start daemon thread → return 202 with `{issue_url, status_url}`
- `_run_pipeline_job`: set `running`, call `run_pipeline`, catch `SystemExit`/`Exception`, write `completed`/`failed` + `outcome` + `run_dir`
- `GET /status?issue=<url>`: look up job; read STATE.json from `run_dir` when available; 404 if unknown
- Guidelines inline string → tempfile, cleaned up in `finally`
- Already queued/running → return 202 to same `status_url` without starting a new thread

**6.3 — `main.py`** (restructured)
- Argparse subcommands: `run` and `serve`
- Verification: `python main.py --help` shows both subcommands

### Phase 6 Tests
- `tests/test_server.py` using `fastapi.testclient.TestClient` and a synchronous thread mock (`_SyncThread`):
  - `GET /health` → 200, correct body
  - Missing fields / budget ≤ 0 → 422
  - `POST /issue` → 202, response has `issue_url` and `status_url`
  - Already running/queued → 202, no new thread
  - Pass/fail/exception outcomes stored correctly in job registry
  - `GET /status` for unknown → 404; for queued/running/completed/failed → correct fields
  - Completed job exposes STATE.json in `state` field
  - Guidelines tempfile creation and cleanup

---

## Milestones

| Milestone | Done when |
|---|---|
| Phase 1 complete | All scaffold tests pass including setup utilities and updated execute_cli policy |
| Phase 2 complete | Pipeline loops and stops correctly; setup runs before plan; context injection fires on loop-back; cost budget enforced |
| Phase 3 complete | All 5 agents produce correct outputs; Test agent commits on success; Summary squashes and creates/updates PR |
| Phase 4 complete | TRACE.json with `cost_usd` per span generated on every run |
| Phase 5 complete | ≥1 fixture issue produces a mergeable PR end-to-end; LLM-as-judge scores all 5 agents |
| Phase 6 complete | HTTP server accepts jobs, returns 202, exposes STATE.json via polling endpoint |
