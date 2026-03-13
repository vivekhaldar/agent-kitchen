# ABOUTME: CLI entry point for agent-kitchen with subcommands.
# ABOUTME: "web" launches the dashboard, "index" pre-summarizes sessions.

import argparse
import asyncio
import logging
import sys
import webbrowser

import uvicorn

from agent_kitchen import config
from agent_kitchen.config import setup_auth
from agent_kitchen.indexer import run_indexer
from agent_kitchen.server import create_app


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the argument parser with web and index subcommands."""
    parser = argparse.ArgumentParser(
        prog="agent-kitchen",
        description="Dashboard for monitoring AI coding agent sessions",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- web subcommand ---
    web = subparsers.add_parser(
        "web",
        help="Launch the web dashboard (uses cached summaries by default)",
    )
    web.add_argument(
        "--port",
        type=int,
        default=config.SERVER_PORT,
        help=f"Port to serve on (default: {config.SERVER_PORT})",
    )
    web.add_argument(
        "--scan-days",
        type=int,
        default=config.SCAN_WINDOW_DAYS,
        help=f"Number of days to scan back (default: {config.SCAN_WINDOW_DAYS})",
    )
    web.add_argument(
        "--no-open",
        action="store_true",
        default=False,
        help="Don't auto-open the dashboard in a browser",
    )
    web.add_argument(
        "--summarize",
        action="store_true",
        default=False,
        help="Enable background LLM summarization (off by default)",
    )

    # --- index subcommand ---
    index = subparsers.add_parser(
        "index",
        help="Pre-index and summarize sessions to populate the cache",
    )
    index.add_argument(
        "--scan-days",
        type=int,
        default=config.SCAN_WINDOW_DAYS,
        help=f"Days of history to scan (default: {config.SCAN_WINDOW_DAYS})",
    )
    index.add_argument(
        "--concurrency",
        type=int,
        default=config.SUMMARY_CONCURRENCY,
        help=f"Max concurrent LLM calls (default: {config.SUMMARY_CONCURRENCY})",
    )
    index.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report what would be indexed, but skip LLM calls",
    )
    index.add_argument(
        "--force",
        action="store_true",
        help="Re-summarize all sessions, ignoring cache",
    )

    return parser


def _run_web(args: argparse.Namespace) -> None:
    """Launch the web dashboard."""
    config.SCAN_WINDOW_DAYS = args.scan_days
    config.SERVER_PORT = args.port

    print("Agent Kitchen starting...")

    if args.summarize:
        try:
            setup_auth()
            print("Authentication configured via Max subscription.")
        except RuntimeError as e:
            print(f"Warning: {e}")
            print("LLM summarization will use fallback mode.")

    app = create_app(summarize=args.summarize)

    url = f"http://localhost:{args.port}"
    print(f"Dashboard starting at {url}")

    if not args.no_open:
        webbrowser.open(url)

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


def _run_index(args: argparse.Namespace) -> None:
    """Pre-index and summarize sessions."""
    asyncio.run(
        run_indexer(
            scan_days=args.scan_days,
            concurrency=args.concurrency,
            dry_run=args.dry_run,
            force=args.force,
        )
    )


def run_cli(argv: list[str] | None = None) -> None:
    """Parse arguments and dispatch to the appropriate subcommand."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    logging.getLogger("claude_agent_sdk").setLevel(logging.WARNING)

    if args.command == "web":
        _run_web(args)
    elif args.command == "index":
        _run_index(args)


def main() -> None:
    """Entry point for the agent-kitchen console script."""
    run_cli()
