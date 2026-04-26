# ABOUTME: ACP bridge that spawns coding agents via the Agent Client Protocol.
# ABOUTME: Manages agent subprocess lifecycle and relays updates to a callback.

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any, Callable, Coroutine

import acp
from acp.schema import (
    ClientCapabilities,
    Implementation,
    RequestPermissionResponse,
)

logger = logging.getLogger(__name__)

# Map agent names to their ACP spawn commands
AGENT_COMMANDS: dict[str, list[str]] = {
    "claude": ["npx", "-y", "@agentclientprotocol/claude-agent-acp"],
    "codex": ["npx", "@zed-industries/codex-acp"],
    "copilot": ["npx", "@github/copilot-language-server", "--acp"],
    "gemini": ["npx", "@google/gemini-cli", "--experimental-acp"],
}

# 0.31.0 is the first claude-agent-acp release that bundles a Claude Code
# binary aware of Opus 4.7. Older cached versions resolve "opus" to 4.6.
MIN_CLAUDE_AGENT_ACP_VERSION = "0.31.0"


def _version_tuple(v: str) -> tuple[int, ...]:
    """Convert "0.31.0" → (0, 31, 0). Non-numeric segments become 0."""
    parts = []
    for segment in v.split("."):
        try:
            parts.append(int(segment.split("-", 1)[0]))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def check_min_agent_version(agent_info) -> None:
    """Raise RuntimeError if claude-agent-acp is older than the minimum required.

    Other agents are not version-checked. The error message tells the user how
    to refresh the npx cache, since the most common cause is a stale cache
    serving an old version even though the installed agent-kitchen is current.
    """
    name = getattr(agent_info, "name", "") or ""
    if name != "@agentclientprotocol/claude-agent-acp":
        return
    version = getattr(agent_info, "version", None)
    if version is None or _version_tuple(version) < _version_tuple(MIN_CLAUDE_AGENT_ACP_VERSION):
        reported = version or "unknown"
        raise RuntimeError(
            f"claude-agent-acp {reported} is too old "
            f"(need >= {MIN_CLAUDE_AGENT_ACP_VERSION} for Claude Opus 4.7 support). "
            "Refresh the npx cache with: "
            "npx -y @agentclientprotocol/claude-agent-acp@latest"
        )


class AuthRequiredError(Exception):
    """Raised when an agent requires authentication before proceeding."""

    def __init__(self, agent_name: str, message: str):
        self.agent_name = agent_name
        self.message = message
        super().__init__(message)


class KitchenACPClient(acp.Client):
    """ACP client callbacks for Agent Kitchen.

    FS access is scoped to the session's cwd to prevent out-of-repo writes.
    Terminal callbacks are NOT implemented — we don't advertise terminal
    capability, so agents won't call these methods.
    """

    def __init__(
        self,
        on_update: Callable[[str, Any], Coroutine],
        cwd: Path,
        auto_approve: bool = True,
    ):
        self._on_update = on_update
        self._cwd = cwd.resolve()
        self._auto_approve = auto_approve

    def _validate_path(self, path: str) -> Path:
        """Ensure path is under cwd. Raises ValueError if not."""
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self._cwd):
            raise ValueError(f"Path {path} is outside session root {self._cwd}")
        return resolved

    async def session_update(self, session_id, update, **kwargs):
        await self._on_update(session_id, update)

    async def request_permission(self, options, session_id, tool_call, **kwargs):
        """Handle permission requests from the agent.

        ACP permissions use RequestPermissionResponse with a nested outcome:
          - AllowedOutcome: {"outcome": "selected", "optionId": "<id>"}
          - DeniedOutcome:  {"outcome": "cancelled"}
        """
        if self._auto_approve and options:
            oid = getattr(options[0], "optionId", None)
            logger.debug("Auto-approving permission: optionId=%s", oid)
            return RequestPermissionResponse(outcome={"outcome": "selected", "optionId": oid})
        return RequestPermissionResponse(outcome={"outcome": "cancelled"})

    async def read_text_file(self, path, line=None, limit=None, **kwargs):
        """Read a file, scoped to session cwd. Supports ACP slice params."""
        validated = self._validate_path(path)
        text = validated.read_text()
        if line is not None:
            lines = text.splitlines(keepends=True)
            start = max(0, line - 1)  # ACP lines are 1-indexed
            end = start + limit if limit else len(lines)
            text = "".join(lines[start:end])
        return {"content": text}

    async def write_text_file(self, path, content, **kwargs):
        """Write a file, scoped to session cwd."""
        validated = self._validate_path(path)
        validated.parent.mkdir(parents=True, exist_ok=True)
        validated.write_text(content)


class ACPBridge:
    """Bridges an ACP agent subprocess to a callback for WebSocket relay.

    Lifecycle: start() → prompt() (repeatable) → close()
    """

    def __init__(
        self,
        agent_command: list[str],
        cwd: str,
        on_update: Callable[[str, Any], Coroutine],
        auto_approve: bool = True,
    ):
        self._agent_command = agent_command
        self._cwd = Path(cwd).resolve()
        self._on_update = on_update
        self._auto_approve = auto_approve
        self._conn = None
        self._proc = None
        self._ctx = None
        self._session_id: str | None = None
        self._agent_caps = None
        self._agent_info = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def is_alive(self) -> bool:
        """True when the agent process is running and the connection is usable."""
        if self._conn is None or self._proc is None:
            return False
        return self._proc.returncode is None

    @property
    def can_load_session(self) -> bool:
        if self._agent_caps is None:
            return False
        return getattr(self._agent_caps, "load_session", False)

    _INIT_TIMEOUT = 30  # seconds to wait for agent process to initialize

    async def start(self, session_id: str | None = None) -> dict:
        """Spawn agent, initialize, create/load session.

        Returns {"sessionId": str, "historyLoaded": bool, "agentInfo": dict}.
        Raises AuthRequiredError if agent needs authentication.
        """
        # Pre-flight: verify the agent command is executable
        cmd = self._agent_command[0]
        if not shutil.which(cmd):
            raise RuntimeError(
                f"Agent command not found: {cmd}. Is the agent installed and on PATH?"
            )

        client = KitchenACPClient(self._on_update, self._cwd, self._auto_approve)

        # 10MB buffer — the default 64KB is too small for large file reads
        self._ctx = acp.spawn_agent_process(
            client, *self._agent_command, transport_kwargs={"limit": 10 * 1024 * 1024}
        )
        self._conn, self._proc = await self._ctx.__aenter__()

        try:
            result = await asyncio.wait_for(
                self._conn.initialize(
                    protocol_version=acp.PROTOCOL_VERSION,
                    client_capabilities=ClientCapabilities(
                        fs={"readTextFile": True, "writeTextFile": True}
                    ),
                    client_info=Implementation(
                        name="agent-kitchen", title="Agent Kitchen", version="0.1.0"
                    ),
                ),
                timeout=self._INIT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await self.close()
            raise RuntimeError(
                f"Agent process failed to initialize within {self._INIT_TIMEOUT}s. "
                "The process may have crashed on startup — check that the agent is "
                "installed correctly."
            )
        self._agent_caps = result.agentCapabilities
        self._agent_info = result.agentInfo
        logger.info(
            "ACP initialized: agent=%s loadSession=%s",
            getattr(self._agent_info, "name", "unknown"),
            self.can_load_session,
        )
        try:
            check_min_agent_version(self._agent_info)
        except RuntimeError:
            await self.close()
            raise

        history_loaded = False
        if session_id and self.can_load_session:
            try:
                await self._conn.load_session(
                    session_id=session_id,
                    cwd=str(self._cwd),
                    mcp_servers=[],
                )
                self._session_id = session_id
                history_loaded = True
                logger.info("Loaded existing session: %s", session_id)
            except Exception:
                logger.warning(
                    "session/load failed for %s, restarting agent process",
                    session_id,
                    exc_info=True,
                )
                # load_session failure can corrupt the transport, so restart
                # the entire agent process before creating a new session.
                await self.close()
                client = KitchenACPClient(self._on_update, self._cwd, self._auto_approve)
                self._ctx = acp.spawn_agent_process(client, *self._agent_command)
                self._conn, self._proc = await self._ctx.__aenter__()
                await self._conn.initialize(
                    protocol_version=acp.PROTOCOL_VERSION,
                    client_capabilities=ClientCapabilities(
                        fs={"readTextFile": True, "writeTextFile": True}
                    ),
                    client_info=Implementation(
                        name="agent-kitchen", title="Agent Kitchen", version="0.1.0"
                    ),
                )
                session_result = await self._conn.new_session(cwd=str(self._cwd), mcp_servers=[])
                self._session_id = session_result.sessionId
        else:
            try:
                session_result = await self._conn.new_session(cwd=str(self._cwd), mcp_servers=[])
                self._session_id = session_result.sessionId
            except Exception as exc:
                error_msg = str(exc).lower()
                if "auth" in error_msg or "login" in error_msg:
                    agent_name = getattr(self._agent_info, "name", "agent")
                    raise AuthRequiredError(
                        agent_name,
                        f"{agent_name} requires authentication.",
                    ) from exc
                raise

        logger.info("Session ready: %s (historyLoaded=%s)", self._session_id, history_loaded)

        return {
            "sessionId": self._session_id,
            "historyLoaded": history_loaded,
            "agentInfo": {
                "name": getattr(self._agent_info, "name", "unknown"),
                "title": getattr(self._agent_info, "title", "Unknown Agent"),
                "version": getattr(self._agent_info, "version", ""),
            },
        }

    async def restart(self) -> dict:
        """Close the dead bridge and start a new one, resuming the session if possible.

        Returns the same dict as start(): {sessionId, historyLoaded, agentInfo}.
        """
        old_session_id = self._session_id
        await self.close()
        return await self.start(session_id=old_session_id)

    async def prompt(self, content_blocks: list) -> str:
        """Send user message. Updates stream via on_update callback. Returns stop_reason.

        content_blocks is a list of ACP content blocks (text_block, image_block, etc.).

        If the agent process has died, automatically restarts and resumes the
        session before sending the message.
        """
        if not self._session_id:
            raise RuntimeError("Bridge not started")

        if not self.is_alive:
            logger.info("Agent process died, restarting for session %s", self._session_id)
            await self.restart()

        response = await self._conn.prompt(
            session_id=self._session_id,
            prompt=content_blocks,
        )
        return getattr(response, "stopReason", "end_turn")

    async def cancel(self):
        """Cancel the current turn."""
        if self._conn and self._session_id:
            try:
                await self._conn.cancel(session_id=self._session_id)
            except Exception:
                logger.warning("Cancel failed", exc_info=True)

    async def close(self):
        """Terminate the agent process and clean up."""
        if self._ctx:
            try:
                await self._ctx.__aexit__(None, None, None)
            except Exception:
                logger.warning("ACP cleanup failed", exc_info=True)
            self._ctx = None
            self._conn = None
            self._proc = None
