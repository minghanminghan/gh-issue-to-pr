# gh-issue-to-pr

An AI agent pipeline that turns GitHub issues into pull requests. Given an issue URL, it autonomously reads the codebase, plans changes, writes code, validates it, runs tests, and opens a PR -- all using an LLM via LiteLLM.

## How it works

This repo wraps [mini-swe-agent](https://mini-swe-agent.com/) by adding a webserver and gh CLI to give it access to Issues.

## Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)**
- **[gh CLI](https://cli.github.com/)**
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

### Only run pipeline

```bash
python main.py run <issue_url> <repo_url> [options]
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

```bash
# Clone the repo automatically and run the pipeline
python main.py run https://github.com/<user>/<repo>/issues/<issue>

# Use a local repo checkout (must have a clean working tree)
python main.py run https://github.com/<user>/<repo>/issues/<issue> \
    --local-path /path/to/local/repo

# Pass contribution guidelines and a custom budget
python main.py run https://github.com/<user>/<repo>/issues/<issue> \
    --guidelines CONTRIBUTING.md \
    --budget 5.00

# Use a custom agent configuration
python main.py run https://github.com/<user>/<repo>/issues/<issue> \
    --config my_agent_config.yaml
```

The pipeline prints the run artifact directory on completion:

```bash
Pipeline completed. Run artifacts: <root>/run/<hash>/
```

### Run webserver

```bash
python main.py serve [--host HOST] [--port PORT]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--host HOST` | `127.0.0.1` | Bind address |
| `--port PORT` | `8080` | Bind port |

```bash
# Start on port 8080
python main.py serve

# Specify host and port
python main.py serve --host <host> --port <port>
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

## Run artifacts

Every run creates a directory at `<root>/run/<hash>/` (`run/` is gitignored). The hash is the first 8 hex characters of the SHA-256 of the issue URL, so re-running the same issue always maps to the same directory. If a hash collision occurs, the existing run directory is overwritten.

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
