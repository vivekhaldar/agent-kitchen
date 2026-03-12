# ABOUTME: Extracts compact context from session JSONL files for LLM summarization.
# ABOUTME: Handles both Claude Code and Codex CLI session formats.

import json
import logging

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 500
LAST_MESSAGES_COUNT = 5


def _parse_line(line: str) -> dict | None:
    """Parse a JSONL line, returning None on failure."""
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None


def _extract_text_from_content(content) -> str:
    """Extract text from a Claude message content field.

    Content can be a plain string or an array of content blocks.
    Tool use blocks are stripped — only text blocks are included.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return " ".join(parts)
    return ""


def _truncate(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> str:
    """Truncate text to max_length, adding ellipsis if truncated."""
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


def _extract_claude_messages(lines: list[str]) -> tuple[str | None, list[tuple[str, str]], int]:
    """Extract messages from Claude Code JSONL lines.

    Returns:
        (first_user_message, all_messages_as_role_text_pairs, turn_count)
    """
    first_user_message = None
    messages: list[tuple[str, str]] = []
    turn_count = 0

    for line in lines:
        record = _parse_line(line.strip())
        if not record:
            continue

        record_type = record.get("type")
        if record_type not in ("user", "assistant"):
            continue

        turn_count += 1
        msg = record.get("message", {})
        content = msg.get("content", "")
        text = _extract_text_from_content(content)

        if not text.strip():
            continue

        role = "user" if record_type == "user" else "assistant"
        messages.append((role, text))

        if first_user_message is None and record_type == "user":
            first_user_message = text

    return first_user_message, messages, turn_count


def _extract_codex_messages(lines: list[str]) -> tuple[str | None, list[tuple[str, str]], int]:
    """Extract messages from Codex CLI JSONL lines.

    Returns:
        (first_user_message, all_messages_as_role_text_pairs, turn_count)
    """
    first_user_message = None
    messages: list[tuple[str, str]] = []
    turn_count = 0

    for line in lines:
        record = _parse_line(line.strip())
        if not record:
            continue

        record_type = record.get("type")
        if record_type != "event_msg":
            continue

        payload = record.get("payload", {})
        payload_type = payload.get("type")
        message_text = payload.get("message", "")

        if payload_type == "user_message":
            turn_count += 1
            if message_text:
                messages.append(("user", message_text))
                if first_user_message is None:
                    first_user_message = message_text

        elif payload_type == "agent_message":
            turn_count += 1
            if message_text:
                messages.append(("assistant", message_text))

    return first_user_message, messages, turn_count


def extract_context_for_summary(file_path: str, source: str) -> str:
    """Extract a compact representation of a session for LLM summarization.

    Reads the session JSONL file and extracts:
    1. The first user message (to understand the original task).
    2. The last 5 user+assistant text messages (to understand current state).
    3. Total turn count.

    Tool use blocks are stripped. Each message is truncated to 500 chars max.
    Target: under 2000 tokens total.

    Args:
        file_path: Absolute path to the session JSONL file.
        source: "claude" or "codex".

    Returns:
        Formatted string with extracted context, or empty string on failure.
    """
    try:
        with open(file_path) as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning("Failed to read %s: %s", file_path, e)
        return ""

    if not lines:
        return ""

    if source == "claude":
        first_user_message, messages, turn_count = _extract_claude_messages(lines)
    elif source == "codex":
        first_user_message, messages, turn_count = _extract_codex_messages(lines)
    else:
        logger.warning("Unknown source type: %s", source)
        return ""

    if first_user_message is None:
        return ""

    # Build the context string
    parts = []
    parts.append(f"Total turns: {turn_count}")
    parts.append(f"First user message: {_truncate(first_user_message)}")

    # Last N messages
    last_messages = messages[-LAST_MESSAGES_COUNT:]
    if last_messages:
        parts.append("Last messages:")
        for role, text in last_messages:
            parts.append(f"  [{role}]: {_truncate(text)}")

    return "\n".join(parts)
