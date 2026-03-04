# gh-issue-to-pr

An AI agent pipeline that turns GitHub issues into pull requests. Given an issue URL, it autonomously reads the codebase, plans changes, writes code, validates it, runs tests, and opens a PR — all using Claude as the reasoning engine.

## How it works

The pipeline runs six sequential steps. Each agent step produces a markdown artifact in `.agent/<hash>/` inside the repo, and all steps share `STATE.json` for coordination.

```
Setup → Plan → Execute ↔ Validate → Test → Summary → Report
  0        1       2          3        4       5          6
```

| Step | Type | What it does |
|------|------|------|
| **0 Setup** | Deterministic | Fetches issue via `gh`, clones/verifies repo, creates `agent/<hash>` branch, starts Docker sandbox, writes `ISSUE.md` and `STATE.json` |
| **1 Plan** | Agent | Reads the issue and repo, writes `PLAN.md` (ordered change list with verification commands) and `FILES.md` (files to modify) |
| **2 Execute** | Agent | Translates `PLAN.md` into code changes, documents justifications in `CHANGES.md` |
| **3 Validate** | Agent | Runs verification commands from `PLAN.md`, checks code against spec, writes verdict to `VALIDATE.md` |
| **4 Test** | Agent | Runs existing tests, appends new tests as needed, commits on success |
| **5 Summary** | Agent | Squash-rebases, creates/force-pushes PR, polls CI, writes `SUMMARY.md` |
| **6 Report** | Deterministic | Tears down Docker sandbox, writes `TRACE.json`, `FAILURE.md` on failure, exports to Arize Phoenix if configured |

### Loop control

When validation or tests fail, the pipeline loops back rather than aborting:

- **`minor` / `spec_deviation`** — retry in the local Execute ↔ Validate cycle (cap: 2 attempts)
- **`plan_invalid`** — loop back to Plan with failure context injected (global loop cap: 3)
- **`unrecoverable`** — route straight to Report
- **Budget exceeded** — route straight to Report

On any loop-back, the agent that failed writes a report to its artifact file, and the orchestrator prepends a *Previous Attempt* block to the next agent's prompt so it can learn from the failure.

---

## Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** — package manager
- **[gh CLI](https://cli.github.com/)** — authenticated (`gh auth login`)
- **`ANTHROPIC_API_KEY`** environment variable set
- Git installed and the target repo must be either cloned locally (clean working tree) or accessible for cloning
- **[Docker](https://docs.docker.com/get-docker/)** — optional but recommended; code execution is sandboxed when available

---

## Installation

```bash
git clone https://github.com/yourname/gh-issue-to-pr
cd gh-issue-to-pr
uv sync
```

---

## Usage

### CLI — run the pipeline

```
python main.py run <issue_url> <repo_url> [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `issue_url` | Yes | Full GitHub issue URL — `https://github.com/owner/repo/issues/42` |
| `repo_url` | Yes | GitHub repo URL — `https://github.com/owner/repo` |
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

### CLI — start the web server

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

Once the server is running, two endpoints are available:

#### `GET /health`

```bash
curl http://127.0.0.1:8080/health
# {"status":"ok","version":"0.1.0"}
```

#### `POST /issue`

Runs the full pipeline synchronously. Returns `200` regardless of pipeline outcome (check the `outcome` field); returns `422` for invalid input and `500` for unexpected server errors.

```bash
curl -X POST http://127.0.0.1:8080/issue \
  -H "Content-Type: application/json" \
  -d '{
    "issue_url": "https://github.com/owner/repo/issues/42",
    "repo_url": "https://github.com/owner/repo",
    "budget": 3.00
  }'
```

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `issue_url` | string | Yes | Full GitHub issue URL |
| `repo_url` | string | Yes | GitHub repo URL |
| `local_path` | string | No | Path to an existing local checkout |
| `guidelines` | string | No | Contribution guidelines as an inline string |
| `budget` | float | No | Cost cap in USD (default: `2.00`, must be > 0) |

**Response body:**

| Field | Type | Description |
|-------|------|-------------|
| `run_dir` | string | Path to the `.agent/<hash>/` run directory |
| `outcome` | string | `"pass"` or `"fail"` |
| `pr_url` | string \| null | GitHub PR URL, if created |
| `cost_spent_usd` | float | Total cost spent in USD |
| `loop_count` | int | Number of global plan→execute→validate→test loops |

---

## Code sandbox (Docker)

When Docker is available, agent code execution is isolated in a container:

- **Setup** starts a `python:3.11-slim` container with the repo mounted at `/workspace` and runs `pip install -e .` inside it.
- Commands like `python`, `pytest`, `ruff`, `mypy`, `node`, `cargo`, `npm` run inside the container via `docker exec`.
- `git` and `gh` always run on the host (version control stays outside the sandbox).
- **Report** stops and removes the container at the end of every run, regardless of outcome.

If Docker is not installed, the pipeline prints a warning and continues without sandboxing — no configuration needed.

To use a different base image, the `start_container` function in `tools/docker.py` accepts an `image` parameter (default: `python:3.11-slim`).

---

## Run artifacts

Every run creates a directory at `<repo_root>/.agent/<hash>/` (`.agent/` is gitignored). The hash is the first 8 hex characters of the SHA-256 of the issue URL, so re-running the same issue always maps to the same directory.

| File | Written by | Contents |
|------|-----------|----------|
| `STATE.json` | All steps | Shared pipeline state — branch name, loop counts, cost, PR URL, container ID, etc. |
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
| `cost_budget_usd` | `2.00` | Hard cap — pipeline halts if `cost_spent_usd` reaches this before any agent call |
| `read_only` | See below | Glob patterns of files agents may never write |
| `loop_count` | `0` | Global plan→execute→validate→test loop count |
| `local_loop_count` | `0` | Execute↔Validate retry count within a single global iteration |
| `container_id` | `null` | Docker container ID for the current run; `null` if Docker is unavailable |

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

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key |
| `PHOENIX_COLLECTOR_ENDPOINT` | No | OTLP endpoint for Arize Phoenix trace export (e.g. `http://localhost:6006`) |

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

### Code sandbox

When Docker is available, all code execution (python, pytest, ruff, mypy, node, cargo, npm) runs inside a container with the repo mounted read-write. This prevents agent-written code from affecting the host environment beyond the repo directory. git and gh always run on the host so that version control and GitHub operations work normally.

### Shell command allowlist

Agents can only run commands from the following `(binary, args_prefix)` pairs:

| Binary | Allowed args | Runs in |
|--------|-------------|---------|
| `python` | any | Container |
| `node` | any | Container |
| `cargo` | any | Container |
| `npm` | any | Container |
| `pytest` | any | Container |
| `ruff` | `check`, `format` | Container |
| `mypy` | any | Container |
| `git` | `status`, `add`, `commit`, `rebase`, `push`, `branch`, `checkout`, `log` | Host |
| `gh` | `issue`, `pr` | Host |

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

The test suite uses `pytest` and requires no running services — all external calls (Claude API, `gh` CLI, git, Docker) are mocked.

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
uv run pytest tests/test_docker.py -v
uv run pytest tests/test_server.py -v
```

### Test suite overview

| File | Tests | What it covers |
|------|-------|----------------|
| `tests/test_state.py` | 10 | `PipelineState` schema defaults, round-trip serialisation, `init_run`, gitignore mutation |
| `tests/test_tools.py` | 22 | All `fs` tool primitives, shell allowlist checks, `execute_cli` safety |
| `tests/test_manifests.py` | 11 | Per-agent tool manifests: correct schemas exported, forbidden tools absent per agent |
| `tests/test_readonly.py` | 8 | Glob-based read-only enforcement for `.github/**`, `.env*`, `*.lock`, and exact filenames |
| `tests/test_context.py` | 10 | Context block content, artifact injection on loop-back, truncation at 4000-token limit |
| `tests/test_report.py` | 6 | Pass/fail outcomes, `FAILURE.md` field presence, `TRACE.json` written |
| `tests/test_pipeline.py` | 3 | Orchestration logic with stubbed agents: all-pass, report-on-failure, budget-exceeded routing |
| `tests/test_loop.py` | 3 | Global loop increment, max-loops → fail transition, `local_loop_count` reset on global retry |
| `tests/test_docker.py` | 35 | Docker container lifecycle, sandbox binary routing, `container_id` state field, report teardown |
| `tests/test_server.py` | 27 | `GET /health`, `POST /issue` validation/pass/fail, `SystemExit` interception, guidelines tempfile, CLI subcommands |

### Expected output

```
144 passed in ~2s
```

---

## Project structure

```
gh-issue-to-pr/
├── main.py                  # CLI entry point (subcommands: run, serve)
├── pipeline.py              # Orchestrator: sequences steps, manages loops
├── server.py                # FastAPI web server (GET /health, POST /issue)
├── pyproject.toml           # uv project config and dependencies
│
├── agents/
│   ├── base.py              # Streaming agentic loop (tool dispatch, cost tracking, span recording)
│   ├── plan.py              # Plan agent
│   ├── execute.py           # Execute agent
│   ├── validate.py          # Validate agent
│   ├── test_agent.py        # Test agent
│   └── summary.py           # Summary agent
│
├── tools/
│   ├── fs.py                # read_file, write_file, create_file, append_file, list_dir, grep
│   ├── shell.py             # execute_cli with allowlist and Docker routing
│   ├── docker.py            # Docker container lifecycle (start, stop, install deps)
│   ├── state.py             # STATE.json read/write, init_run
│   ├── setup.py             # Step 0: issue fetch, clone, branch creation, container start
│   ├── report.py            # Step 6: container stop, FAILURE.md, TRACE.json, sys.exit
│   ├── trace.py             # Span recording, TRACE.json, OTLP export
│   ├── manifests.py         # Per-agent tool schema definitions
│   ├── context.py           # Context block builder for loop-back prompts
│   ├── logger.py            # JSON-lines event logger (RUN.log)
│   └── eval.py              # LLM-as-judge scorer (per-agent rubrics)
│
├── schemas/
│   └── state.py             # Pydantic PipelineState model, Step/FailureSource enums
│
├── tests/                   # pytest test suite (144 tests, no external deps)
│
├── docs/
│   └── concerns.md          # Operational concerns and mitigations
│
└── runs/                    # Empty placeholder (run dirs live inside repo clones)
```

---

## Evaluation

### Per-agent (LLM-as-judge)

`tools/eval.py` implements rubric-based scoring for each agent using `claude-opus-4-6`:

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

- **Soft cost cap** — mid-agent cost spikes can exceed the budget before the next check
- **Branch conflicts** — if `agent/<hash>` already exists from a prior run, the pipeline fails loudly and requires manual branch deletion
- **Docker image language support** — the default `python:3.11-slim` image only covers Python projects; set a different image in `tools/docker.py` for Node, Rust, etc.
- **Synchronous HTTP server** — `POST /issue` blocks until the pipeline completes; long runs will hold the connection open
- **Large repos** — the Plan agent may approach context limits on repos with >10K files; chunked grep strategies are not yet implemented
- **`package-lock.json`** — the default `*.lock` pattern matches `yarn.lock`, `Pipfile.lock`, etc., but not `package-lock.json` (which ends in `.json`). Add `package-lock.json` to `read_only` manually if needed.
