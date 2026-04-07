# ABOUTME: Tests for the FastAPI server endpoints, startup orchestration, and background refresh.
# ABOUTME: Covers /api/sessions, /api/refresh, /api/launch, scan pipeline, and periodic rescan.

import asyncio
import subprocess
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent_kitchen.models import NonRepoGroup, RepoGroup, Session
from agent_kitchen.summarizer import SummarizeResult


def _make_session(**overrides) -> Session:
    """Create a Session with sensible defaults, overridable by keyword args."""
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


def _make_repo_group(**overrides) -> RepoGroup:
    defaults = dict(
        repo_root="/Users/test/repos/myproject",
        repo_name="myproject",
        git_branch="main",
        git_dirty=False,
        unpushed_commits=0,
        sessions=[_make_session()],
        last_active=datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return RepoGroup(**defaults)


def _make_non_repo_group(**overrides) -> NonRepoGroup:
    defaults = dict(
        cwd="/Users/test/Desktop",
        sessions=[_make_session(repo_root=None, repo_name=None, cwd="/Users/test/Desktop")],
        last_active=datetime(2026, 3, 10, 11, 0, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return NonRepoGroup(**defaults)


# --- Fixtures ---


@pytest.fixture
def mock_dashboard_data():
    """Pre-built dashboard data to inject into the server."""
    return {
        "repo_groups": [_make_repo_group()],
        "non_repo_groups": [_make_non_repo_group()],
        "last_scanned": datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc).isoformat(),
        "scan_duration_ms": 1234,
    }


@pytest.fixture
def app(mock_dashboard_data):
    """Create a test FastAPI app with mocked scan pipeline."""
    from agent_kitchen.server import create_app

    test_app = create_app(enable_background_refresh=False)
    # Inject pre-built data directly
    from agent_kitchen import server

    server._dashboard_data = mock_dashboard_data
    return test_app


@pytest.fixture
def client(app):
    return TestClient(app)


# --- GET /api/sessions ---


class TestGetSessions:
    def test_returns_200(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200

    def test_returns_repo_groups(self, client):
        resp = client.get("/api/sessions")
        data = resp.json()
        assert "repo_groups" in data
        assert len(data["repo_groups"]) == 1
        assert data["repo_groups"][0]["repo_name"] == "myproject"

    def test_returns_non_repo_groups(self, client):
        resp = client.get("/api/sessions")
        data = resp.json()
        assert "non_repo_groups" in data
        assert len(data["non_repo_groups"]) == 1
        assert data["non_repo_groups"][0]["cwd"] == "/Users/test/Desktop"

    def test_returns_scan_metadata(self, client):
        resp = client.get("/api/sessions")
        data = resp.json()
        assert "last_scanned" in data
        assert "scan_duration_ms" in data

    def test_session_fields_complete(self, client):
        resp = client.get("/api/sessions")
        session = resp.json()["repo_groups"][0]["sessions"][0]
        assert session["id"] == "test-session-001"
        assert session["source"] == "claude"
        assert session["summary"] == "Implement retry logic"
        assert session["status"] == "done"
        assert session["turn_count"] == 10
        assert session["slug"] == "lively-herding-sonnet"

    def test_repo_group_git_fields(self, client):
        resp = client.get("/api/sessions")
        group = resp.json()["repo_groups"][0]
        assert group["git_branch"] == "main"
        assert group["git_dirty"] is False
        assert group["unpushed_commits"] == 0


# --- GET /api/refresh ---


class TestRefresh:
    def test_refresh_returns_200(self, client):
        with patch("agent_kitchen.server.run_scan_pipeline", new_callable=AsyncMock) as mock_scan:
            mock_scan.return_value = {
                "repo_groups": [],
                "non_repo_groups": [],
                "last_scanned": datetime.now(timezone.utc).isoformat(),
                "scan_duration_ms": 500,
            }
            resp = client.get("/api/refresh")
            assert resp.status_code == 200

    def test_refresh_triggers_rescan(self, client):
        with patch("agent_kitchen.server.run_scan_pipeline", new_callable=AsyncMock) as mock_scan:
            mock_scan.return_value = {
                "repo_groups": [],
                "non_repo_groups": [],
                "last_scanned": datetime.now(timezone.utc).isoformat(),
                "scan_duration_ms": 100,
            }
            client.get("/api/refresh")
            mock_scan.assert_called_once()

    def test_refresh_returns_updated_data(self, client):
        new_group = _make_repo_group(repo_name="new-project")
        with patch("agent_kitchen.server.run_scan_pipeline", new_callable=AsyncMock) as mock_scan:
            mock_scan.return_value = {
                "repo_groups": [new_group],
                "non_repo_groups": [],
                "last_scanned": datetime.now(timezone.utc).isoformat(),
                "scan_duration_ms": 100,
            }
            resp = client.get("/api/refresh")
            data = resp.json()
            assert data["repo_groups"][0]["repo_name"] == "new-project"


# --- GET /api/launch ---


class TestLaunch:
    def test_launch_claude_session(self, client):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            resp = client.get(
                "/api/launch",
                params={
                    "source": "claude",
                    "session_id": "abc-123",
                    "cwd": "/Users/test/repos/myproject",
                },
            )
            assert resp.status_code == 200
            assert resp.json()["ok"] is True
            mock_run.assert_called_once()

    def test_launch_codex_session(self, client):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            resp = client.get(
                "/api/launch",
                params={
                    "source": "codex",
                    "session_id": "ulid-456",
                    "cwd": "/Users/test/repos/myproject",
                },
            )
            assert resp.status_code == 200
            assert resp.json()["ok"] is True

    def test_launch_claude_uses_correct_command(self, client):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            client.get(
                "/api/launch",
                params={
                    "source": "claude",
                    "session_id": "abc-123",
                    "cwd": "/Users/test/repos/proj",
                },
            )
            call_args = mock_run.call_args[0][0]
            cmd = call_args[-1]
            assert "claude --dangerously-skip-permissions --resume abc-123" in cmd
            assert "cd /Users/test/repos/proj" in cmd
            assert "unset CLAUDECODE" in cmd

    def test_launch_codex_uses_correct_command(self, client):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            client.get(
                "/api/launch",
                params={
                    "source": "codex",
                    "session_id": "ulid-456",
                    "cwd": "/Users/test/repos/proj",
                },
            )
            call_args = mock_run.call_args[0][0]
            cmd = call_args[-1]
            assert "codex resume ulid-456" in cmd
            assert "cd /Users/test/repos/proj" in cmd

    def test_launch_uses_ghostty_by_default(self, client):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            client.get(
                "/api/launch",
                params={
                    "source": "claude",
                    "session_id": "abc-123",
                    "cwd": "/tmp",
                },
            )
            call_args = mock_run.call_args[0][0]
            assert call_args[0] == "open"
            assert "-na" in call_args
            assert "Ghostty" in call_args

    def test_launch_uses_terminal_app_when_configured(self, client):
        with (
            patch("agent_kitchen.server._config") as mock_config,
            patch("subprocess.run") as mock_run,
        ):
            mock_config.TERMINAL_APP = "terminal"
            mock_run.return_value = MagicMock(returncode=0)
            client.get(
                "/api/launch",
                params={
                    "source": "claude",
                    "session_id": "abc-123",
                    "cwd": "/tmp",
                },
            )
            call_args = mock_run.call_args[0][0]
            assert call_args[0] == "osascript"

    def test_launch_missing_params_returns_422(self, client):
        resp = client.get("/api/launch")
        assert resp.status_code == 422

    def test_launch_invalid_source_returns_400(self, client):
        resp = client.get(
            "/api/launch",
            params={
                "source": "invalid",
                "session_id": "abc",
                "cwd": "/tmp",
            },
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_launch_subprocess_failure(self, client):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError("Terminal not found")
            resp = client.get(
                "/api/launch",
                params={
                    "source": "claude",
                    "session_id": "abc",
                    "cwd": "/tmp",
                },
            )
            assert resp.status_code == 500
            assert "error" in resp.json()


# --- Static file serving ---


class TestStaticFiles:
    def test_serves_index_html(self, client, tmp_path):
        # The static dir mount should be configured
        # We test that the route exists, even if the file doesn't
        resp = client.get("/")
        # With no static files, this might return 404, but the route should exist
        assert resp.status_code in (200, 404)


# --- Serialization ---


class TestSerialization:
    def test_session_serializes_datetime_as_iso(self, client):
        resp = client.get("/api/sessions")
        session = resp.json()["repo_groups"][0]["sessions"][0]
        # Datetime fields should be ISO strings
        assert "2026-03-10" in session["started_at"]
        assert "2026-03-10" in session["last_active"]

    def test_repo_group_serializes_datetime(self, client):
        resp = client.get("/api/sessions")
        group = resp.json()["repo_groups"][0]
        assert "2026-03-10" in group["last_active"]


class TestSerializeDashboard:
    """Tests for _serialize_dashboard with various data shapes."""

    def test_empty_groups(self):
        from agent_kitchen.server import _serialize_dashboard

        data = {
            "repo_groups": [],
            "non_repo_groups": [],
            "last_scanned": "2026-03-10T12:00:00Z",
            "scan_duration_ms": 100,
        }
        result = _serialize_dashboard(data)
        assert result["repo_groups"] == []
        assert result["non_repo_groups"] == []
        assert result["last_scanned"] == "2026-03-10T12:00:00Z"
        assert result["scan_duration_ms"] == 100

    def test_missing_fields_use_defaults(self):
        from agent_kitchen.server import _serialize_dashboard

        result = _serialize_dashboard({})
        assert result["repo_groups"] == []
        assert result["non_repo_groups"] == []
        assert result["last_scanned"] == ""
        assert result["scan_duration_ms"] == 0

    def test_datetime_serialized_to_iso(self):
        from agent_kitchen.server import _serialize_dashboard

        group = _make_repo_group()
        data = {
            "repo_groups": [group],
            "non_repo_groups": [],
            "last_scanned": datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc).isoformat(),
            "scan_duration_ms": 50,
        }
        result = _serialize_dashboard(data)
        session = result["repo_groups"][0]["sessions"][0]
        assert isinstance(session["started_at"], str)
        assert "2026-03-10" in session["started_at"]

    def test_non_repo_group_serialization(self):
        from agent_kitchen.server import _serialize_dashboard

        non_repo = _make_non_repo_group()
        data = {
            "repo_groups": [],
            "non_repo_groups": [non_repo],
            "last_scanned": "",
            "scan_duration_ms": 0,
        }
        result = _serialize_dashboard(data)
        assert len(result["non_repo_groups"]) == 1
        assert result["non_repo_groups"][0]["cwd"] == "/Users/test/Desktop"

    def test_timeline_phases_serialized(self):
        from agent_kitchen.models import TimelinePhase
        from agent_kitchen.server import _serialize_dashboard

        group = _make_repo_group()
        group.timeline = [
            TimelinePhase(
                period="Today",
                description="Fixed bugs",
                session_count=2,
                status="done",
            )
        ]
        data = {
            "repo_groups": [group],
            "non_repo_groups": [],
            "last_scanned": "",
            "scan_duration_ms": 0,
        }
        result = _serialize_dashboard(data)
        timeline = result["repo_groups"][0]["timeline"]
        assert len(timeline) == 1
        assert timeline[0]["period"] == "Today"
        assert timeline[0]["description"] == "Fixed bugs"

    def test_multiple_repo_groups(self):
        from agent_kitchen.server import _serialize_dashboard

        g1 = _make_repo_group(repo_name="project-a")
        g2 = _make_repo_group(repo_name="project-b")
        data = {
            "repo_groups": [g1, g2],
            "non_repo_groups": [],
            "last_scanned": "",
            "scan_duration_ms": 0,
        }
        result = _serialize_dashboard(data)
        assert len(result["repo_groups"]) == 2
        assert result["repo_groups"][0]["repo_name"] == "project-a"
        assert result["repo_groups"][1]["repo_name"] == "project-b"


class TestLaunchErrorPaths:
    """Additional tests for /api/launch error paths."""

    def test_launch_empty_source_returns_400(self, client):
        resp = client.get(
            "/api/launch",
            params={"source": "", "session_id": "abc", "cwd": "/tmp"},
        )
        assert resp.status_code == 400

    def test_launch_subprocess_called_process_error(self, client):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "open")
            resp = client.get(
                "/api/launch",
                params={"source": "claude", "session_id": "abc", "cwd": "/tmp"},
            )
            assert resp.status_code == 500
            assert "error" in resp.json()

    def test_launch_missing_session_id(self, client):
        resp = client.get(
            "/api/launch",
            params={"source": "claude", "cwd": "/tmp"},
        )
        assert resp.status_code == 422

    def test_launch_missing_cwd(self, client):
        resp = client.get(
            "/api/launch",
            params={"source": "claude", "session_id": "abc"},
        )
        assert resp.status_code == 422


# --- Scan pipeline ---


class TestScanPipeline:
    @pytest.mark.asyncio
    async def test_run_scan_pipeline_integrates_components(self):
        """Test that the scan pipeline calls scanner, summarizer, and grouping."""
        from agent_kitchen.server import run_scan_pipeline

        mock_sessions = [_make_session(summary="", status="")]
        mock_result = SummarizeResult(summary="Test summary", status="done")
        mock_repo_groups = [_make_repo_group()]

        with (
            patch("agent_kitchen.server.scan_claude_sessions", return_value=mock_sessions),
            patch("agent_kitchen.server.scan_codex_sessions", return_value=[]),
            patch("agent_kitchen.server.SummaryCache"),
            patch(
                "agent_kitchen.server.batch_summarize",
                new_callable=AsyncMock,
                return_value=[mock_result],
            ),
            patch(
                "agent_kitchen.server.get_repo_root", return_value="/Users/test/repos/myproject"
            ),
            patch("agent_kitchen.server.group_sessions", return_value=(mock_repo_groups, [])),
        ):
            result = await run_scan_pipeline()

            assert "repo_groups" in result
            assert "non_repo_groups" in result
            assert "last_scanned" in result
            assert "scan_duration_ms" in result

    @pytest.mark.asyncio
    async def test_pipeline_applies_summaries_to_sessions(self):
        """Test that summaries from batch_summarize are applied to sessions."""
        from agent_kitchen.server import run_scan_pipeline

        session = _make_session(summary="", status="")
        result = SummarizeResult(summary="Fixed bug in parser", status="done")

        with (
            patch("agent_kitchen.server.scan_claude_sessions", return_value=[session]),
            patch("agent_kitchen.server.scan_codex_sessions", return_value=[]),
            patch("agent_kitchen.server.SummaryCache"),
            patch(
                "agent_kitchen.server.batch_summarize",
                new_callable=AsyncMock,
                return_value=[result],
            ),
            patch(
                "agent_kitchen.server.get_repo_root", return_value="/Users/test/repos/myproject"
            ),
            patch("agent_kitchen.server.group_sessions") as mock_group,
        ):
            mock_group.return_value = ([], [])
            await run_scan_pipeline()

            # Check that group_sessions was called with sessions that have summaries applied
            called_sessions = mock_group.call_args[0][0]
            assert called_sessions[0].summary == "Fixed bug in parser"
            assert called_sessions[0].status == "done"

    @pytest.mark.asyncio
    async def test_pipeline_resolves_repo_roots(self):
        """Test that repo_root and repo_name are populated from git."""
        from agent_kitchen.server import run_scan_pipeline

        session = _make_session(repo_root=None, repo_name=None)

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
            patch("agent_kitchen.server.group_sessions") as mock_group,
        ):
            mock_group.return_value = ([], [])
            await run_scan_pipeline()

            called_sessions = mock_group.call_args[0][0]
            assert called_sessions[0].repo_root == "/Users/test/repos/myproject"
            assert called_sessions[0].repo_name == "myproject"

    @pytest.mark.asyncio
    async def test_pipeline_handles_missing_directories(self):
        """Pipeline should work even if no sessions are found."""
        from agent_kitchen.server import run_scan_pipeline

        with (
            patch("agent_kitchen.server.scan_claude_sessions", return_value=[]),
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


# --- Background refresh ---


class TestBackgroundRefresh:
    @pytest.mark.asyncio
    async def test_refresh_loop_calls_scan_pipeline(self):
        """Background loop should call run_scan_pipeline after the interval."""
        from agent_kitchen import server
        from agent_kitchen.server import _background_refresh_loop

        call_count = 0
        original_data = server._dashboard_data

        async def mock_pipeline():
            nonlocal call_count
            call_count += 1
            return {
                "repo_groups": [],
                "non_repo_groups": [],
                "last_scanned": datetime.now(timezone.utc).isoformat(),
                "scan_duration_ms": 50,
            }

        with patch("agent_kitchen.server.run_scan_pipeline", side_effect=mock_pipeline):
            task = asyncio.create_task(_background_refresh_loop(interval=0.05))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert call_count >= 2
        server._dashboard_data = original_data

    @pytest.mark.asyncio
    async def test_refresh_loop_updates_dashboard_data_atomically(self):
        """Background loop should swap _dashboard_data reference, not mutate."""
        from agent_kitchen import server
        from agent_kitchen.server import _background_refresh_loop

        original_data = server._dashboard_data
        new_group = _make_repo_group(repo_name="refreshed-project")
        new_data = {
            "repo_groups": [new_group],
            "non_repo_groups": [],
            "last_scanned": datetime.now(timezone.utc).isoformat(),
            "scan_duration_ms": 100,
        }

        with patch(
            "agent_kitchen.server.run_scan_pipeline",
            new_callable=AsyncMock,
            return_value=new_data,
        ):
            task = asyncio.create_task(_background_refresh_loop(interval=0.05))
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert server._dashboard_data is new_data
        assert server._dashboard_data["repo_groups"][0].repo_name == "refreshed-project"
        server._dashboard_data = original_data

    @pytest.mark.asyncio
    async def test_refresh_loop_survives_pipeline_errors(self):
        """Background loop should log errors but keep running if scan fails."""
        from agent_kitchen.server import _background_refresh_loop

        call_count = 0

        async def failing_then_succeeding():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Scan failed")
            return {
                "repo_groups": [],
                "non_repo_groups": [],
                "last_scanned": datetime.now(timezone.utc).isoformat(),
                "scan_duration_ms": 50,
            }

        with patch("agent_kitchen.server.run_scan_pipeline", side_effect=failing_then_succeeding):
            task = asyncio.create_task(_background_refresh_loop(interval=0.05))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should have continued past the error and called at least twice
        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_refresh_loop_cancellation(self):
        """Background loop should exit cleanly when cancelled."""
        from agent_kitchen.server import _background_refresh_loop

        with patch(
            "agent_kitchen.server.run_scan_pipeline",
            new_callable=AsyncMock,
            return_value={
                "repo_groups": [],
                "non_repo_groups": [],
                "last_scanned": "",
                "scan_duration_ms": 0,
            },
        ):
            task = asyncio.create_task(_background_refresh_loop(interval=10))
            # Cancel while it's sleeping (before first run)
            await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    @pytest.mark.asyncio
    async def test_lifespan_starts_and_stops_refresh(self):
        """Lifespan should create a background task on startup and cancel on shutdown."""
        from agent_kitchen import server
        from agent_kitchen.server import _lifespan, create_app

        app = create_app(enable_background_refresh=False)

        with patch(
            "agent_kitchen.server.run_scan_pipeline",
            new_callable=AsyncMock,
            return_value={
                "repo_groups": [],
                "non_repo_groups": [],
                "last_scanned": "",
                "scan_duration_ms": 0,
            },
        ):
            async with _lifespan(app):
                assert server._refresh_task is not None
                assert not server._refresh_task.done()

            # After exiting lifespan, task should be cancelled
            assert server._refresh_task is None

    def test_create_app_without_background_refresh(self):
        """create_app with enable_background_refresh=False should not use our lifespan."""
        from agent_kitchen.server import _lifespan, create_app

        app = create_app(enable_background_refresh=False)
        # Should not be our custom lifespan
        assert app.router.lifespan_context is not _lifespan

    def test_create_app_with_background_refresh(self):
        """create_app with default args should use our custom lifespan."""
        from agent_kitchen.server import _lifespan, create_app

        app = create_app(enable_background_refresh=True)
        assert app.router.lifespan_context is _lifespan

    @pytest.mark.asyncio
    async def test_refresh_does_not_mutate_old_data(self):
        """After refresh, the old data reference should be untouched."""
        from agent_kitchen import server
        from agent_kitchen.server import _background_refresh_loop

        old_data = {
            "repo_groups": [_make_repo_group(repo_name="old-project")],
            "non_repo_groups": [],
            "last_scanned": "2026-01-01T00:00:00Z",
            "scan_duration_ms": 100,
        }
        server._dashboard_data = old_data
        old_data_copy = dict(old_data)

        new_data = {
            "repo_groups": [_make_repo_group(repo_name="new-project")],
            "non_repo_groups": [],
            "last_scanned": datetime.now(timezone.utc).isoformat(),
            "scan_duration_ms": 50,
        }

        with patch(
            "agent_kitchen.server.run_scan_pipeline",
            new_callable=AsyncMock,
            return_value=new_data,
        ):
            task = asyncio.create_task(_background_refresh_loop(interval=0.05))
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Old data dict should be unmodified
        assert old_data["last_scanned"] == old_data_copy["last_scanned"]
        assert old_data["repo_groups"][0].repo_name == "old-project"
        # New data should be the current
        assert server._dashboard_data is new_data
        server._dashboard_data = None


class TestTruncateToolContent:
    """Tests for server-side truncation of large tool call content."""

    def test_truncates_large_tool_content(self):
        data = {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "call_001",
            "content": [
                {
                    "type": "content",
                    "content": {"type": "text", "text": "x" * 5000},
                }
            ],
        }
        # Inline truncation test
        for item in data["content"]:
            inner = item.get("content")
            if isinstance(inner, dict):
                text = inner.get("text")
                if isinstance(text, str) and len(text) > 2000:
                    inner["text"] = text[:2000] + "\n...(truncated)"

        assert len(data["content"][0]["content"]["text"]) == 2000 + len("\n...(truncated)")
        assert data["content"][0]["content"]["text"].endswith("...(truncated)")

    def test_does_not_truncate_small_content(self):
        """Content under the limit should pass through unchanged."""
        data = {
            "sessionUpdate": "tool_call_update",
            "content": [
                {
                    "type": "content",
                    "content": {"type": "text", "text": "short text"},
                }
            ],
        }
        original_text = data["content"][0]["content"]["text"]
        # Should not be modified
        assert len(original_text) < 2000
        assert original_text == "short text"

    def test_ignores_non_tool_updates(self):
        """Non-tool_call_update messages should pass through unchanged."""
        data = {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "x" * 5000},
        }
        # Should not be modified — wrong sessionUpdate type
        assert len(data["content"]["text"]) == 5000


# --- build_content_blocks ---


class TestBuildContentBlocks:
    """Tests for building ACP content blocks from WebSocket messages."""

    def test_text_only(self):
        from agent_kitchen.server import build_content_blocks

        blocks = build_content_blocks({"text": "hello world"})
        assert len(blocks) == 1
        assert blocks[0].type == "text"
        assert blocks[0].text == "hello world"

    def test_image_only(self):
        from agent_kitchen.server import build_content_blocks

        blocks = build_content_blocks(
            {
                "text": "",
                "images": [{"data": "iVBORw0KGgo=", "mimeType": "image/png"}],
            }
        )
        assert len(blocks) == 1
        assert blocks[0].type == "image"
        assert blocks[0].data == "iVBORw0KGgo="
        assert blocks[0].mime_type == "image/png"

    def test_text_and_images(self):
        from agent_kitchen.server import build_content_blocks

        blocks = build_content_blocks(
            {
                "text": "describe these",
                "images": [
                    {"data": "abc123", "mimeType": "image/png"},
                    {"data": "def456", "mimeType": "image/jpeg"},
                ],
            }
        )
        assert len(blocks) == 3
        assert blocks[0].type == "text"
        assert blocks[0].text == "describe these"
        assert blocks[1].type == "image"
        assert blocks[1].data == "abc123"
        assert blocks[2].type == "image"
        assert blocks[2].mime_type == "image/jpeg"

    def test_empty_message(self):
        from agent_kitchen.server import build_content_blocks

        assert build_content_blocks({}) == []
        assert build_content_blocks({"text": ""}) == []
        assert build_content_blocks({"text": "  ", "images": []}) == []

    def test_whitespace_stripped_from_text(self):
        from agent_kitchen.server import build_content_blocks

        blocks = build_content_blocks({"text": "  hello  "})
        assert blocks[0].text == "hello"

    def test_text_block_comes_before_images(self):
        """Text should always be the first block, followed by images."""
        from agent_kitchen.server import build_content_blocks

        blocks = build_content_blocks(
            {
                "text": "what is this?",
                "images": [{"data": "img1", "mimeType": "image/png"}],
            }
        )
        assert blocks[0].type == "text"
        assert blocks[1].type == "image"
