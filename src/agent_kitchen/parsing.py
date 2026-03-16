# ABOUTME: Shared JSONL parsing utilities for reading session records.
# ABOUTME: Provides JSON line parsing and content-block text extraction.

import json


def parse_jsonl_line(line: str) -> dict | None:
    """Parse a single JSONL line, returning None on failure."""
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None


def extract_text_from_content(content) -> str:
    """Extract text from a Claude message content field.

    Content can be a plain string or an array of content blocks.
    Tool use blocks are stripped — only text blocks are included.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""
