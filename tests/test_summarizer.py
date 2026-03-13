# ABOUTME: Tests for the context extraction and summarization module.
# ABOUTME: Covers extract_context_for_summary with Claude and Codex session formats.

import json
from pathlib import Path

from agent_kitchen.summarizer import extract_context_for_summary

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CLAUDE_FIXTURES = FIXTURES_DIR / "claude_projects" / "-Users-test-repos-myproject"
CODEX_FIXTURES = FIXTURES_DIR / "codex_sessions"
SUMMARIZER_FIXTURES = FIXTURES_DIR / "summarizer"


class TestExtractContextClaude:
    """Tests for extracting context from Claude Code sessions."""

    def test_extracts_first_user_message(self):
        """First user message should be extracted."""
        ctx = extract_context_for_summary(
            str(CLAUDE_FIXTURES / "aaaa1111-2222-3333-4444-555566667777.jsonl"),
            "claude",
        )
        assert "Implement retry logic for HTTP client" in ctx

    def test_extracts_last_messages(self):
        """Last messages should be included in context."""
        ctx = extract_context_for_summary(
            str(CLAUDE_FIXTURES / "aaaa1111-2222-3333-4444-555566667777.jsonl"),
            "claude",
        )
        assert "exponential backoff" in ctx

    def test_strips_tool_use_blocks(self):
        """Tool use blocks should not appear in extracted context."""
        ctx = extract_context_for_summary(
            str(CLAUDE_FIXTURES / "bbbb1111-2222-3333-4444-555566667777.jsonl"),
            "claude",
        )
        # The assistant message has a tool_use block — it should be stripped
        assert "tool_use" not in ctx
        assert "Read" not in ctx or "Looking at the test failures" in ctx

    def test_includes_text_from_assistant_with_tool_use(self):
        """Text blocks from assistant messages with tool_use should still be included."""
        ctx = extract_context_for_summary(
            str(CLAUDE_FIXTURES / "bbbb1111-2222-3333-4444-555566667777.jsonl"),
            "claude",
        )
        assert "Looking at the test failures" in ctx

    def test_includes_turn_count(self):
        """Context should include the total turn count."""
        ctx = extract_context_for_summary(
            str(CLAUDE_FIXTURES / "aaaa1111-2222-3333-4444-555566667777.jsonl"),
            "claude",
        )
        assert "Total turns: 6" in ctx  # 3 user + 3 assistant

    def test_last_five_messages_for_long_session(self):
        """For sessions with many messages, only last 5 user+assistant messages are included."""
        ctx = extract_context_for_summary(
            str(SUMMARIZER_FIXTURES / "claude-long-session.jsonl"),
            "claude",
        )
        # 10 messages total. First user message always included, plus last 5.
        # First user message
        assert "Implement retry logic for HTTP client" in ctx
        # Last 5 messages should include the final ones
        assert "logging" in ctx
        # Earlier middle messages should NOT be in the "last messages" section
        # (but first user message is always there)

    def test_truncates_long_messages(self):
        """Messages longer than 500 chars should be truncated."""
        # Create a temp file with a very long message
        import tempfile

        long_text = "A" * 1000
        lines = [
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": long_text},
                    "timestamp": "2026-03-01T10:00:00Z",
                    "sessionId": "test",
                    "cwd": "/tmp",
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Short reply"}],
                    },
                    "timestamp": "2026-03-01T10:01:00Z",
                    "sessionId": "test",
                }
            ),
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(lines) + "\n")
            tmp_path = f.name

        ctx = extract_context_for_summary(tmp_path, "claude")
        # The long message should be truncated — shouldn't have 1000 A's
        assert "A" * 501 not in ctx
        # But should have some of the content
        assert "A" * 100 in ctx
        Path(tmp_path).unlink()

    def test_returns_string(self):
        """Extract function should return a string."""
        ctx = extract_context_for_summary(
            str(CLAUDE_FIXTURES / "aaaa1111-2222-3333-4444-555566667777.jsonl"),
            "claude",
        )
        assert isinstance(ctx, str)

    def test_nonexistent_file_returns_empty(self):
        """Non-existent file should return empty string."""
        ctx = extract_context_for_summary("/nonexistent/path.jsonl", "claude")
        assert ctx == ""

    def test_empty_file_returns_empty(self):
        """Empty file should return empty string."""
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            tmp_path = f.name

        ctx = extract_context_for_summary(tmp_path, "claude")
        assert ctx == ""
        Path(tmp_path).unlink()


class TestExtractContextCodex:
    """Tests for extracting context from Codex CLI sessions."""

    def test_extracts_first_user_message(self):
        """First user message from event_msg should be extracted."""
        codex_file = (
            CODEX_FIXTURES
            / "2026/03/10/rollout-2026-03-10T14-30-00-019c0001-aaaa-7777-bbbb-ccccddddeeee.jsonl"
        )
        ctx = extract_context_for_summary(str(codex_file), "codex")
        assert "Fix the broken tests in test_runner.py" in ctx

    def test_extracts_agent_messages(self):
        """Agent messages should be included in context."""
        codex_file = (
            CODEX_FIXTURES
            / "2026/03/10/rollout-2026-03-10T14-30-00-019c0001-aaaa-7777-bbbb-ccccddddeeee.jsonl"
        )
        ctx = extract_context_for_summary(str(codex_file), "codex")
        assert "outdated fixture" in ctx

    def test_skips_non_message_events(self):
        """Non-message event_msg records (task_started, token_count, etc.) should be skipped."""
        codex_file = (
            CODEX_FIXTURES
            / "2026/03/10/rollout-2026-03-10T14-30-00-019c0001-aaaa-7777-bbbb-ccccddddeeee.jsonl"
        )
        ctx = extract_context_for_summary(str(codex_file), "codex")
        assert "task_started" not in ctx
        assert "token_count" not in ctx

    def test_includes_turn_count(self):
        """Context should include the total turn count for Codex sessions."""
        codex_file = (
            CODEX_FIXTURES
            / "2026/03/10/rollout-2026-03-10T14-30-00-019c0001-aaaa-7777-bbbb-ccccddddeeee.jsonl"
        )
        ctx = extract_context_for_summary(str(codex_file), "codex")
        # 1 user_message + 2 agent_message = 3 turns
        assert "3" in ctx

    def test_skips_encrypted_content(self):
        """Encrypted response_item content should be skipped."""
        codex_file = (
            CODEX_FIXTURES
            / "2026/03/10/rollout-2026-03-10T14-30-00-019c0001-aaaa-7777-bbbb-ccccddddeeee.jsonl"
        )
        ctx = extract_context_for_summary(str(codex_file), "codex")
        # reasoning content "Let me look at the test file..." is from response_item,
        # not event_msg — we only use event_msg for messages
        assert "function_call" not in ctx


class TestExtractContextEdgeCases:
    """Edge case tests for context extraction."""

    def test_malformed_json_lines_skipped(self):
        """Malformed JSON lines should be skipped without crashing."""
        import tempfile

        lines = [
            "not valid json",
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "Hello"},
                    "timestamp": "2026-03-01T10:00:00Z",
                    "sessionId": "test",
                    "cwd": "/tmp",
                }
            ),
            "{broken json",
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Hi there"}],
                    },
                    "timestamp": "2026-03-01T10:01:00Z",
                    "sessionId": "test",
                }
            ),
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(lines) + "\n")
            tmp_path = f.name

        ctx = extract_context_for_summary(tmp_path, "claude")
        assert "Hello" in ctx
        assert "Hi there" in ctx
        Path(tmp_path).unlink()

    def test_session_with_only_non_message_records(self):
        """Session with no user/assistant messages should return empty."""
        import tempfile

        lines = [
            json.dumps({"type": "file-history-snapshot", "timestamp": "2026-03-01T10:00:00Z"}),
            json.dumps({"type": "progress", "timestamp": "2026-03-01T10:01:00Z"}),
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(lines) + "\n")
            tmp_path = f.name

        ctx = extract_context_for_summary(tmp_path, "claude")
        assert ctx == ""
        Path(tmp_path).unlink()

    def test_user_message_as_string_content(self):
        """Claude user messages can have content as a plain string."""
        import tempfile

        lines = [
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "Simple string content"},
                    "timestamp": "2026-03-01T10:00:00Z",
                    "sessionId": "test",
                    "cwd": "/tmp",
                }
            ),
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(lines) + "\n")
            tmp_path = f.name

        ctx = extract_context_for_summary(tmp_path, "claude")
        assert "Simple string content" in ctx
        Path(tmp_path).unlink()

    def test_user_message_as_array_content(self):
        """Claude user messages can have content as an array of blocks."""
        import tempfile

        lines = [
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "Array block content"}],
                    },
                    "timestamp": "2026-03-01T10:00:00Z",
                    "sessionId": "test",
                    "cwd": "/tmp",
                }
            ),
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(lines) + "\n")
            tmp_path = f.name

        ctx = extract_context_for_summary(tmp_path, "claude")
        assert "Array block content" in ctx
        Path(tmp_path).unlink()
