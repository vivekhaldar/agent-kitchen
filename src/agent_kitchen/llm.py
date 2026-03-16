# ABOUTME: Shared LLM call interface for Claude Haiku via Agent SDK.
# ABOUTME: Provides structured output queries with JSON schema validation.

import os

from agent_kitchen.config import HAIKU_MODEL


async def call_haiku_structured(prompt: str, schema: dict) -> dict:
    """Call Claude Haiku via the Agent SDK and return structured output as a dict.

    Args:
        prompt: The prompt to send to the model.
        schema: JSON schema for the structured output format.

    Returns:
        Parsed structured output dict, or empty dict if no output received.
    """
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    # Allow running inside a Claude Code session (the SDK refuses nested sessions)
    os.environ.pop("CLAUDECODE", None)

    result: dict = {}
    async for msg in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            model=HAIKU_MODEL,
            max_turns=2,
            output_format={
                "type": "json_schema",
                "schema": schema,
            },
        ),
    ):
        if isinstance(msg, ResultMessage) and msg.structured_output:
            result = msg.structured_output
    return result
