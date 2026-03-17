# ABOUTME: Tests for repo-level timeline generation (fallback and LLM-based).
# ABOUTME: Validates day bucketing, fallback phases, LLM integration, and caching.

import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_kitchen.models import RepoGroup, Session
from agent_kitchen.timeline import (
    _sessions_by_day,
    apply_cached_timelines,
    batch_generate_timelines,
    fallback_timeline,
    generate_group_timeline,
)


def _make_session(
    session_id: str = "s1",
    started_at: datetime | None = None,
    last_active: datetime | None = None,
    summary: str = "Test session",
    status: str = "done",
    file_mtime: float = 1000.0,
) -> Session:
    if started_at is None:
        started_at = datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc)
    if last_active is None:
        last_active = started_at
    return Session(
        id=session_id,
        source="claude",
        cwd="/tmp/repo",
        repo_root="/tmp/repo",
        repo_name="repo",
        git_branch="main",
        started_at=started_at,
        last_active=last_active,
        slug=None,
        summary=summary,
        status=status,
        turn_count=5,
        file_path="/tmp/test.jsonl",
        file_mtime=file_mtime,
    )


def _make_repo_group(sessions: list[Session] | None = None) -> RepoGroup:
    if sessions is None:
        sessions = [_make_session()]
    return RepoGroup(
        repo_root="/tmp/repo",
        repo_name="repo",
        git_branch="main",
        git_dirty=False,
        unpushed_commits=0,
        sessions=sessions,
        last_active=sessions[0].last_active if sessions else datetime.min,
    )


class TestSessionsByDay:
    """Tests for bucketing sessions by day."""

    def test_single_session(self):
        s = _make_session(started_at=datetime(2026, 3, 15, 10, tzinfo=timezone.utc))
        days = _sessions_by_day([s])
        assert len(days) == 1
        assert days[0][0].day == 15

    def test_multiple_days_sorted_newest_first(self):
        s1 = _make_session(
            session_id="s1",
            started_at=datetime(2026, 3, 13, 10, tzinfo=timezone.utc),
        )
        s2 = _make_session(
            session_id="s2",
            started_at=datetime(2026, 3, 15, 10, tzinfo=timezone.utc),
        )
        s3 = _make_session(
            session_id="s3",
            started_at=datetime(2026, 3, 14, 10, tzinfo=timezone.utc),
        )
        days = _sessions_by_day([s1, s2, s3])
        assert len(days) == 3
        assert days[0][0].day == 15  # newest first
        assert days[1][0].day == 14
        assert days[2][0].day == 13

    def test_sessions_same_day_grouped(self):
        s1 = _make_session(
            session_id="s1",
            started_at=datetime(2026, 3, 15, 8, tzinfo=timezone.utc),
        )
        s2 = _make_session(
            session_id="s2",
            started_at=datetime(2026, 3, 15, 14, tzinfo=timezone.utc),
        )
        days = _sessions_by_day([s1, s2])
        assert len(days) == 1
        assert len(days[0][1]) == 2

    def test_empty_sessions(self):
        assert _sessions_by_day([]) == []


class TestFallbackTimeline:
    """Tests for the no-LLM fallback timeline."""

    def test_single_session_single_phase(self):
        s = _make_session(summary="Add user auth")
        phases = fallback_timeline([s])
        assert len(phases) == 1
        assert phases[0].description == "Add user auth"
        assert phases[0].session_count == 1

    def test_multiple_days_multiple_phases(self):
        s1 = _make_session(
            session_id="s1",
            started_at=datetime(2026, 3, 13, 10, tzinfo=timezone.utc),
            last_active=datetime(2026, 3, 13, 12, tzinfo=timezone.utc),
            summary="Set up project",
        )
        s2 = _make_session(
            session_id="s2",
            started_at=datetime(2026, 3, 14, 10, tzinfo=timezone.utc),
            last_active=datetime(2026, 3, 14, 12, tzinfo=timezone.utc),
            summary="Add API endpoints",
        )
        phases = fallback_timeline([s1, s2])
        assert len(phases) == 2
        # Newest first
        assert phases[0].description == "Add API endpoints"
        assert phases[1].description == "Set up project"

    def test_empty_sessions_returns_empty(self):
        assert fallback_timeline([]) == []

    def test_long_summary_truncated(self):
        s = _make_session(summary="A" * 120)
        phases = fallback_timeline([s])
        assert len(phases[0].description) <= 80

    def test_status_aggregation_all_done(self):
        s1 = _make_session(session_id="s1", status="done")
        s2 = _make_session(session_id="s2", status="likely done")
        phases = fallback_timeline([s1, s2])
        assert phases[0].status == "done"

    def test_status_aggregation_mixed(self):
        s1 = _make_session(
            session_id="s1",
            status="done",
            started_at=datetime(2026, 3, 15, 10, tzinfo=timezone.utc),
        )
        s2 = _make_session(
            session_id="s2",
            status="in progress",
            started_at=datetime(2026, 3, 15, 12, tzinfo=timezone.utc),
        )
        phases = fallback_timeline([s1, s2])
        assert phases[0].status == "mixed"


class TestGenerateRepoTimeline:
    """Tests for LLM-based timeline generation."""

    @pytest.mark.asyncio
    async def test_single_day_uses_fallback(self):
        """Repos with sessions on a single day should skip LLM."""
        group = _make_repo_group(
            [
                _make_session(session_id="s1", summary="Fix bug"),
                _make_session(session_id="s2", summary="Add test"),
            ]
        )
        phases = await generate_group_timeline(group)
        assert len(phases) == 1
        assert phases[0].session_count == 2

    @pytest.mark.asyncio
    async def test_multi_day_calls_llm(self):
        """Repos spanning multiple days should call the LLM."""
        sessions = [
            _make_session(
                session_id="s1",
                started_at=datetime(2026, 3, 13, 10, tzinfo=timezone.utc),
                summary="Set up project",
            ),
            _make_session(
                session_id="s2",
                started_at=datetime(2026, 3, 15, 10, tzinfo=timezone.utc),
                summary="Add features",
            ),
        ]
        group = _make_repo_group(sessions)

        mock_path = "agent_kitchen.timeline._call_timeline_llm"
        with patch(mock_path, new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "phases": [
                    {"period": "Mar 15", "description": "Added features", "status": "done"},
                    {"period": "Mar 13", "description": "Project setup", "status": "done"},
                ]
            }
            phases = await generate_group_timeline(group)

        assert len(phases) == 2
        assert phases[0].period == "Mar 15"
        assert phases[1].period == "Mar 13"

    @pytest.mark.asyncio
    async def test_llm_failure_returns_fallback(self):
        """LLM failure should fall back to day-based timeline."""
        sessions = [
            _make_session(
                session_id="s1",
                started_at=datetime(2026, 3, 13, 10, tzinfo=timezone.utc),
                summary="Day one work",
            ),
            _make_session(
                session_id="s2",
                started_at=datetime(2026, 3, 15, 10, tzinfo=timezone.utc),
                summary="Day three work",
            ),
        ]
        group = _make_repo_group(sessions)

        mock_path = "agent_kitchen.timeline._call_timeline_llm"
        with patch(mock_path, new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = RuntimeError("Connection failed")
            phases = await generate_group_timeline(group)

        assert len(phases) == 2  # fallback: one per day

    @pytest.mark.asyncio
    async def test_empty_group(self):
        group = _make_repo_group([])
        group.sessions = []
        phases = await generate_group_timeline(group)
        assert phases == []

    @pytest.mark.asyncio
    async def test_max_five_phases(self):
        """LLM returning more than 5 phases should be truncated."""
        sessions = [
            _make_session(
                session_id=f"s{i}",
                started_at=datetime(2026, 3, i + 1, 10, tzinfo=timezone.utc),
            )
            for i in range(7)
        ]
        group = _make_repo_group(sessions)

        mock_path = "agent_kitchen.timeline._call_timeline_llm"
        with patch(mock_path, new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "phases": [
                    {"period": f"Mar {i}", "description": f"Phase {i}", "status": "done"}
                    for i in range(8)
                ]
            }
            phases = await generate_group_timeline(group)

        assert len(phases) <= 5


class TestBatchGenerateTimelines:
    """Tests for batch timeline generation with caching."""

    @pytest.mark.asyncio
    async def test_uses_cache_when_fresh(self):
        """Should use cached timeline when not stale."""
        group = _make_repo_group()
        phases_data = [
            {"period": "Today", "description": "Cached work", "session_count": 1, "status": "done"}
        ]
        cache = MagicMock()
        cache.needs_refresh.return_value = False
        cache.get.return_value = {
            "type": "timeline",
            "summary": json.dumps(phases_data),
            "status": "timeline",
        }

        await batch_generate_timelines([group], cache)

        assert len(group.timeline) == 1
        assert group.timeline[0].description == "Cached work"

    @pytest.mark.asyncio
    async def test_generates_when_cache_stale(self):
        """Should call LLM when cache is stale."""
        sessions = [
            _make_session(
                session_id="s1",
                started_at=datetime(2026, 3, 13, 10, tzinfo=timezone.utc),
                summary="Old work",
            ),
            _make_session(
                session_id="s2",
                started_at=datetime(2026, 3, 15, 10, tzinfo=timezone.utc),
                summary="New work",
            ),
        ]
        group = _make_repo_group(sessions)
        cache = MagicMock()
        cache.needs_refresh.return_value = True

        mock_path = "agent_kitchen.timeline._call_timeline_llm"
        with patch(mock_path, new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "phases": [
                    {"period": "Mar 15", "description": "Recent work", "status": "done"},
                    {"period": "Mar 13", "description": "Earlier work", "status": "done"},
                ]
            }
            await batch_generate_timelines([group], cache)

        assert len(group.timeline) == 2
        cache.set_timeline.assert_called_once()
        cache.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_invalidation_by_mtime(self):
        """Cache key should use max mtime of all sessions."""
        s1 = _make_session(session_id="s1", file_mtime=100.0)
        s2 = _make_session(session_id="s2", file_mtime=200.0)
        group = _make_repo_group([s1, s2])
        cache = MagicMock()
        cache.needs_refresh.return_value = False
        phases_data = [
            {"period": "Today", "description": "Work", "session_count": 2, "status": "done"}
        ]
        cache.get.return_value = {
            "type": "timeline",
            "summary": json.dumps(phases_data),
            "status": "timeline",
        }

        await batch_generate_timelines([group], cache)

        # Should check with max mtime = 200.0
        cache.needs_refresh.assert_called_once_with("timeline:/tmp/repo", 200.0)


class TestApplyCachedTimelines:
    """Tests for applying cached or fallback timelines."""

    def test_applies_cached_timeline(self):
        group = _make_repo_group()
        phases_data = [
            {"period": "Today", "description": "Cached", "session_count": 1, "status": "done"}
        ]
        cache = MagicMock()
        cache.needs_refresh.return_value = False
        cache.get.return_value = {
            "type": "timeline",
            "summary": json.dumps(phases_data),
            "status": "timeline",
        }

        apply_cached_timelines([group], cache)

        assert len(group.timeline) == 1
        assert group.timeline[0].description == "Cached"

    def test_falls_back_when_no_cache(self):
        group = _make_repo_group([_make_session(summary="Fallback work")])
        cache = MagicMock()
        cache.needs_refresh.return_value = True

        apply_cached_timelines([group], cache)

        assert len(group.timeline) == 1
        assert group.timeline[0].description == "Fallback work"


class TestFormatDateRange:
    """Tests for _format_date_range edge cases."""

    def test_same_date_returns_single_period(self):
        from agent_kitchen.timeline import _format_date_range

        d = date(2026, 3, 15)
        result = _format_date_range(d, d)
        # Should delegate to _format_period for a single date
        assert result  # non-empty string

    def test_cross_month_range(self):
        from agent_kitchen.timeline import _format_date_range

        start = date(2026, 2, 27)
        end = date(2026, 3, 3)
        result = _format_date_range(start, end)
        assert "Feb" in result
        assert "Mar" in result

    def test_same_month_range(self):
        from agent_kitchen.timeline import _format_date_range

        start = date(2026, 3, 10)
        end = date(2026, 3, 15)
        result = _format_date_range(start, end)
        assert "Mar" in result
        assert "10" in result
        assert "15" in result

    def test_yesterday_today_range(self):
        from agent_kitchen.timeline import _format_date_range

        today = datetime.now().astimezone().date()
        yesterday = today - timedelta(days=1)
        result = _format_date_range(yesterday, today)
        assert result == "Yesterday-Today"

    def test_today_only(self):
        from agent_kitchen.timeline import _format_date_range

        today = datetime.now().astimezone().date()
        result = _format_date_range(today, today)
        assert result == "Today"

    def test_yesterday_only(self):
        from agent_kitchen.timeline import _format_date_range

        yesterday = datetime.now().astimezone().date() - timedelta(days=1)
        result = _format_date_range(yesterday, yesterday)
        assert result == "Yesterday"


class TestAggregateStatus:
    """Tests for _aggregate_status with various status combinations."""

    def test_all_done_statuses(self):
        from agent_kitchen.timeline import _aggregate_status

        sessions = [
            _make_session(session_id="s1", status="done"),
            _make_session(session_id="s2", status="likely done"),
        ]
        assert _aggregate_status(sessions) == "done"

    def test_all_active_statuses(self):
        from agent_kitchen.timeline import _aggregate_status

        sessions = [
            _make_session(session_id="s1", status="in progress"),
            _make_session(session_id="s2", status="likely in progress"),
        ]
        assert _aggregate_status(sessions) == "in progress"

    def test_active_and_done_returns_mixed(self):
        from agent_kitchen.timeline import _aggregate_status

        sessions = [
            _make_session(session_id="s1", status="done"),
            _make_session(session_id="s2", status="in progress"),
        ]
        assert _aggregate_status(sessions) == "mixed"

    def test_waiting_for_input_is_active(self):
        from agent_kitchen.timeline import _aggregate_status

        sessions = [
            _make_session(session_id="s1", status="waiting for input"),
        ]
        assert _aggregate_status(sessions) == "in progress"

    def test_waiting_and_done_returns_mixed(self):
        from agent_kitchen.timeline import _aggregate_status

        sessions = [
            _make_session(session_id="s1", status="waiting for input"),
            _make_session(session_id="s2", status="done"),
        ]
        assert _aggregate_status(sessions) == "mixed"

    def test_single_done_session(self):
        from agent_kitchen.timeline import _aggregate_status

        sessions = [_make_session(status="done")]
        assert _aggregate_status(sessions) == "done"

    def test_unknown_status_returns_mixed(self):
        from agent_kitchen.timeline import _aggregate_status

        sessions = [_make_session(status="something_unknown")]
        assert _aggregate_status(sessions) == "mixed"


class TestFallbackTimelineEdgeCases:
    """Additional edge case tests for fallback_timeline."""

    def test_session_with_no_summary_uses_default(self):
        s = _make_session(summary="")
        phases = fallback_timeline([s])
        assert len(phases) == 1
        assert phases[0].description == "Work session"

    def test_multiple_sessions_same_day(self):
        s1 = _make_session(
            session_id="s1",
            started_at=datetime(2026, 3, 15, 8, tzinfo=timezone.utc),
            last_active=datetime(2026, 3, 15, 9, tzinfo=timezone.utc),
            summary="Morning work",
        )
        s2 = _make_session(
            session_id="s2",
            started_at=datetime(2026, 3, 15, 14, tzinfo=timezone.utc),
            last_active=datetime(2026, 3, 15, 16, tzinfo=timezone.utc),
            summary="Afternoon work",
        )
        phases = fallback_timeline([s1, s2])
        assert len(phases) == 1
        assert phases[0].session_count == 2
        # Most recent session's summary should be used
        assert phases[0].description == "Afternoon work"
