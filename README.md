# gh-issue-to-pr

An AI agent pipeline that turns GitHub issues into pull requests. Given an issue URL, it autonomously reads the codebase, plans changes, writes code, validates it, runs tests, and opens a PR -- all using an LLM via LiteLLM.

## How it works

The pipeline runs six sequential steps. Each agent step produces a markdown artifact in `.agent/<hash>/` inside the repo, and all steps share `STATE.json` for coordination.

```
Setup → Plan → Execute ↔ Validate → Test → Summary → Report
  0        1       2          3        4       5          6
```

| Step | Type | What it does |
|------|------|------|
| **0 Setup** | Deterministic | Fetches issue via `gh`, clones/verifies repo, creates `agent/<hash>` branch (force-resets if it already exists), writes `ISSUE.md` and `STATE.json` |
| **1 Plan** | Agent | Reads the issue and repo, writes `PLAN.md` (ordered change list with verification commands) and `FILES.md` (files to modify) |
| **2 Execute** | Agent | Translates `PLAN.md` into code changes, documents justifications in `CHANGES.md` |
| **3 Validate** | Agent | Runs verification commands from `PLAN.md`, checks code against spec, writes verdict to `VALIDATE.md` |
| **4 Test** | Agent | Runs existing tests, appends new tests as needed, commits on success |
| **5 Summary** | Agent | Squash-rebases, creates/force-pushes PR, polls CI, writes `SUMMARY.md` |
| **6 Report** | Deterministic | Writes `TRACE.json`, `FAILURE.md` on failure, exports to Arize Phoenix if configured |

### Loop control

When validation or tests fail, the pipeline loops back rather than aborting:

- **`minor` / `spec_deviation`** -- retry in the local Execute ↔ Validate cycle (cap: 2 retries, 3 total attempts)
- **`plan_invalid`** -- loop back to Plan with failure context injected (global loop cap: 3)
- **`unrecoverable`** -- route straight to Report
- **Budget exceeded** -- route straight to Report

On any loop-back, the agent that failed writes a report to its artifact file, and the orchestrator prepends a *Previous Attempt* block to the next agent's prompt so it can learn from the failure.

---

## Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** -- package manager
- **[gh CLI](https://cli.github.com/)** -- authenticated (`gh auth login`)
- **Environment variables set** -- see .env.example
- The target repo must be either accessible for cloning or cloned locally

---

## Installation

```bash
git clone https://github.com/yourname/gh-issue-to-pr
cd gh-issue-to-pr
uv sync
```

---

## Usage

### CLI -- run the pipeline

```
python main.py run <issue_url> <repo_url> [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `issue_url` | Yes | Full GitHub issue URL -- `https://github.com/owner/repo/issues/42` |
| `repo_url` | Yes | GitHub repo URL -- `https://github.com/owner/repo` |
| `--local-path PATH` | No | Use an existing local checkout instead of cloning |
| `--guidelines FILE` | No | Path to contribution guidelines (e.g. `CONTRIBUTING.md`) |
| `--budget USD` | No | Cost cap in USD (default: `2.00`) |

```bash
# Clone the repo automatically and run the pipeline
python main.py run https://github.com/owner/repo/issues/42 https://github.com/owner/repo

# Use a local repo checkout (must have a clean working tree)
python main.py run https://github.com/owner/repo/issues/42 https://github.com/owner/repo \
    --local-path /path/to/local/repo

# Pass contribution guidelines and a custom budget
python main.py run https://github.com/owner/repo/issues/42 https://github.com/owner/repo \
    --guidelines CONTRIBUTING.md \
    --budget 5.00
```

The pipeline prints the run artifact directory on completion:

```
Pipeline completed. Run artifacts: /path/to/repo/.agent/a3f9c1b2/
```

### CLI -- start the web server

```
python main.py serve [--host HOST] [--port PORT]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--host HOST` | `127.0.0.1` | Bind address |
| `--port PORT` | `8080` | Bind port |

```bash
# Start locally
python main.py serve

# Bind to all interfaces on port 9000
python main.py serve --host 0.0.0.0 --port 9000
```

### HTTP API

Once the server is running, three endpoints are available:

#### `GET /health`

```bash
curl http://127.0.0.1:8080/health
# {"status":"ok","version":"0.1.0"}
```

#### `POST /issue`

Accepts a pipeline job and runs it in the background. Returns `202 Accepted` immediately with a polling URL; returns `422` for invalid input.

```bash
curl -X POST http://127.0.0.1:8080/issue \
  -H "Content-Type: application/json" \
  -d '{
    "issue_url": "https://github.com/owner/repo/issues/42",
    "repo_url": "https://github.com/owner/repo",
    "budget": 3.00
  }'
# {"issue_url":"https://...","status_url":"/status?issue=https://..."}
```

If a job for the same `issue_url` is already queued or running, the server returns 202 pointing to the existing status URL without starting a new run.

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `issue_url` | string | Yes | Full GitHub issue URL |
| `repo_url` | string | Yes | GitHub repo URL |
| `local_path` | string | No | Path to an existing local checkout |
| `guidelines` | string | No | Contribution guidelines as an inline string |
| `budget` | float | No | Cost cap in USD (default: `2.00`, must be > 0) |

**Response body (202):**

| Field | Type | Description |
|-------|------|-------------|
| `issue_url` | string | The submitted issue URL |
| `status_url` | string | Path to poll for status, e.g. `/status?issue=<url>` |

#### `GET /status`

Poll this endpoint after `POST /issue`. Returns `404` if no job has been submitted for the given URL.

```bash
curl "http://127.0.0.1:8080/status?issue=https://github.com/owner/repo/issues/42"
```

**Response body:**

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"queued"`, `"running"`, `"completed"`, or `"failed"` |
| `issue_url` | string | The issue URL |
| `run_dir` | string \| null | Path to the `.agent/<hash>/` run directory once known |
| `outcome` | string \| null | `"pass"` or `"fail"` once complete |
| `error` | string \| null | Error message if an unexpected exception occurred |
| `state` | object \| null | Full `STATE.json` contents once a run directory is available |

---

## Run artifacts

Every run creates a directory at `<repo_root>/.agent/<hash>/` (`.agent/` is gitignored). The hash is the first 8 hex characters of the SHA-256 of the issue URL, so re-running the same issue always maps to the same directory.

| File | Written by | Contents |
|------|-----------|----------|
| `STATE.json` | All steps | Shared pipeline state -- branch name, loop counts, cost, PR URL, container ID, etc. |
| `ISSUE.md` | Setup | Issue title, body, and comments fetched from GitHub |
| `PLAN.md` | Plan agent | Ordered list of changes with per-step verification commands |
| `FILES.md` | Plan agent | Repo-relative paths of files to be modified |
| `CHANGES.md` | Execute agent | Justification for each edit |
| `VALIDATE.md` | Validate agent | Verification command output and pass/fail verdict |
| `TEST.md` | Test agent | Test run output and coverage notes |
| `SUMMARY.md` | Summary agent | PR description, CI status, loop count, total cost |
| `TRACE.json` | Report | Full observability trace: all spans, tokens, cost, outcome |
| `FAILURE.md` | Report | Written only on failure: `failure_source`, `last_failure_reason`, `current_step`, `loop_count` |
| `RUN.log` | All steps | JSON-lines event log written continuously throughout the run |

---

## Configuration

### `STATE.json` fields

Key fields you can inspect or override:

| Field | Default | Description |
|-------|---------|-------------|
| `cost_budget_usd` | `2.00` | Hard cap -- pipeline halts if `cost_spent_usd` reaches this before any agent call |
| `read_only` | See below | Glob patterns of files agents may never write |
| `loop_count` | `0` | Global plan→execute→validate→test loop count |
| `local_loop_count` | `0` | Execute↔Validate retry count within a single global iteration (cap: 2 retries, 3 total) |

### Default `read_only` patterns

These file patterns are blocked from being written by any agent:

```
README.md
CONTRIBUTING.md
LICENSE
.github/**
*.lock
.env*
```

The Plan agent can extend this list by adding entries to `STATE.json` if it identifies files that must not change for the given issue.

---

## Observability

Every agent call produces a **span** recorded in-memory and flushed to `TRACE.json` by the Report step. Each span includes:

- Agent name, start/end timestamps
- Token counts (input + output) and cost in USD
- Tool calls made, files read, files written
- Outcome (`pass` / `fail`)

If `PHOENIX_COLLECTOR_ENDPOINT` is set, the full trace is exported via OTLP to a self-hosted [Arize Phoenix](https://docs.arize.com/phoenix) instance at the end of the run. The pipeline creates one root span (`pipeline_run`) with one child span per agent call.

---

## Security model

### Shell command allowlist

Agents can only run commands from the following `(binary, args_prefix)` pairs:

| Binary | Allowed args |
|--------|-------------|
| `python` | any |
| `node` | any |
| `cargo` | any |
| `npm` | any |
| `pytest` | any |
| `ruff` | `check`, `format` |
| `mypy` | any |
| `git` | `status`, `add`, `commit`, `rebase`, `push`, `branch`, `checkout`, `log` |
| `gh` | `issue`, `pr` |

Additionally, commands are rejected if they contain shell metacharacters (`;`, `|`, `&`, `$(`, `` ` ``) or `..` path components.

### Write scoping

- The Execute agent may only write files listed in `FILES.md`
- The Validate agent may only write `VALIDATE.md`
- The Test agent may only append to test files (never overwrite, never delete)
- All agents respect the `read_only` glob list in `STATE.json`
- Agent artifacts live in `.agent/<hash>/`, completely separate from source files

### Branch safety

Setup creates `agent/<hash>` and fails loudly if the branch already exists. It will not reset or force-push without explicit instruction.

---

## Testing

The test suite uses `pytest` and requires no running services -- all external calls (LLM API, `gh` CLI, git) are mocked.

### Run all tests

```bash
uv run pytest
```

### Run with verbose output

```bash
uv run pytest -v
```

### Run a specific test file

```bash
uv run pytest tests/test_tools.py -v
uv run pytest tests/test_server.py -v
```

---

## Evaluation

### Per-agent (LLM-as-judge)

`tools/eval.py` implements rubric-based scoring for each agent using the model configured via `LLM_MODEL`:

| Agent | Rubric criteria |
|-------|----------------|
| Plan | All referenced files exist; each step is unambiguous; verification commands are runnable |
| Execute | All `FILES.md` entries addressed; `CHANGES.md` justification matches each edit; no out-of-scope writes |
| Validate | All `PLAN.md` verification steps run; each verdict has evidence; failure classification is correct |
| Test | No existing tests modified without justification; new tests are syntactically valid; all tests pass |
| Summary | PR description matches `CHANGES.md`; PR is linked to issue; CI status correctly reported |

### End-to-end benchmarks

- [SWE-bench](https://www.swebench.com/)
- [SWE-bench Verified](https://www.swebench.com/)
- PullRequestBenchmark

---

## Known limitations

See [docs/concerns.md](docs/concerns.md) for full details. In brief:

- **Soft cost cap** -- mid-agent cost spikes can still exceed the budget between API responses; the check fires after each streamed response, not token-by-token
- **Branch conflicts** -- if `agent/<hash>` already exists, Setup force-deletes and recreates it; any uncommitted work on that branch will be lost
- **In-memory job state** -- the server stores job status in memory; restarting the server loses all pending/running job records
- **Large repos** -- the Plan agent may approach context limits on repos with >10K files; chunked grep strategies are not yet implemented
- **`package-lock.json`** -- the default `*.lock` pattern matches `yarn.lock`, `Pipfile.lock`, etc., but not `package-lock.json` (which ends in `.json`). Add `package-lock.json` to `read_only` manually if needed.
