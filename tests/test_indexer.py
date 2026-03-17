# ABOUTME: Tests for the session pre-indexer that batch-summarizes via LLM.
# ABOUTME: Covers dry-run mode, force mode, periodic save, and error handling.

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_kitchen.models import Session


def _make_session(
    session_id: str = "s1",
    source: str = "claude",
    file_mtime: float = 1000.0,
) -> Session:
    return Session(
        id=session_id,
        source=source,
        cwd="/tmp/repo",
        repo_root="/tmp/repo",
        repo_name="repo",
        git_branch="main",
        started_at=datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc),
        last_active=datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc),
        slug=None,
        summary="",
        status="",
        turn_count=5,
        file_path="/tmp/test.jsonl",
        file_mtime=file_mtime,
    )


class TestRunIndexer:
    """Tests for the run_indexer async function."""

    @pytest.mark.asyncio
    async def test_no_sessions_exits_early(self):
        from agent_kitchen.indexer import run_indexer

        with (
            patch("agent_kitchen.indexer.scan_claude_sessions", return_value=[]),
            patch("agent_kitchen.indexer.scan_codex_sessions", return_value=[]),
        ):
            # Should not raise, just return early
            await run_indexer(scan_days=7, concurrency=3, dry_run=False, force=False)

    @pytest.mark.asyncio
    async def test_dry_run_skips_summarization(self):
        from agent_kitchen.indexer import run_indexer

        sessions = [_make_session("s1"), _make_session("s2")]
        mock_cache = MagicMock()
        mock_cache.needs_refresh.return_value = True

        with (
            patch("agent_kitchen.indexer.scan_claude_sessions", return_value=sessions),
            patch("agent_kitchen.indexer.scan_codex_sessions", return_value=[]),
            patch("agent_kitchen.indexer.SummaryCache", return_value=mock_cache),
            patch("agent_kitchen.indexer.summarize_session", new_callable=AsyncMock) as mock_llm,
            patch("agent_kitchen.indexer.setup_auth"),
        ):
            await run_indexer(scan_days=7, concurrency=3, dry_run=True, force=False)
            # LLM should never be called in dry-run
            mock_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_force_mode_resummarizes_all(self):
        from agent_kitchen.indexer import run_indexer
        from agent_kitchen.summarizer import SummarizeResult

        sessions = [_make_session("s1"), _make_session("s2")]
        mock_cache = MagicMock()
        # Even if cache says "don't need refresh", force should override
        mock_cache.needs_refresh.return_value = False

        mock_result = SummarizeResult(summary="Test summary", status="done")

        with (
            patch("agent_kitchen.indexer.scan_claude_sessions", return_value=sessions),
            patch("agent_kitchen.indexer.scan_codex_sessions", return_value=[]),
            patch("agent_kitchen.indexer.SummaryCache", return_value=mock_cache),
            patch("agent_kitchen.indexer.setup_auth"),
            patch("agent_kitchen.indexer.extract_context_for_summary", return_value="context"),
            patch(
                "agent_kitchen.indexer.summarize_session",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            await run_indexer(scan_days=7, concurrency=3, dry_run=False, force=True)
            # Cache should be saved at the end
            mock_cache.save.assert_called()
            # Both sessions should have been set in cache
            assert mock_cache.set.call_count == 2

    @pytest.mark.asyncio
    async def test_all_cached_skips_work(self):
        from agent_kitchen.indexer import run_indexer

        sessions = [_make_session("s1")]
        mock_cache = MagicMock()
        mock_cache.needs_refresh.return_value = False

        with (
            patch("agent_kitchen.indexer.scan_claude_sessions", return_value=sessions),
            patch("agent_kitchen.indexer.scan_codex_sessions", return_value=[]),
            patch("agent_kitchen.indexer.SummaryCache", return_value=mock_cache),
            patch("agent_kitchen.indexer.summarize_session", new_callable=AsyncMock) as mock_llm,
        ):
            await run_indexer(scan_days=7, concurrency=3, dry_run=False, force=False)
            mock_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_periodic_save_during_indexing(self):
        """Cache should be saved periodically during indexing (every 25 sessions)."""
        from agent_kitchen.indexer import run_indexer
        from agent_kitchen.summarizer import SummarizeResult

        # Create 30 sessions to trigger at least one periodic save (at 25)
        sessions = [_make_session(f"s{i}") for i in range(30)]
        mock_cache = MagicMock()
        mock_cache.needs_refresh.return_value = True

        mock_result = SummarizeResult(summary="Summary", status="done")

        with (
            patch("agent_kitchen.indexer.scan_claude_sessions", return_value=sessions),
            patch("agent_kitchen.indexer.scan_codex_sessions", return_value=[]),
            patch("agent_kitchen.indexer.SummaryCache", return_value=mock_cache),
            patch("agent_kitchen.indexer.setup_auth"),
            patch("agent_kitchen.indexer.extract_context_for_summary", return_value="ctx"),
            patch(
                "agent_kitchen.indexer.summarize_session",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            await run_indexer(scan_days=7, concurrency=3, dry_run=False, force=False)
            # At least 2 saves: 1 periodic (at 25) + 1 final
            assert mock_cache.save.call_count >= 2

    @pytest.mark.asyncio
    async def test_llm_failure_uses_fallback(self):
        """Failed LLM calls should use fallback and still cache the result."""
        from agent_kitchen.indexer import run_indexer

        sessions = [_make_session("s1")]
        mock_cache = MagicMock()
        mock_cache.needs_refresh.return_value = True

        with (
            patch("agent_kitchen.indexer.scan_claude_sessions", return_value=sessions),
            patch("agent_kitchen.indexer.scan_codex_sessions", return_value=[]),
            patch("agent_kitchen.indexer.SummaryCache", return_value=mock_cache),
            patch("agent_kitchen.indexer.setup_auth"),
            patch(
                "agent_kitchen.indexer.extract_context_for_summary",
                return_value="some context",
            ),
            patch(
                "agent_kitchen.indexer.summarize_session",
                new_callable=AsyncMock,
                side_effect=RuntimeError("API error"),
            ),
        ):
            await run_indexer(scan_days=7, concurrency=3, dry_run=False, force=False)
            # Should still cache fallback result
            mock_cache.set.assert_called_once()
            mock_cache.save.assert_called()

    @pytest.mark.asyncio
    async def test_empty_context_uses_fallback(self):
        """Sessions with no extractable context should get a fallback summary."""
        from agent_kitchen.indexer import run_indexer

        sessions = [_make_session("s1")]
        mock_cache = MagicMock()
        mock_cache.needs_refresh.return_value = True

        with (
            patch("agent_kitchen.indexer.scan_claude_sessions", return_value=sessions),
            patch("agent_kitchen.indexer.scan_codex_sessions", return_value=[]),
            patch("agent_kitchen.indexer.SummaryCache", return_value=mock_cache),
            patch("agent_kitchen.indexer.setup_auth"),
            patch("agent_kitchen.indexer.extract_context_for_summary", return_value=""),
            patch("agent_kitchen.indexer.summarize_session", new_callable=AsyncMock) as mock_llm,
        ):
            await run_indexer(scan_days=7, concurrency=3, dry_run=False, force=False)
            # LLM should not be called for empty context
            mock_llm.assert_not_called()
            # But cache should still be set with fallback
            mock_cache.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_auth_failure_exits(self):
        """Auth failure should cause sys.exit(1)."""
        from agent_kitchen.indexer import run_indexer

        sessions = [_make_session("s1")]
        mock_cache = MagicMock()
        mock_cache.needs_refresh.return_value = True

        with (
            patch("agent_kitchen.indexer.scan_claude_sessions", return_value=sessions),
            patch("agent_kitchen.indexer.scan_codex_sessions", return_value=[]),
            patch("agent_kitchen.indexer.SummaryCache", return_value=mock_cache),
            patch("agent_kitchen.indexer.setup_auth", side_effect=RuntimeError("No API key")),
            pytest.raises(SystemExit) as exc_info,
        ):
            await run_indexer(scan_days=7, concurrency=3, dry_run=False, force=False)
        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_scanner_failure_continues(self):
        """If one scanner fails, the other's sessions should still be indexed."""
        from agent_kitchen.indexer import run_indexer
        from agent_kitchen.summarizer import SummarizeResult

        codex_session = _make_session("codex-1", source="codex")
        mock_cache = MagicMock()
        mock_cache.needs_refresh.return_value = True

        mock_result = SummarizeResult(summary="Test", status="done")

        with (
            patch(
                "agent_kitchen.indexer.scan_claude_sessions",
                side_effect=RuntimeError("Claude dir missing"),
            ),
            patch("agent_kitchen.indexer.scan_codex_sessions", return_value=[codex_session]),
            patch("agent_kitchen.indexer.SummaryCache", return_value=mock_cache),
            patch("agent_kitchen.indexer.setup_auth"),
            patch("agent_kitchen.indexer.extract_context_for_summary", return_value="ctx"),
            patch(
                "agent_kitchen.indexer.summarize_session",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            await run_indexer(scan_days=7, concurrency=3, dry_run=False, force=False)
            mock_cache.set.assert_called_once()
