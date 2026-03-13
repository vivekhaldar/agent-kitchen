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


def get_claude_token() -> str:
    """Retrieve Claude subscription token from the pass password manager."""
    result = subprocess.run(
        ["pass", "dev/CLAUDE_SUBSCRIPTION_TOKEN"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("pass returned exit code %d: %s", result.returncode, result.stderr.strip())
        raise RuntimeError(
            "Failed to retrieve Claude token from pass. "
            "Ensure `pass dev/CLAUDE_SUBSCRIPTION_TOKEN` is set."
        )
    return result.stdout.strip()


def setup_auth() -> None:
    """Set up authentication for the Claude Agent SDK via Max subscription."""
    token = get_claude_token()
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token
    # Unset API key to avoid billing the API account instead of using Max subscription
    os.environ.pop("ANTHROPIC_API_KEY", None)
    logger.info("Auth configured via Max subscription token")
