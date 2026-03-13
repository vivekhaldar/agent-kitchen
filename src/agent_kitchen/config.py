# ABOUTME: Configuration constants and environment variable overrides for agent-kitchen.
# ABOUTME: Retrieves Claude subscription token from the `pass` password manager.

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

SCAN_WINDOW_DAYS = int(os.environ.get("AGENT_KITCHEN_SCAN_DAYS", "60"))
CACHE_DIR = Path(os.environ.get("AGENT_KITCHEN_CACHE_DIR", "~/.cache/agent-kitchen")).expanduser()
REFRESH_INTERVAL_SECONDS = int(os.environ.get("AGENT_KITCHEN_REFRESH_INTERVAL", "60"))
SERVER_PORT = int(os.environ.get("AGENT_KITCHEN_PORT", "8099"))
TERMINAL_APP = os.environ.get("AGENT_KITCHEN_TERMINAL", "ghostty")
HAIKU_MODEL = "claude-haiku-4-5-20251001"
SUMMARY_CONCURRENCY = 3

CLAUDE_PROJECTS_DIR = Path("~/.claude/projects").expanduser()
CODEX_SESSIONS_DIR = Path("~/.codex/sessions").expanduser()
CODEX_INDEX_PATH = Path("~/.codex/session_index.jsonl").expanduser()


def setup_auth() -> None:
    """Set up authentication for the Claude Agent SDK.

    Checks for credentials in this order:
    1. ANTHROPIC_API_KEY environment variable (standard API key)
    2. CLAUDE_CODE_OAUTH_TOKEN environment variable (Max subscription token)
    3. `pass` password manager at dev/CLAUDE_SUBSCRIPTION_TOKEN (fallback)

    Raises RuntimeError if no credentials are found.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        logger.info("Auth configured via ANTHROPIC_API_KEY")
        return

    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        logger.info("Auth configured via CLAUDE_CODE_OAUTH_TOKEN")
        return

    # Fallback: try the `pass` password manager
    result = subprocess.run(
        ["pass", "dev/CLAUDE_SUBSCRIPTION_TOKEN"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = result.stdout.strip()
        logger.info("Auth configured via pass password manager")
        return

    raise RuntimeError(
        "No Claude API credentials found. Set ANTHROPIC_API_KEY or "
        "CLAUDE_CODE_OAUTH_TOKEN environment variable."
    )
