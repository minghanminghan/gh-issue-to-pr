#!/usr/bin/env python3
"""CLI entry point for the gh-issue-to-pr pipeline."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import tools.log  # configures logging before any other import
from pipeline import run_pipeline, _DEFAULT_CI_RETRIES, _DEFAULT_BUDGET_USD

log = tools.log.get_logger(__name__)

def _run_subcommand(args: argparse.Namespace) -> None:
    """Execute the pipeline on a GitHub issue."""
    if not args.issue_url.startswith("https://github.com/"):
        log.error("Error: issue_url must be a full GitHub URL (https://github.com/...)")
        sys.exit(2)

    if args.local_path and not Path(args.local_path).is_dir():
        log.error(f"Error: --local-path does not exist or is not a directory: {args.local_path}")
        sys.exit(2)

    if args.guidelines and not Path(args.guidelines).is_file():
        log.error(f"Error: --guidelines file not found: {args.guidelines}")
        sys.exit(2)

    try:
        run_pipeline(
            issue_url=args.issue_url,
            guidelines_path=args.guidelines,
            local_path=args.local_path,
            model_name=args.model_name,
            max_steps=args.max_steps,
            budget=args.budget,
            cache=args.cache,
            ci_retries=args.ci_retries,
        )
        log.debug("\nPipeline completed.")
    except SystemExit as e:
        sys.exit(e.code)
    except KeyboardInterrupt:
        log.error("\nInterrupted by user.")
        sys.exit(130)
    except Exception as e:
        log.error(f"\nPipeline error: {e}")
        sys.exit(1)


def _serve_subcommand(args: argparse.Namespace) -> None:
    """Start the FastAPI/uvicorn web server, optionally with a GitHub webhook."""
    import uvicorn

    if not args.repo_url:
        uvicorn.run("server:app", host=args.host, port=args.port, reload=False)
        return

    # Webhook mode: start ngrok, register webhook, then serve
    import atexit
    import json
    import signal
    import subprocess
    import time
    import urllib.error
    import urllib.request

    if not args.repo_url.startswith("https://github.com/"):
        log.error("Error: --repo-url must be a full GitHub URL (https://github.com/...)")
        sys.exit(2)

    webhook_secret = os.environ.get("WEBHOOK_SECRET", "")
    if not webhook_secret:
        log.error("Error: WEBHOOK_SECRET environment variable must be set")
        sys.exit(2)

    owner_repo = args.repo_url.removeprefix("https://github.com/").rstrip("/")

    # Start ngrok
    log.info(f"Starting ngrok tunnel on port {args.port}...")
    ngrok_proc = subprocess.Popen(
        ["ngrok", "http", str(args.port), "--log=stdout"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Poll ngrok local API until HTTPS tunnel is ready
    public_url: str | None = None
    for _ in range(20):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen("http://localhost:4040/api/tunnels") as resp:
                data = json.loads(resp.read())
            for tunnel in data.get("tunnels", []):
                if tunnel.get("proto") == "https":
                    public_url = tunnel["public_url"]
                    break
        except urllib.error.URLError:
            pass
        if public_url:
            break

    if not public_url:
        ngrok_proc.terminate()
        log.error("Error: timed out waiting for ngrok HTTPS tunnel")
        sys.exit(1)

    webhook_url = f"{public_url}/webhook/github"
    log.info(f"ngrok tunnel active: {webhook_url}")

    # Register webhook via gh CLI
    log.info(f"Registering webhook on {args.repo_url}...")
    result = subprocess.run(
        [
            "gh", "api", f"repos/{owner_repo}/hooks",
            "--method", "POST",
            "-f", f"config[url]={webhook_url}",
            "-f", "config[content_type]=json",
            "-f", f"config[secret]={webhook_secret}",
            "-f", "events[]=issues",
            "-f", "events[]=issue_comment",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        ngrok_proc.terminate()
        log.error(f"Error: gh webhook registration failed:\n{result.stderr.strip()}")
        sys.exit(1)

    hook_id: str | None = None
    try:
        hook_id = str(json.loads(result.stdout)["id"])
        log.info(f"Webhook registered (id={hook_id})")
    except (KeyError, json.JSONDecodeError):
        log.warning("Could not parse hook ID from gh response; webhook will not be auto-deleted on exit")

    # Cleanup on exit
    def _cleanup() -> None:
        log.info("Cleaning up webhook and ngrok...")
        if hook_id:
            subprocess.run(
                ["gh", "api", f"repos/{owner_repo}/hooks/{hook_id}", "--method", "DELETE"],
                capture_output=True,
            )
            log.info(f"Webhook {hook_id} deleted")
        ngrok_proc.terminate()

    atexit.register(_cleanup)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    # Configure webhook trigger behaviour via env vars before server imports them
    os.environ["WEBHOOK_LABEL"] = args.label
    os.environ["WEBHOOK_ON_OPEN"] = "true" if args.on_open else "false"
    os.environ["WEBHOOK_ON_COMMENT"] = "true" if args.on_comment else "false"

    log.info(f"Starting server on {args.host}:{args.port}")
    uvicorn.run("server:app", host=args.host, port=args.port, reload=False)


def _detect_github_url(repo_path: str) -> str | None:
    """Return the GitHub HTTPS URL inferred from git remote origin, or None."""
    import subprocess
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_path, capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    remote = result.stdout.strip()
    # Normalise SSH → HTTPS: git@github.com:owner/repo.git → https://github.com/owner/repo
    if remote.startswith("git@github.com:"):
        remote = "https://github.com/" + remote[len("git@github.com:"):]
    # Strip .git suffix
    if remote.endswith(".git"):
        remote = remote[:-4]
    if remote.startswith("https://github.com/"):
        return remote
    return None


def _self_loop_subcommand(args: argparse.Namespace) -> None:
    """Run the self-improvement loop."""
    from self_loop.loop import self_loop_run
    from self_loop.schema.loop_config import SelfLoopConfig

    repo_path = str(Path(args.repo_path).resolve()) if args.repo_path else str(Path(".").resolve())

    if not Path(repo_path).is_dir():
        log.error(f"Error: --repo-path does not exist: {repo_path}")
        sys.exit(2)

    repo_url = args.repo_url or _detect_github_url(repo_path)
    if not repo_url:
        log.error("Error: could not detect GitHub URL from git remote; use --repo-url")
        sys.exit(2)
    if not repo_url.startswith("https://github.com/"):
        log.error("Error: --repo-url must be a full GitHub URL (https://github.com/...)")
        sys.exit(2)

    config: SelfLoopConfig = {
        "repo_local_path": repo_path,
        "repo_github_url": repo_url,
        "self_loop_branch": "self-loop",
        "max_iterations": args.max_iterations,
        "max_total_budget_usd": args.max_budget,
        "per_run_budget_usd": args.per_run_budget,
        "per_run_max_steps": args.per_run_steps,
        "scanner_model": args.scanner_model,
        "fix_model": args.fix_model,
        "state_file": "self-loop/STATE.json",
        "dry_run": args.dry_run,
        "min_issue_priority": args.min_priority,
        "guidelines_path": args.guidelines,
    }

    try:
        reason = self_loop_run(config)
        log.info(f"Self-loop completed: {reason}")
    except KeyboardInterrupt:
        log.error("\nInterrupted by user.")
        sys.exit(130)
    except Exception as e:
        log.error(f"\nSelf-loop error: {e}")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Turn a GitHub issue into a pull request using AI agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py run https://github.com/owner/repo/issues/42
  python main.py run https://github.com/owner/repo/issues/42 \\
      --local-path /path/to/local/repo --guidelines CONTRIBUTING.md --budget 5.00
  python main.py serve --host 0.0.0.0 --port 8080
  python main.py serve --repo-url https://github.com/owner/repo
  python main.py self-loop --dry-run
  python main.py self-loop --repo-url https://github.com/owner/gh-issue-to-pr --dry-run
""",
    )

    subparsers = parser.add_subparsers(dest="command")

    # ---- 'run' subcommand (original behavior) ----
    run_parser = subparsers.add_parser("run", help="Run the pipeline on a GitHub issue")
    run_parser.add_argument(
        "issue_url",
        help="Full GitHub issue URL (e.g. https://github.com/owner/repo/issues/42)",
    )
    run_parser.add_argument(
        "--local-path",
        metavar="PATH",
        help="Use an existing local repo checkout instead of cloning",
    )
    run_parser.add_argument(
        "--guidelines",
        metavar="FILE",
        help="Path to contribution guidelines file (e.g. CONTRIBUTING.md)",
    )
    run_parser.add_argument(
        "--model-name",
        metavar="MODEL",
        help="LiteLLM model name to use (e.g. anthropic/claude-sonnet-4-5-20250929)",
    )
    run_parser.add_argument(
        "--max-steps",
        type=int,
        metavar="STEPS",
        help="Maximum number of steps the mini-swe-agent can take",
    )
    run_parser.add_argument(
        "--budget",
        type=float,
        default=None,
        metavar="USD",
        help=f"Cost budget in USD (default: ${_DEFAULT_BUDGET_USD:.2f}, or $PIPELINE_BUDGET env var)",
    )
    run_parser.add_argument(
        "--cache",
        action="store_true",
        help="Keep the cloned run/<hash> directory after the PR is pushed (default: delete it)",
    )
    run_parser.add_argument(
        "--ci-retries",
        type=int,
        default=None,
        metavar="N",
        help=f"Max times to re-run the agent after CI failure (default: {_DEFAULT_CI_RETRIES})",
    )
    run_parser.set_defaults(func=_run_subcommand)

    # ---- 'serve' subcommand ----
    serve_parser = subparsers.add_parser("serve", help="Start the HTTP API server")
    serve_parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)"
    )
    serve_parser.add_argument(
        "--port", type=int, default=8080, help="Bind port (default: 8080)"
    )
    serve_parser.add_argument(
        "--repo-url",
        default=None,
        metavar="URL",
        help="GitHub repo URL to subscribe to via webhook (https://github.com/owner/repo). "
             "When set, starts an ngrok tunnel and registers a GitHub webhook automatically.",
    )
    serve_parser.add_argument(
        "--label",
        default="agent",
        metavar="LABEL",
        help="Only trigger on issues labeled with this value (default: 'agent'). Pass '' to trigger on any label.",
    )
    serve_parser.add_argument(
        "--on-open",
        action="store_true",
        help="Also trigger when an issue is opened, regardless of label",
    )
    serve_parser.add_argument(
        "--on-comment",
        action="store_true",
        help="Also trigger when an issue comment starts with /fix",
    )
    serve_parser.set_defaults(func=_serve_subcommand)

    # ---- 'self-loop' subcommand ----
    sl_parser = subparsers.add_parser(
        "self-loop", help="Continuously improve the codebase via self-loop agent"
    )
    sl_parser.add_argument(
        "--repo-url",
        default=None,
        metavar="URL",
        help="GitHub repo URL (default: auto-detected from git remote origin)",
    )
    sl_parser.add_argument(
        "--repo-path",
        metavar="PATH",
        default=".",
        help="Local path to the repo (default: current directory)",
    )
    sl_parser.add_argument(
        "--max-iterations",
        type=int,
        default=10,
        metavar="N",
        help="Maximum number of loop iterations (default: 10)",
    )
    sl_parser.add_argument(
        "--max-budget",
        type=float,
        default=30.0,
        metavar="USD",
        help="Total budget in USD across all runs (default: 30.0)",
    )
    sl_parser.add_argument(
        "--per-run-budget",
        type=float,
        default=3.0,
        metavar="USD",
        help="Budget per pipeline run in USD (default: 3.0)",
    )
    sl_parser.add_argument(
        "--per-run-steps",
        type=int,
        default=100,
        metavar="STEPS",
        help="Max steps per pipeline run (default: 100)",
    )
    sl_parser.add_argument(
        "--scanner-model",
        default="gemini/gemini-3.1-flash-lite-preview",
        metavar="MODEL",
        help="LiteLLM model for the scanner agent (default: gemini/gemini-3.1-flash-lite-preview)",
    )
    sl_parser.add_argument(
        "--fix-model",
        default=None,
        metavar="MODEL",
        help="LiteLLM model for the fix agent (default: MODEL_NAME env var)",
    )
    sl_parser.add_argument(
        "--min-priority",
        choices=["critical", "high", "medium", "low"],
        default="medium",
        help="Minimum issue priority to act on (default: medium)",
    )
    sl_parser.add_argument(
        "--guidelines",
        metavar="FILE",
        help="Path to contribution guidelines file",
    )
    sl_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and print candidates only; do not create issues or run pipeline",
    )
    sl_parser.set_defaults(func=_self_loop_subcommand)

    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
