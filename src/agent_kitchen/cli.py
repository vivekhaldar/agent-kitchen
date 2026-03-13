# ABOUTME: CLI entry point for the agent-kitchen command.
# ABOUTME: Parses --port, --scan-days, --no-open flags and starts the server.

import argparse
import logging
import webbrowser

import uvicorn

from agent_kitchen import config
from agent_kitchen.config import setup_auth
from agent_kitchen.server import create_app


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the agent-kitchen CLI."""
    parser = argparse.ArgumentParser(
        prog="agent-kitchen",
        description="Dashboard for monitoring AI coding agent sessions",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=config.SERVER_PORT,
        help=f"Port to serve on (default: {config.SERVER_PORT})",
    )
    parser.add_argument(
        "--scan-days",
        type=int,
        default=config.SCAN_WINDOW_DAYS,
        help=f"Number of days to scan back (default: {config.SCAN_WINDOW_DAYS})",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        default=False,
        help="Don't auto-open the dashboard in a browser",
    )
    return parser


def run_cli(argv: list[str] | None = None) -> None:
    """Run the agent-kitchen server with CLI argument parsing.

    Args:
        argv: Command-line arguments. If None, uses sys.argv.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    # Configure logging for the whole package
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    # Suppress noisy Claude Agent SDK transport logs
    logging.getLogger("claude_agent_sdk").setLevel(logging.WARNING)

    # Apply CLI overrides to config
    config.SCAN_WINDOW_DAYS = args.scan_days
    config.SERVER_PORT = args.port

    print("Agent Kitchen starting...")

    # Set up LLM authentication
    try:
        setup_auth()
        print("Authentication configured via Max subscription.")
    except RuntimeError as e:
        print(f"Warning: {e}")
        print("LLM summarization will use fallback mode.")

    # Create the app (initial scan runs in the background via lifespan)
    app = create_app()

    url = f"http://localhost:{args.port}"
    print(f"Dashboard starting at {url}")

    # Open browser unless --no-open
    if not args.no_open:
        webbrowser.open(url)

    # Start the server
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


def main() -> None:
    """Entry point for the agent-kitchen console script."""
    run_cli()
