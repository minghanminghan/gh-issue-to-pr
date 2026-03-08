# gh-issue-to-pr

An AI agent pipeline that turns GitHub issues into pull requests. Given an issue URL, it autonomously reads the codebase, plans changes, writes code, validates it, runs tests, and opens a PR -- all using an LLM via LiteLLM. It is recommended to run this project on Linux (although Windows and Mac are supported) because of agent sandboxing concerns.

## How it works

This repo wraps [mini-swe-agent](https://mini-swe-agent.com/) by adding a webserver and gh CLI to give it access to Issues.

## Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)**
- **[bwrap](https://github.com/containers/bubblewrap)** (Linux-only)
- **[gh CLI](https://cli.github.com/)**
- **[ngrok](https://ngrok.com/download)** (only required for `serve --repo-url` webhook mode)
- **[mini-swe-agent](https://mini-swe-agent.com/)**

---

## Installation

```bash
git clone https://github.com/yourname/gh-issue-to-pr
cd gh-issue-to-pr
uv sync
```

---

## Usage

### Optional: Collect traces using OpenTelemetry
e.g. using [Arize Phoenix](https://arize.com/docs/phoenix)

```bash
uv install --group phoenix
phoenix serve # defaults to port 6006
```

### Run pipeline through CLI

```bash
python src/main.py run <issue_url> <repo_url> [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `issue_url` | Yes | Full GitHub issue URL -- `https://github.com/owner/repo/issues/42` |
| `repo_url` | Yes | GitHub repo URL -- `https://github.com/owner/repo` |
| `--max_steps` | No | Maximum steps mini-swe-agent is allowed to take (default 50) |
| `--local-path PATH` | No | Use an existing local checkout instead of cloning |
| `--guidelines FILE` | No | Path to contribution guidelines (e.g. `CONTRIBUTING.md`) |
| `--config FILE` | No | Path to mini-swe-agent configuration YAML file |
| `--budget USD` | No | Cost cap in USD (default: `2.00`) |
| `--cache` | No | Keep the cloned `run/<hash>` directory after the PR is pushed (default: delete it) |

```bash
# Clone the repo automatically and run the pipeline
python src/main.py run https://github.com/<user>/<repo>/issues/<issue>

# Use a local repo checkout (must have a clean working tree)
python src/main.py run https://github.com/<user>/<repo>/issues/<issue> \
    --local-path /path/to/local/repo

# Pass contribution guidelines and a custom budget
python src/main.py run https://github.com/<user>/<repo>/issues/<issue> \
    --guidelines CONTRIBUTING.md \
    --budget 5.00

# Use a custom agent configuration
python src/main.py run https://github.com/<user>/<repo>/issues/<issue> \
    --config my_agent_config.yaml
```

The pipeline prints the run artifact directory on completion:

```bash
Pipeline completed. Run artifacts: <root>/run/<hash>/
```

### Run webserver

```bash
python src/main.py serve [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--host HOST` | `127.0.0.1` | Bind address |
| `--port PORT` | `8080` | Bind port |
| `--repo-url URL` | — | GitHub repo to subscribe to via webhook. When set, starts an ngrok tunnel and registers the webhook automatically. |
| `--label LABEL` | `agent` | Only trigger on issues labeled with this value. Pass `''` to trigger on any label. |
| `--on-open` | off | Also trigger when an issue is opened, regardless of label |
| `--on-comment` | off | Also trigger when an issue comment starts with `/fix` |

```bash
# Start the HTTP API server only
python src/main.py serve

# Start server and auto-subscribe to a GitHub repo via webhook
export WEBHOOK_SECRET=your-secret-here
python src/main.py serve --repo-url https://github.com/owner/repo

# Trigger on every new issue, not just labeled ones
python src/main.py serve --repo-url https://github.com/owner/repo --on-open --label ''
```

When `--repo-url` is provided:
1. An ngrok HTTPS tunnel is started on the bind port ([ngrok](https://ngrok.com/) must be installed and authenticated)
2. The tunnel URL is registered as a GitHub webhook on the repo (`WEBHOOK_SECRET` env var must be set)
3. The HTTP server starts

On exit (Ctrl+C), the webhook is automatically deleted from GitHub and ngrok is stopped.

**Trigger conditions (default):**

| GitHub event | Condition | Enabled by default |
|---|---|---|
| Issue labeled | label name == `agent` | yes |
| Issue opened | any | no — enable with `--on-open` |
| Issue comment created | body starts with `/fix` | no — enable with `--on-comment` |

Duplicate submissions (e.g. an issue opened and then labeled) are deduplicated — only one pipeline run per issue will be active at a time.

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
| `config` | string | No | Path to a custom agent configuration YAML file |
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
| `run_dir` | string \| null | Path to the `run/<hash>/` run directory once known |
| `outcome` | string \| null | `"pass"` or `"fail"` once complete |
| `error` | string \| null | Error message if an unexpected exception occurred |

---

### Self-loop (autonomous self-improvement)

The `self-loop` subcommand runs an autonomous improvement cycle on the repo itself. Each iteration:

1. Scans the codebase with a lightweight scanner agent to find improvement candidates (bugs, missing tests, code quality issues, etc.)
2. Deduplicates against already-seen fingerprints and open GitHub issues
3. Creates a GitHub issue for the best candidate (labeled `self-loop`)
4. Runs the fix pipeline, targeting a dedicated `self-loop` branch
5. Waits for CI; auto-merges on green, then restarts the loop with the updated code

State is persisted in `self-loop/STATE.json` so the loop can be resumed across restarts.

```bash
python src/main.py self-loop [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--repo-url URL` | auto-detected from `git remote origin` | GitHub repo URL |
| `--repo-path PATH` | `.` | Local path to the repo |
| `--max-iterations N` | `10` | Maximum loop iterations |
| `--max-budget USD` | `30.0` | Total budget cap in USD across all runs |
| `--per-run-budget USD` | `3.0` | Cost cap per individual pipeline run |
| `--per-run-steps N` | `100` | Max agent steps per pipeline run |
| `--scanner-model MODEL` | `gemini/gemini-3.1-flash-lite-preview` | LiteLLM model for the scanner agent |
| `--fix-model MODEL` | `$MODEL_NAME` | LiteLLM model for the fix agent |
| `--min-priority LEVEL` | `medium` | Minimum candidate priority to act on (`critical`, `high`, `medium`, `low`) |
| `--guidelines FILE` | — | Path to contribution guidelines file |
| `--dry-run` | off | Scan and print candidates only; do not create issues or run the pipeline |

```bash
# Preview what the scanner would find, without creating issues or running the pipeline
python src/main.py self-loop --dry-run

# Run against a specific repo with a $20 total budget
python src/main.py self-loop \
    --repo-url https://github.com/owner/repo \
    --max-budget 20.0 \
    --min-priority high
```

The loop terminates when any of the following conditions are met:

| Reason | Description |
|--------|-------------|
| `max_iterations_reached` | Ran all `--max-iterations` iterations |
| `budget_exhausted` | Total cost would exceed `--max-budget` |
| `no_candidates` | No viable candidates found in 3 consecutive iterations |
| `consecutive_failures` | 3 consecutive pipeline/CI/merge failures |
| `codebase_broken` | Import sanity check failed (loop would worsen a broken state) |
| `dry_run_complete` | `--dry-run` mode: candidates printed, nothing executed |

---

## Run artifacts

Every run creates a directory at `<root>/run/<hash>/` (`run/` is gitignored). The hash is the first 8 hex characters of the SHA-256 of the issue URL, so re-running the same issue always maps to the same directory. If a hash collision occurs, the existing run directory is overwritten.

By default the cloned repo inside `run/<hash>/` is deleted after the PR is successfully pushed. Pass `--cache` (CLI) or `"cache": true` (HTTP API) to keep it. Directories are never deleted when `--local-path` is used, since the checkout belongs to the caller.

| File | Written by | Contents |
|------|-----------|----------|
| `TRACE.json` | Report | Full observability trace: all spans, tokens, cost, outcome |
| `RUN.log` | All steps | JSON-lines event log written continuously throughout the run |

---

## Observability

Every agent call produces a **span** recorded in-memory and flushed to `TRACE.json` by the Report step. Each span includes:

- Agent name, start/end timestamps
- Token counts (input + output) and cost in USD
- Tool calls made, files read, files written

If `OTEL_COLLECTOR_ENDPOINT` is set, spans are exported via OTLP to any compatible collector (Arize Phoenix, Jaeger, Grafana Tempo, etc.). Each pipeline run creates one root span (`agent.run`) with one child span per LiteLLM call.

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

## Benchmarking (SWE-bench Verified)

The benchmark runner evaluates the pipeline against [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified). It requires Docker and the `benchmark` extra:

```bash
uv sync --extra benchmark
```

### Run the benchmark

```bash
uv run python src/benchmarks/benchmark.py [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--num-tasks N` | `10` | Number of tasks to run |
| `--instance-ids ID …` | — | Run specific tasks by ID (overrides `--num-tasks`) |
| `--model MODEL` | `$MODEL_NAME` | LiteLLM model name |
| `--max-steps N` | — | Max agent steps per task |
| `--max-workers N` | `4` | Docker eval parallelism |
| `--cache-level LEVEL` | `env` | Docker image cache: `none`, `base`, `env`, `instance` |
| `--output-dir PATH` | `benchmarks/results/<run_id>/` | Where to write results |
| `--run-id ID` | timestamp | Run identifier |
| `--skip-eval` | — | Write predictions without running the harness |

```bash
# Run 10 tasks with default settings
uv run python src/benchmarks/benchmark.py

# Run a specific task
uv run python src/benchmarks/benchmark.py --instance-ids django__django-10097

# Iterative dev: cache full Docker images so re-runs skip the build step (~30s saved per task)
uv run python src/benchmarks/benchmark.py --instance-ids django__django-10097 --cache-level instance

# Run 50 tasks with a specific model, 8 parallel Docker workers
uv run python src/benchmarks/benchmark.py --num-tasks 50 --model openai/gpt-4o --max-workers 8
```

Results are written to `benchmarks/results/<run_id>/`:

| File | Contents |
|------|----------|
| `predictions.jsonl` | One prediction per task (instance_id, model_patch, model_name_or_path) |
| `results.json` | Harness evaluation output (resolved counts, resolved IDs) |
| `summary.txt` | Human-readable summary printed at the end of the run |
