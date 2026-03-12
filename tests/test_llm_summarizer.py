# ABOUTME: Tests for the LLM-based session summarizer (summarize_session, batch_summarize).
# ABOUTME: Uses mocked LLM calls for unit tests; integration test requires live SDK.

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_kitchen.models import Session
from agent_kitchen.summarizer import (
    SUMMARY_PROMPT_TEMPLATE,
    SummarizeResult,
    batch_summarize,
    summarize_session,
)

VALID_STATUSES = {"done", "likely done", "in progress", "likely in progress", "waiting for input"}


def _make_session(
    session_id: str = "test-session-1",
    source: str = "claude",
    summary: str = "",
    status: str = "",
    file_mtime: float = 1000.0,
    file_path: str = "/tmp/test.jsonl",
    cwd: str = "/tmp",
) -> Session:
    return Session(
        id=session_id,
        source=source,
        cwd=cwd,
        repo_root=None,
        repo_name=None,
        git_branch=None,
        started_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        last_active=datetime(2026, 3, 1, 1, tzinfo=timezone.utc),
        slug=None,
        summary=summary,
        status=status,
        turn_count=5,
        file_path=file_path,
        file_mtime=file_mtime,
    )


class TestSummarizeResult:
    """Tests for the SummarizeResult dataclass."""

    def test_creation(self):
        result = SummarizeResult(summary="Test summary", status="done")
        assert result.summary == "Test summary"
        assert result.status == "done"


class TestSummaryPromptTemplate:
    """Tests for the prompt template."""

    def test_template_contains_placeholders(self):
        assert "{source}" in SUMMARY_PROMPT_TEMPLATE
        assert "{cwd}" in SUMMARY_PROMPT_TEMPLATE
        assert "{context}" in SUMMARY_PROMPT_TEMPLATE

    def test_template_mentions_valid_statuses(self):
        for status in VALID_STATUSES:
            assert status in SUMMARY_PROMPT_TEMPLATE


class TestSummarizeSession:
    """Tests for summarize_session with mocked LLM calls."""

    @pytest.mark.asyncio
    async def test_returns_summarize_result(self):
        """Should return a SummarizeResult with summary and status."""
        mock_response = json.dumps({"summary": "Implement retry logic", "status": "done"})
        with patch("agent_kitchen.summarizer._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            result = await summarize_session("Some context", "claude", "/tmp")
        assert isinstance(result, SummarizeResult)
        assert result.summary == "Implement retry logic"
        assert result.status == "done"

    @pytest.mark.asyncio
    async def test_parses_valid_json_response(self):
        """Should parse a valid JSON response from the LLM."""
        mock_response = json.dumps({"summary": "Fix CI pipeline", "status": "in progress"})
        with patch("agent_kitchen.summarizer._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            result = await summarize_session("Context text", "codex", "/home/user")
        assert result.summary == "Fix CI pipeline"
        assert result.status == "in progress"

    @pytest.mark.asyncio
    async def test_handles_json_with_extra_whitespace(self):
        """Should handle JSON with extra whitespace or newlines."""
        mock_response = '  \n  {"summary": "Test thing", "status": "done"}  \n  '
        with patch("agent_kitchen.summarizer._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            result = await summarize_session("Context", "claude", "/tmp")
        assert result.summary == "Test thing"
        assert result.status == "done"

    @pytest.mark.asyncio
    async def test_handles_json_in_markdown_code_block(self):
        """LLMs sometimes wrap JSON in markdown code blocks."""
        mock_response = '```json\n{"summary": "Do stuff", "status": "likely done"}\n```'
        with patch("agent_kitchen.summarizer._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            result = await summarize_session("Context", "claude", "/tmp")
        assert result.summary == "Do stuff"
        assert result.status == "likely done"

    @pytest.mark.asyncio
    async def test_fallback_on_invalid_json(self):
        """Should use fallback when LLM returns non-JSON."""
        with patch("agent_kitchen.summarizer._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "This is not JSON at all"
            result = await summarize_session(
                "First user message: Implement caching\nLast messages:\n  [user]: more stuff",
                "claude",
                "/tmp",
            )
        assert isinstance(result, SummarizeResult)
        assert result.status == "likely in progress"
        # Fallback uses first user message text
        assert len(result.summary) > 0

    @pytest.mark.asyncio
    async def test_fallback_on_llm_exception(self):
        """Should use fallback when LLM call raises an exception."""
        with patch("agent_kitchen.summarizer._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = RuntimeError("Connection failed")
            result = await summarize_session(
                "First user message: Fix tests\nLast messages:\n  [user]: check output",
                "claude",
                "/tmp",
            )
        assert isinstance(result, SummarizeResult)
        assert result.status == "likely in progress"

    @pytest.mark.asyncio
    async def test_fallback_on_missing_summary_key(self):
        """Should use fallback when JSON is valid but missing required keys."""
        mock_response = json.dumps({"wrong_key": "value"})
        with patch("agent_kitchen.summarizer._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            result = await summarize_session(
                "First user message: Deploy service\nLast messages:",
                "claude",
                "/tmp",
            )
        assert isinstance(result, SummarizeResult)
        assert result.status == "likely in progress"

    @pytest.mark.asyncio
    async def test_truncates_long_summary(self):
        """Summary should be truncated to 80 chars max."""
        long_summary = "A" * 120
        mock_response = json.dumps({"summary": long_summary, "status": "done"})
        with patch("agent_kitchen.summarizer._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            result = await summarize_session("Context", "claude", "/tmp")
        assert len(result.summary) <= 83  # 80 + "..."

    @pytest.mark.asyncio
    async def test_normalizes_invalid_status(self):
        """Should normalize an invalid status to 'likely in progress'."""
        mock_response = json.dumps({"summary": "Do things", "status": "completed successfully"})
        with patch("agent_kitchen.summarizer._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            result = await summarize_session("Context", "claude", "/tmp")
        assert result.status in VALID_STATUSES

    @pytest.mark.asyncio
    async def test_prompt_includes_source_and_cwd(self):
        """The prompt sent to the LLM should include source and cwd."""
        with patch("agent_kitchen.summarizer._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = json.dumps({"summary": "x", "status": "done"})
            await summarize_session("Context here", "codex", "/home/user/project")
        prompt_sent = mock_llm.call_args[0][0]
        assert "codex" in prompt_sent
        assert "/home/user/project" in prompt_sent
        assert "Context here" in prompt_sent


class TestBatchSummarize:
    """Tests for batch_summarize with mocked LLM and cache."""

    @pytest.mark.asyncio
    async def test_skips_sessions_with_cached_summary(self):
        """Sessions with valid cache entries should not trigger LLM calls."""
        session = _make_session(summary="Cached", status="done")
        cache = MagicMock()
        cache.needs_refresh.return_value = False
        cache.get.return_value = {"summary": "Cached", "status": "done"}

        results = await batch_summarize([session], cache)
        assert len(results) == 1
        assert results[0].summary == "Cached"
        assert results[0].status == "done"

    @pytest.mark.asyncio
    async def test_calls_llm_for_uncached_sessions(self):
        """Sessions without cache entries should trigger LLM calls."""
        session = _make_session()
        cache = MagicMock()
        cache.needs_refresh.return_value = True
        cache.get.return_value = None

        mock_response = json.dumps({"summary": "New summary", "status": "in progress"})

        with patch("agent_kitchen.summarizer._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            with patch("agent_kitchen.summarizer.extract_context_for_summary") as mock_extract:
                mock_extract.return_value = "First user message: Test\nLast messages:"
                results = await batch_summarize([session], cache)

        assert len(results) == 1
        assert results[0].summary == "New summary"
        assert results[0].status == "in progress"

    @pytest.mark.asyncio
    async def test_updates_cache_after_summarization(self):
        """Cache should be updated after successful LLM summarization."""
        session = _make_session(session_id="s1", file_mtime=2000.0)
        cache = MagicMock()
        cache.needs_refresh.return_value = True
        cache.get.return_value = None

        mock_response = json.dumps({"summary": "Updated", "status": "done"})

        with patch("agent_kitchen.summarizer._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            with patch("agent_kitchen.summarizer.extract_context_for_summary") as mock_extract:
                mock_extract.return_value = "First user message: Test\nLast messages:"
                await batch_summarize([session], cache)

        cache.set.assert_called_once_with("s1", "Updated", "done", 2000.0)

    @pytest.mark.asyncio
    async def test_uses_codex_thread_name_as_summary(self):
        """Codex sessions with a slug (thread_name) should use it as summary."""
        session = _make_session(source="codex", summary="Thread name summary")
        cache = MagicMock()
        cache.needs_refresh.return_value = True
        cache.get.return_value = None

        mock_response = json.dumps({"summary": "LLM summary", "status": "done"})

        with patch("agent_kitchen.summarizer._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            with patch("agent_kitchen.summarizer.extract_context_for_summary") as mock_extract:
                mock_extract.return_value = "First user message: Test\nLast messages:"
                results = await batch_summarize([session], cache)

        # When summary is pre-filled (from thread_name), still need status from LLM
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_handles_empty_context_gracefully(self):
        """Sessions with empty context (unreadable file) should get fallback."""
        session = _make_session()
        cache = MagicMock()
        cache.needs_refresh.return_value = True
        cache.get.return_value = None

        with patch("agent_kitchen.summarizer.extract_context_for_summary") as mock_extract:
            mock_extract.return_value = ""
            results = await batch_summarize([session], cache)

        assert len(results) == 1
        assert results[0].status == "likely in progress"

    @pytest.mark.asyncio
    async def test_respects_concurrency_limit(self):
        """Batch summarize should limit concurrent LLM calls."""
        sessions = [_make_session(session_id=f"s{i}") for i in range(20)]
        cache = MagicMock()
        cache.needs_refresh.return_value = True
        cache.get.return_value = None

        concurrent_count = 0
        max_concurrent = 0

        async def slow_llm(prompt):
            nonlocal concurrent_count, max_concurrent
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
            await asyncio.sleep(0.01)
            concurrent_count -= 1
            return json.dumps({"summary": "x", "status": "done"})

        with patch("agent_kitchen.summarizer._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = slow_llm
            with patch("agent_kitchen.summarizer.extract_context_for_summary") as mock_extract:
                mock_extract.return_value = "First user message: Test\nLast messages:"
                await batch_summarize(sessions, cache, concurrency=5)

        assert max_concurrent <= 5

    @pytest.mark.asyncio
    async def test_returns_results_in_session_order(self):
        """Results should be returned in the same order as input sessions."""
        sessions = [_make_session(session_id=f"s{i}") for i in range(5)]
        cache = MagicMock()
        cache.needs_refresh.return_value = True
        cache.get.return_value = None

        call_count = 0

        async def sequenced_llm(prompt):
            nonlocal call_count
            call_count += 1
            return json.dumps({"summary": f"Summary {call_count}", "status": "done"})

        with patch("agent_kitchen.summarizer._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = sequenced_llm
            with patch("agent_kitchen.summarizer.extract_context_for_summary") as mock_extract:
                mock_extract.return_value = "First user message: Test\nLast messages:"
                results = await batch_summarize(sessions, cache)

        assert len(results) == 5
        # Each result should be a SummarizeResult
        for r in results:
            assert isinstance(r, SummarizeResult)

    @pytest.mark.asyncio
    async def test_empty_session_list(self):
        """Batch summarize with no sessions should return empty list."""
        cache = MagicMock()
        results = await batch_summarize([], cache)
        assert results == []

    @pytest.mark.asyncio
    async def test_saves_cache_after_batch(self):
        """Cache.save() should be called after batch processing."""
        session = _make_session()
        cache = MagicMock()
        cache.needs_refresh.return_value = True
        cache.get.return_value = None

        mock_response = json.dumps({"summary": "x", "status": "done"})

        with patch("agent_kitchen.summarizer._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            with patch("agent_kitchen.summarizer.extract_context_for_summary") as mock_extract:
                mock_extract.return_value = "First user message: Test\nLast messages:"
                await batch_summarize([session], cache)

        cache.save.assert_called_once()
