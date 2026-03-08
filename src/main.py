#!/usr/bin/env python3
"""CLI entry point for the gh-issue-to-pr pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import tools.log  # configures logging before any other import
from pipeline import run_pipeline

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
    """Start the FastAPI/uvicorn web server."""
    import uvicorn

    uvicorn.run("server:app", host=args.host, port=args.port, reload=False)


def _self_loop_subcommand(args: argparse.Namespace) -> None:
    """Run the self-improvement loop."""
    import os
    from self_loop.loop import self_loop_run
    from self_loop.schema.loop_config import SelfLoopConfig

    repo_path = str(Path(args.repo_path).resolve()) if args.repo_path else str(Path(".").resolve())

    if not Path(repo_path).is_dir():
        log.error(f"Error: --repo-path does not exist: {repo_path}")
        sys.exit(2)

    if not args.repo_url.startswith("https://github.com/"):
        log.error("Error: --repo-url must be a full GitHub URL (https://github.com/...)")
        sys.exit(2)

    config: SelfLoopConfig = {
        "repo_local_path": repo_path,
        "repo_github_url": args.repo_url,
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
        default=2.00,
        metavar="USD",
        help="Cost budget in USD (default: 2.00)",
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
    serve_parser.set_defaults(func=_serve_subcommand)

    # ---- 'self-loop' subcommand ----
    sl_parser = subparsers.add_parser(
        "self-loop", help="Continuously improve the codebase via self-loop agent"
    )
    sl_parser.add_argument(
        "--repo-url",
        required=True,
        metavar="URL",
        help="GitHub repo URL (e.g. https://github.com/owner/gh-issue-to-pr)",
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
        default="gemini/gemini-flash",
        metavar="MODEL",
        help="LiteLLM model for the scanner agent (default: gemini/gemini-flash)",
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
