# ABOUTME: Tests for ACP session lifecycle — process death detection, session termination,
# ABOUTME: and auto-restart/resume when a user messages a dead session.

from unittest.mock import AsyncMock, MagicMock, patch

import acp
import pytest

from agent_kitchen.acp_bridge import ACPBridge


class TestStartPreflight:
    """ACPBridge.start() should fail fast when the agent command is missing."""

    @pytest.mark.asyncio
    async def test_start_fails_when_command_not_found(self):
        bridge = ACPBridge(
            agent_command=["nonexistent-agent-binary-xyz"],
            cwd="/tmp",
            on_update=AsyncMock(),
        )
        with pytest.raises(RuntimeError, match="Agent command not found"):
            await bridge.start()


class TestIsAlive:
    """ACPBridge.is_alive should reflect the actual process state."""

    def test_not_alive_before_start(self):
        bridge = ACPBridge(
            agent_command=["echo"],
            cwd="/tmp",
            on_update=AsyncMock(),
        )
        assert bridge.is_alive is False

    def test_alive_when_process_running(self):
        bridge = ACPBridge(
            agent_command=["echo"],
            cwd="/tmp",
            on_update=AsyncMock(),
        )
        proc = MagicMock()
        proc.returncode = None  # process still running
        bridge._proc = proc
        bridge._conn = MagicMock()
        assert bridge.is_alive is True

    def test_not_alive_when_process_exited(self):
        bridge = ACPBridge(
            agent_command=["echo"],
            cwd="/tmp",
            on_update=AsyncMock(),
        )
        proc = MagicMock()
        proc.returncode = 0  # process exited
        bridge._proc = proc
        bridge._conn = MagicMock()
        assert bridge.is_alive is False

    def test_not_alive_when_no_connection(self):
        bridge = ACPBridge(
            agent_command=["echo"],
            cwd="/tmp",
            on_update=AsyncMock(),
        )
        bridge._proc = MagicMock(returncode=None)
        bridge._conn = None
        assert bridge.is_alive is False


class TestRestart:
    """ACPBridge.restart() should close old bridge and start a new one."""

    @pytest.mark.asyncio
    async def test_restart_closes_old_and_starts_new(self):
        bridge = ACPBridge(
            agent_command=["echo"],
            cwd="/tmp",
            on_update=AsyncMock(),
        )
        bridge._session_id = "old-session-123"

        with (
            patch.object(bridge, "close", new_callable=AsyncMock) as mock_close,
            patch.object(bridge, "start", new_callable=AsyncMock) as mock_start,
        ):
            mock_start.return_value = {
                "sessionId": "new-session-456",
                "historyLoaded": True,
                "agentInfo": {"name": "claude-code", "title": "Claude Code", "version": "1.0"},
            }
            result = await bridge.restart()

        mock_close.assert_called_once()
        mock_start.assert_called_once_with(session_id="old-session-123")
        assert result["sessionId"] == "new-session-456"
        assert result["historyLoaded"] is True

    @pytest.mark.asyncio
    async def test_restart_without_session_id(self):
        bridge = ACPBridge(
            agent_command=["echo"],
            cwd="/tmp",
            on_update=AsyncMock(),
        )
        bridge._session_id = None

        with (
            patch.object(bridge, "close", new_callable=AsyncMock),
            patch.object(bridge, "start", new_callable=AsyncMock) as mock_start,
        ):
            mock_start.return_value = {
                "sessionId": "fresh-session",
                "historyLoaded": False,
                "agentInfo": {"name": "claude-code", "title": "Claude Code", "version": "1.0"},
            }
            result = await bridge.restart()

        mock_start.assert_called_once_with(session_id=None)
        assert result["historyLoaded"] is False


class TestPromptAutoRestart:
    """Bridge should auto-restart when prompting a dead session."""

    @pytest.mark.asyncio
    async def test_prompt_restarts_dead_bridge(self):
        """When is_alive is False, prompt() should restart before sending."""
        bridge = ACPBridge(
            agent_command=["echo"],
            cwd="/tmp",
            on_update=AsyncMock(),
        )
        # Simulate a dead bridge with a session ID
        bridge._session_id = "dead-session"
        bridge._proc = MagicMock(returncode=1)  # exited
        bridge._conn = MagicMock()

        mock_response = MagicMock()
        mock_response.stopReason = "end_turn"
        live_conn = AsyncMock()
        live_conn.prompt = AsyncMock(return_value=mock_response)

        async def fake_restart():
            # Simulate restart: replace dead conn/proc with live ones
            bridge._conn = live_conn
            bridge._proc = MagicMock(returncode=None)
            return {
                "sessionId": "restarted-session",
                "historyLoaded": True,
                "agentInfo": {"name": "claude-code", "title": "Claude Code", "version": "1.0"},
            }

        with patch.object(bridge, "restart", side_effect=fake_restart) as mock_restart:
            stop_reason = await bridge.prompt([acp.text_block("hello after restart")])

        mock_restart.assert_called_once()
        assert stop_reason == "end_turn"


class TestPromptContentBlocks:
    """Bridge.prompt() should forward content blocks to the ACP connection."""

    @pytest.mark.asyncio
    async def test_prompt_with_text_only(self):
        bridge = ACPBridge(
            agent_command=["echo"],
            cwd="/tmp",
            on_update=AsyncMock(),
        )
        bridge._session_id = "test-session"
        mock_response = MagicMock()
        mock_response.stopReason = "end_turn"
        bridge._conn = AsyncMock()
        bridge._conn.prompt = AsyncMock(return_value=mock_response)
        bridge._proc = MagicMock(returncode=None)

        blocks = [acp.text_block("hello")]
        await bridge.prompt(blocks)

        bridge._conn.prompt.assert_called_once_with(
            session_id="test-session",
            prompt=blocks,
        )

    @pytest.mark.asyncio
    async def test_prompt_with_image_block(self):
        bridge = ACPBridge(
            agent_command=["echo"],
            cwd="/tmp",
            on_update=AsyncMock(),
        )
        bridge._session_id = "test-session"
        mock_response = MagicMock()
        mock_response.stopReason = "end_turn"
        bridge._conn = AsyncMock()
        bridge._conn.prompt = AsyncMock(return_value=mock_response)
        bridge._proc = MagicMock(returncode=None)

        blocks = [
            acp.text_block("describe this image"),
            acp.image_block("iVBORw0KGgo=", "image/png"),
        ]
        await bridge.prompt(blocks)

        bridge._conn.prompt.assert_called_once_with(
            session_id="test-session",
            prompt=blocks,
        )

    @pytest.mark.asyncio
    async def test_prompt_with_image_only(self):
        bridge = ACPBridge(
            agent_command=["echo"],
            cwd="/tmp",
            on_update=AsyncMock(),
        )
        bridge._session_id = "test-session"
        mock_response = MagicMock()
        mock_response.stopReason = "end_turn"
        bridge._conn = AsyncMock()
        bridge._conn.prompt = AsyncMock(return_value=mock_response)
        bridge._proc = MagicMock(returncode=None)

        blocks = [acp.image_block("iVBORw0KGgo=", "image/png")]
        result = await bridge.prompt(blocks)

        assert result == "end_turn"
        bridge._conn.prompt.assert_called_once_with(
            session_id="test-session",
            prompt=blocks,
        )
