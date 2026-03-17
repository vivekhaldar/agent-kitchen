# ABOUTME: Tests for error handling across all modules.
# ABOUTME: Covers malformed data, missing dirs, subprocess failures, and graceful degradation.

import json
import subprocess
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_kitchen.cache import SummaryCache
from agent_kitchen.git_status import get_git_status
from agent_kitchen.models import Session
from agent_kitchen.scanner import scan_claude_sessions, scan_codex_sessions
from agent_kitchen.summarizer import SummarizeResult


def _make_session(**overrides) -> Session:
    defaults = dict(
        id="test-session-001",
        source="claude",
        cwd="/Users/test/repos/myproject",
        repo_root="/Users/test/repos/myproject",
        repo_name="myproject",
        git_branch="main",
        started_at=datetime(2026, 3, 10, 10, 0, 0, tzinfo=timezone.utc),
        last_active=datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc),
        slug="lively-herding-sonnet",
        summary="Implement retry logic",
        status="done",
        turn_count=10,
        file_path="/Users/test/.claude/projects/-Users-test-repos-myproject/test.jsonl",
        file_mtime=1710072000.0,
    )
    defaults.update(overrides)
    return Session(**defaults)


# --- git_status.py subprocess failure handling ---


class TestGitStatusSubprocessFailures:
    """Tests that git_status handles subprocess failures gracefully."""

    def test_timeout_returns_none(self, tmp_path):
        """If the status command times out, return None."""
        # Create a .git dir so the os.path.exists check passes
        (tmp_path / ".git").mkdir()
        with patch("agent_kitchen.git_status.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=5)
            status = get_git_status(str(tmp_path))
            assert status is None

    def test_empty_output_returns_safe_defaults(self, tmp_path):
        """If status --porcelain -b returns empty output, defaults are safe."""
        (tmp_path / ".git").mkdir()
        with patch("agent_kitchen.git_status.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            status = get_git_status(str(tmp_path))
            assert status is not None
            assert status.dirty is False
            assert status.untracked == 0

    def test_no_upstream_returns_zero_unpushed(self, tmp_path):
        """If the branch header has no upstream info, unpushed is 0."""
        (tmp_path / ".git").mkdir()
        with patch("agent_kitchen.git_status.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="## main\n")
            status = get_git_status(str(tmp_path))
            assert status is not None
            assert status.unpushed == 0

    def test_oserror_returns_none(self, tmp_path):
        """If the single git status call raises OSError, return None."""
        (tmp_path / ".git").mkdir()
        with patch("agent_kitchen.git_status.subprocess.run") as mock_run:
            mock_run.side_effect = OSError("git not found")
            status = get_git_status(str(tmp_path))
            assert status is None

    def test_file_not_found_returns_none(self, tmp_path):
        """If git status fails with FileNotFoundError, return None."""
        (tmp_path / ".git").mkdir()
        with patch("agent_kitchen.git_status.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("git not found")
            status = get_git_status(str(tmp_path))
            assert status is None


# --- cache.py error handling ---


class TestCacheErrorHandling:
    def test_load_handles_oserror(self, tmp_path):
        """Cache load should handle OSError (e.g., permission denied)."""
        cache_path = tmp_path / "summaries.json"
        cache_path.write_text('{"version": 1, "entries": {"a": {"summary": "test"}}}')
        cache_path.chmod(0o000)

        try:
            cache = SummaryCache(cache_path)
            # Should not crash; entries should be empty or loaded
            assert isinstance(cache.entries, dict)
        finally:
            cache_path.chmod(0o644)

    def test_load_handles_truncated_json(self, tmp_path):
        """Cache load should handle truncated/partial JSON gracefully."""
        cache_path = tmp_path / "summaries.json"
        cache_path.write_text('{"version": 1, "entries": {"a":')

        cache = SummaryCache(cache_path)
        assert cache.entries == {}

    def test_save_handles_readonly_directory(self, tmp_path):
        """Cache save should raise on readonly directory (expected behavior)."""
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        cache_path = readonly_dir / "summaries.json"

        cache = SummaryCache(cache_path)
        cache.set("test-id", "summary", "done", 123.0)

        readonly_dir.chmod(0o555)
        try:
            with pytest.raises(OSError):
                cache.save()
        finally:
            readonly_dir.chmod(0o755)


# --- scanner.py file mtime edge cases ---


class TestScannerMtimeEdgeCases:
    def test_claude_scanner_handles_file_deleted_during_scan(self, tmp_path):
        """Scanner should handle files disappearing between discovery and mtime check."""
        project_dir = tmp_path / "-Users-test"
        project_dir.mkdir()
        # Create a valid JSONL file
        session_file = project_dir / "test-uuid.jsonl"
        record = {
            "type": "user",
            "timestamp": "2026-03-01T10:00:00Z",
            "sessionId": "test-uuid",
            "cwd": "/Users/test",
            "message": {"content": [{"type": "text", "text": "hello"}]},
        }
        session_file.write_text(json.dumps(record) + "\n")

        # Mock getmtime to raise OSError (simulating deleted file)
        original_getmtime = __import__("os").path.getmtime
        call_count = 0

        def failing_getmtime(path):
            nonlocal call_count
            if str(path).endswith("test-uuid.jsonl"):
                call_count += 1
                if call_count == 1:
                    raise OSError("No such file")
            return original_getmtime(path)

        with patch("agent_kitchen.scanner.os.path.getmtime", side_effect=failing_getmtime):
            since = datetime(2026, 1, 1, tzinfo=timezone.utc)
            sessions = scan_claude_sessions(since, projects_dir=tmp_path)
            # Should handle gracefully — either skip or return empty
            assert isinstance(sessions, list)

    def test_codex_scanner_handles_file_deleted_during_scan(self, tmp_path):
        """Codex scanner should handle files disappearing between discovery and mtime check."""
        day_dir = tmp_path / "2026" / "03" / "01"
        day_dir.mkdir(parents=True)
        session_file = day_dir / "rollout-2026-03-01T10-00-00-TESTULID.jsonl"
        record = {
            "type": "session_meta",
            "timestamp": "2026-03-01T10:00:00Z",
            "payload": {"id": "TESTULID", "cwd": "/Users/test"},
        }
        session_file.write_text(json.dumps(record) + "\n")

        original_getmtime = __import__("os").path.getmtime

        def failing_getmtime(path):
            if str(path).endswith("TESTULID.jsonl"):
                raise OSError("No such file")
            return original_getmtime(path)

        with patch("agent_kitchen.scanner.os.path.getmtime", side_effect=failing_getmtime):
            since = datetime(2026, 1, 1, tzinfo=timezone.utc)
            sessions = scan_codex_sessions(since, sessions_dir=tmp_path)
            assert isinstance(sessions, list)


# --- scanner.py malformed JSONL warning ---


class TestScannerMalformedJsonlWarning:
    def test_claude_scanner_logs_warning_for_bad_first_line(self, tmp_path, caplog):
        """Scanner should log a warning when first JSONL line is unparseable."""
        import logging

        project_dir = tmp_path / "-Users-test"
        project_dir.mkdir()
        bad_file = project_dir / "bad-uuid.jsonl"
        bad_file.write_text("NOT JSON AT ALL\n")

        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with caplog.at_level(logging.WARNING, logger="agent_kitchen.scanner"):
            sessions = scan_claude_sessions(since, projects_dir=tmp_path)

        assert sessions == []
        assert "Failed to parse first line" in caplog.text

    def test_claude_scanner_skips_malformed_middle_lines(self, tmp_path):
        """Scanner should skip malformed lines in the middle of a file and still parse."""
        project_dir = tmp_path / "-Users-test"
        project_dir.mkdir()
        session_file = project_dir / "good-uuid.jsonl"
        lines = [
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2026-03-01T10:00:00Z",
                    "sessionId": "good-uuid",
                    "cwd": "/Users/test",
                    "message": {"content": [{"type": "text", "text": "hello"}]},
                }
            ),
            "THIS IS NOT JSON",
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-03-01T10:01:00Z",
                    "sessionId": "good-uuid",
                    "message": {"content": [{"type": "text", "text": "hi there"}]},
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2026-03-01T10:02:00Z",
                    "sessionId": "good-uuid",
                    "cwd": "/Users/test",
                    "message": {"content": [{"type": "text", "text": "thanks"}]},
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-03-01T10:03:00Z",
                    "sessionId": "good-uuid",
                    "message": {"content": [{"type": "text", "text": "welcome"}]},
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2026-03-01T10:04:00Z",
                    "sessionId": "good-uuid",
                    "cwd": "/Users/test",
                    "message": {"content": [{"type": "text", "text": "one more thing"}]},
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-03-01T10:05:00Z",
                    "sessionId": "good-uuid",
                    "message": {"content": [{"type": "text", "text": "sure"}]},
                }
            ),
        ]
        session_file.write_text("\n".join(lines) + "\n")

        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        sessions = scan_claude_sessions(since, projects_dir=tmp_path)
        assert len(sessions) == 1
        assert sessions[0].turn_count == 6  # All valid records counted


# --- server.py pipeline error handling ---


class TestPipelineErrorHandling:
    @pytest.mark.asyncio
    async def test_pipeline_handles_scanner_exception(self):
        """Pipeline should handle scanner exceptions gracefully."""
        from agent_kitchen.server import run_scan_pipeline

        with (
            patch(
                "agent_kitchen.server.scan_claude_sessions",
                side_effect=RuntimeError("Filesystem error"),
            ),
            patch("agent_kitchen.server.scan_codex_sessions", return_value=[]),
            patch("agent_kitchen.server.SummaryCache"),
            patch(
                "agent_kitchen.server.batch_summarize",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("agent_kitchen.server.group_sessions", return_value=([], [])),
        ):
            result = await run_scan_pipeline()
            assert result["repo_groups"] == []
            assert result["non_repo_groups"] == []

    @pytest.mark.asyncio
    async def test_pipeline_handles_codex_scanner_exception(self):
        """Pipeline should handle Codex scanner exceptions gracefully."""
        from agent_kitchen.server import run_scan_pipeline

        with (
            patch("agent_kitchen.server.scan_claude_sessions", return_value=[]),
            patch(
                "agent_kitchen.server.scan_codex_sessions",
                side_effect=PermissionError("Access denied"),
            ),
            patch("agent_kitchen.server.SummaryCache"),
            patch(
                "agent_kitchen.server.batch_summarize",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("agent_kitchen.server.group_sessions", return_value=([], [])),
        ):
            result = await run_scan_pipeline()
            assert result["repo_groups"] == []
            assert result["non_repo_groups"] == []

    @pytest.mark.asyncio
    async def test_pipeline_handles_grouping_exception(self):
        """Pipeline should handle grouping exceptions gracefully."""
        from agent_kitchen.server import run_scan_pipeline

        session = _make_session(summary="", status="")

        with (
            patch("agent_kitchen.server.scan_claude_sessions", return_value=[session]),
            patch("agent_kitchen.server.scan_codex_sessions", return_value=[]),
            patch("agent_kitchen.server.SummaryCache"),
            patch(
                "agent_kitchen.server.batch_summarize",
                new_callable=AsyncMock,
                return_value=[SummarizeResult(summary="test", status="done")],
            ),
            patch(
                "agent_kitchen.server.get_repo_root",
                return_value="/Users/test/repos/myproject",
            ),
            patch(
                "agent_kitchen.server.group_sessions",
                side_effect=RuntimeError("Grouping failed"),
            ),
        ):
            result = await run_scan_pipeline()
            assert result["repo_groups"] == []
            assert result["non_repo_groups"] == []

    @pytest.mark.asyncio
    async def test_pipeline_continues_when_one_scanner_fails(self):
        """If Claude scanner fails but Codex succeeds, Codex sessions still appear."""
        from agent_kitchen.server import run_scan_pipeline

        codex_session = _make_session(source="codex", id="codex-1")

        with (
            patch(
                "agent_kitchen.server.scan_claude_sessions",
                side_effect=RuntimeError("Claude scan failed"),
            ),
            patch("agent_kitchen.server.scan_codex_sessions", return_value=[codex_session]),
            patch("agent_kitchen.server.SummaryCache"),
            patch(
                "agent_kitchen.server.batch_summarize",
                new_callable=AsyncMock,
                return_value=[SummarizeResult(summary="test", status="done")],
            ),
            patch("agent_kitchen.server.get_repo_root", return_value=None),
            patch("agent_kitchen.server.group_sessions") as mock_group,
        ):
            mock_group.return_value = ([], [])
            await run_scan_pipeline()
            # Grouping should have been called with just the codex session
            called_sessions = mock_group.call_args[0][0]
            assert len(called_sessions) == 1
            assert called_sessions[0].source == "codex"
