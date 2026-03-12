# ABOUTME: Reads ~/.claude session JSONL files and extracts Session metadata.
# ABOUTME: Walks project directories, parses records, decodes paths, and yields Session objects.

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from agent_kitchen.config import CLAUDE_PROJECTS_DIR
from agent_kitchen.models import Session

logger = logging.getLogger(__name__)


def decode_claude_project_path(dirname: str) -> str:
    """Decode a Claude project directory name to a filesystem path.

    The directory name encodes the working directory path with `-` as separator.
    The leading `-` maps to `/`, and each subsequent `-` maps to `/`.
    Example: "-Users-haldar-repos-gh-foo" → "/Users/haldar/repos/gh/foo"
    """
    return dirname.replace("-", "/")


def _read_last_line(file_path: str) -> str | None:
    """Read the last non-empty line of a file efficiently using tail."""
    try:
        result = subprocess.run(
            ["tail", "-1", file_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _parse_jsonl_line(line: str) -> dict | None:
    """Parse a single JSONL line, returning None on failure."""
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None


def _parse_timestamp(ts: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp string to a timezone-aware datetime."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def _scan_single_claude_file(file_path: Path) -> Session | None:
    """Parse a single Claude Code JSONL session file into a Session object."""
    try:
        with open(file_path) as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning("Failed to read %s: %s", file_path, e)
        return None

    if not lines:
        return None

    # Parse first line for initial metadata
    first_record = _parse_jsonl_line(lines[0])
    if not first_record:
        logger.warning("Failed to parse first line of %s", file_path)
        return None

    session_id = file_path.stem  # UUID from filename
    cwd = None
    git_branch = None
    slug = None
    started_at = None
    last_active = None
    turn_count = 0

    # Scan all records for metadata and turn count
    for line in lines:
        record = _parse_jsonl_line(line)
        if not record:
            continue

        record_type = record.get("type")
        timestamp = _parse_timestamp(record.get("timestamp"))

        if record_type in ("user", "assistant"):
            turn_count += 1

            # Track timestamps
            if timestamp:
                if started_at is None or timestamp < started_at:
                    started_at = timestamp
                if last_active is None or timestamp > last_active:
                    last_active = timestamp

        if record_type == "user":
            # Extract metadata from user records
            if cwd is None:
                cwd = record.get("cwd")
            if git_branch is None:
                git_branch = record.get("gitBranch")
            if slug is None:
                slug = record.get("slug")

    if started_at is None or last_active is None:
        # No valid timestamped user/assistant records found
        return None

    # Fall back to decoded directory name for cwd if not in records
    if cwd is None:
        cwd = decode_claude_project_path(file_path.parent.name)

    file_mtime = os.path.getmtime(file_path)

    return Session(
        id=session_id,
        source="claude",
        cwd=cwd,
        repo_root=None,  # Populated later by git_status module
        repo_name=None,
        git_branch=git_branch,
        started_at=started_at,
        last_active=last_active,
        slug=slug,
        summary="",
        status="",
        turn_count=turn_count,
        file_path=str(file_path),
        file_mtime=file_mtime,
    )


def scan_claude_sessions(
    since: datetime,
    projects_dir: Path | None = None,
) -> list[Session]:
    """Scan Claude Code project directories for session JSONL files.

    Args:
        since: Only include sessions with file mtime after this datetime.
        projects_dir: Override the default ~/.claude/projects directory (for testing).

    Returns:
        List of Session objects, one per session file found.
    """
    projects_dir = projects_dir or CLAUDE_PROJECTS_DIR

    if not projects_dir.exists():
        logger.info("Claude projects directory not found: %s", projects_dir)
        return []

    since_ts = since.timestamp()
    sessions: list[Session] = []

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue

        # Only look at JSONL files directly under the project directory
        for jsonl_file in project_dir.glob("*.jsonl"):
            # Skip if file is too old
            try:
                mtime = os.path.getmtime(jsonl_file)
            except OSError:
                continue
            if mtime < since_ts:
                continue

            session = _scan_single_claude_file(jsonl_file)
            if session:
                sessions.append(session)

    return sessions
