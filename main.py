#!/usr/bin/env python3
"""CLI entry point for the gh-issue-to-pr pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Turn a GitHub issue into a pull request using AI agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Using a GitHub issue URL + remote repo
  python main.py https://github.com/owner/repo/issues/42 https://github.com/owner/repo

  # Using a local repo checkout
  python main.py https://github.com/owner/repo/issues/42 https://github.com/owner/repo \\
      --local-path /path/to/local/repo

  # With contribution guidelines and custom budget
  python main.py https://github.com/owner/repo/issues/42 https://github.com/owner/repo \\
      --guidelines CONTRIBUTING.md --budget 5.00
""",
    )

    parser.add_argument("issue_url", help="Full GitHub issue URL (e.g. https://github.com/owner/repo/issues/42)")
    parser.add_argument("repo_url", help="GitHub repo URL (e.g. https://github.com/owner/repo)")
    parser.add_argument(
        "--local-path",
        metavar="PATH",
        help="Use an existing local repo checkout instead of cloning",
    )
    parser.add_argument(
        "--guidelines",
        metavar="FILE",
        help="Path to contribution guidelines file (e.g. CONTRIBUTING.md)",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=2.00,
        metavar="USD",
        help="Cost budget in USD (default: 2.00)",
    )

    args = parser.parse_args()

    # Validate inputs
    if not args.issue_url.startswith("https://github.com/"):
        parser.error("issue_url must be a full GitHub URL (https://github.com/...)")

    if args.local_path and not Path(args.local_path).is_dir():
        parser.error(f"--local-path does not exist or is not a directory: {args.local_path}")

    if args.guidelines and not Path(args.guidelines).is_file():
        parser.error(f"--guidelines file not found: {args.guidelines}")

    # Import here to avoid slow startup for --help
    from pipeline import run_pipeline

    try:
        run_dir = run_pipeline(
            repo_url=args.repo_url,
            issue_url=args.issue_url,
            guidelines_path=args.guidelines,
            local_path=args.local_path,
        )
        print(f"\nPipeline completed. Run artifacts: {run_dir}")
    except SystemExit as e:
        # Non-zero exit from report step
        sys.exit(e.code)
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"\nPipeline error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
