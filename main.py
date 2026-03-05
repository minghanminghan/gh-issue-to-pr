#!/usr/bin/env python3
"""CLI entry point for the gh-issue-to-pr pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from pipeline import run_pipeline


def _run_subcommand(args: argparse.Namespace) -> None:
    """Execute the pipeline on a GitHub issue."""
    if not args.issue_url.startswith("https://github.com/"):
        print("Error: issue_url must be a full GitHub URL (https://github.com/...)", file=sys.stderr)
        sys.exit(2)

    if args.local_path and not Path(args.local_path).is_dir():
        print(f"Error: --local-path does not exist or is not a directory: {args.local_path}", file=sys.stderr)
        sys.exit(2)

    if args.guidelines and not Path(args.guidelines).is_file():
        print(f"Error: --guidelines file not found: {args.guidelines}", file=sys.stderr)
        sys.exit(2)

    try:
        run_dir = run_pipeline(
            issue_url=args.issue_url,
            guidelines_path=args.guidelines,
            local_path=args.local_path,
            config_path=args.config,
            max_steps=args.max_steps,
        )
        print(f"\nPipeline completed. Run artifacts: {run_dir}")
    except SystemExit as e:
        sys.exit(e.code)
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"\nPipeline error: {e}", file=sys.stderr)
        sys.exit(1)


def _serve_subcommand(args: argparse.Namespace) -> None:
    """Start the FastAPI/uvicorn web server."""
    import uvicorn
    uvicorn.run("server:app", host=args.host, port=args.port, reload=False)


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
""",
    )

    subparsers = parser.add_subparsers(dest="command")

    # ---- 'run' subcommand (original behavior) ----
    run_parser = subparsers.add_parser("run", help="Run the pipeline on a GitHub issue")
    run_parser.add_argument("issue_url", help="Full GitHub issue URL (e.g. https://github.com/owner/repo/issues/42)")
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
        "--config",
        metavar="FILE",
        help="Path to a custom agent configuration YAML file",
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
    serve_parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    serve_parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    serve_parser.set_defaults(func=_serve_subcommand)

    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
