# ABOUTME: Extracts compact context from session JSONL files for LLM summarization.
# ABOUTME: Calls Claude Haiku via Agent SDK to generate summaries and classify session status.

import asyncio
import logging
from dataclasses import dataclass

from agent_kitchen.config import SUMMARY_CONCURRENCY
from agent_kitchen.parsing import extract_text_from_content, parse_jsonl_line

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 500
LAST_MESSAGES_COUNT = 5


def _parse_line(line: str) -> dict | None:
    """Parse a JSONL line, returning None on failure."""
    return parse_jsonl_line(line)


def _extract_text_from_content(content) -> str:
    """Extract text from a Claude message content field.

    Content can be a plain string or an array of content blocks.
    Tool use blocks are stripped — only text blocks are included.
    """
    return extract_text_from_content(content)


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


# --- LLM Summarization ---

VALID_STATUSES = {"done", "likely done", "in progress", "likely in progress", "waiting for input"}

SUMMARY_PROMPT_TEMPLATE = """\
You are analyzing a coding agent session to generate a brief summary and status.

Session context:
- Source: {source}
- Working directory: {cwd}
{context}

Rules for status:
- "done": The task was clearly completed. Agent confirmed completion or user acknowledged it.
- "likely done": The task appears complete but there's no explicit confirmation.
- "in progress": Work is actively ongoing, not yet complete.
- "likely in progress": Some work happened but it's unclear if more is needed.
- "waiting for input": The last assistant message asks the user a question or presents options.\
"""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "One-line summary of what this session is about, max 80 chars",
        },
        "status": {
            "type": "string",
            "enum": [
                "done",
                "likely done",
                "in progress",
                "likely in progress",
                "waiting for input",
            ],
        },
    },
    "required": ["summary", "status"],
}


@dataclass
class SummarizeResult:
    """Result of LLM summarization for a session."""

    summary: str
    status: str


async def _call_llm(prompt: str) -> dict:
    """Call Claude Haiku via the Agent SDK and return structured output as a dict."""
    from agent_kitchen.llm import call_haiku_structured

    return await call_haiku_structured(prompt, SUMMARY_SCHEMA)


def _make_fallback(context: str) -> SummarizeResult:
    """Generate a fallback summary from the context string."""
    # Try to extract the first user message from the context
    summary = ""
    for line in context.split("\n"):
        if line.startswith("First user message:"):
            summary = line[len("First user message:") :].strip()
            break
    if not summary:
        summary = context[:80] if context else "Unknown session"
    return SummarizeResult(summary=_truncate(summary, 80), status="likely in progress")


async def summarize_session(context: str, source: str, cwd: str) -> SummarizeResult:
    """Generate a summary and status for a session using Claude Haiku.

    Args:
        context: Extracted context string from extract_context_for_summary.
        source: "claude" or "codex".
        cwd: Working directory of the session.

    Returns:
        SummarizeResult with summary and status fields.
    """
    prompt = SUMMARY_PROMPT_TEMPLATE.format(source=source, cwd=cwd, context=context)

    try:
        data = await _call_llm(prompt)
    except Exception:
        logger.warning("LLM call failed for session in %s", cwd, exc_info=True)
        return _make_fallback(context)

    if not data:
        logger.warning("Empty structured output for session in %s", cwd)
        return _make_fallback(context)

    summary = _truncate(str(data.get("summary", "")), 80)
    status = str(data.get("status", ""))
    if status not in VALID_STATUSES:
        status = "likely in progress"

    return SummarizeResult(summary=summary, status=status)


async def batch_summarize(
    sessions: list,
    cache,
    concurrency: int = SUMMARY_CONCURRENCY,
) -> list[SummarizeResult]:
    """Summarize multiple sessions concurrently, using cache where possible.

    Args:
        sessions: List of Session objects to summarize.
        cache: SummaryCache instance for caching results.
        concurrency: Max concurrent LLM calls.

    Returns:
        List of SummarizeResult in the same order as input sessions.
    """
    if not sessions:
        return []

    results: list[SummarizeResult | None] = [None] * len(sessions)
    to_summarize: list[tuple[int, object]] = []  # (index, session) pairs needing LLM

    # Check cache first
    for i, session in enumerate(sessions):
        if not cache.needs_refresh(session.id, session.file_mtime):
            cached = cache.get(session.id)
            if cached:
                results[i] = SummarizeResult(summary=cached["summary"], status=cached["status"])
                continue
        to_summarize.append((i, session))

    if not to_summarize:
        return results  # type: ignore[return-value]

    semaphore = asyncio.Semaphore(concurrency)

    async def _summarize_one(idx: int, session) -> None:
        context = extract_context_for_summary(session.file_path, session.source)
        if not context:
            result = SummarizeResult(
                summary=session.summary or "Unknown session",
                status="likely in progress",
            )
            results[idx] = result
            return

        async with semaphore:
            result = await summarize_session(context, session.source, session.cwd)

        # If session already has a summary (e.g. Codex thread_name), keep it unless LLM is better
        if session.summary and not result.summary:
            result = SummarizeResult(summary=session.summary, status=result.status)

        results[idx] = result
        cache.set(session.id, result.summary, result.status, session.file_mtime)

    tasks = [_summarize_one(idx, session) for idx, session in to_summarize]
    await asyncio.gather(*tasks)

    cache.save()
    return results  # type: ignore[return-value]
