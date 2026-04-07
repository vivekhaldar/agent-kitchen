# ABOUTME: FastAPI web server serving the dashboard API and static files.
# ABOUTME: Orchestrates scanning, summarization, and grouping into a unified pipeline.

import asyncio
import dataclasses
import json
import logging
import os
import shlex
import subprocess
import time
import uuid
import webbrowser
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from ptyprocess import PtyProcess

from agent_kitchen import config as _config
from agent_kitchen.acp_bridge import AGENT_COMMANDS, ACPBridge, AuthRequiredError
from agent_kitchen.cache import SummaryCache
from agent_kitchen.config import CACHE_DIR
from agent_kitchen.git_status import get_repo_root
from agent_kitchen.grouping import group_sessions
from agent_kitchen.scanner import scan_claude_sessions, scan_codex_sessions
from agent_kitchen.summarizer import _make_fallback, batch_summarize, extract_context_for_summary
from agent_kitchen.timeline import apply_cached_timelines, batch_generate_timelines

logger = logging.getLogger(__name__)

# In-memory dashboard data, swapped atomically on each scan
_dashboard_data: dict | None = None

# Background refresh task handle, stored for cancellation on shutdown
_refresh_task: asyncio.Task | None = None

# Active PTY processes keyed by terminal ID
_terminals: dict[str, PtyProcess] = {}

# URL to open in browser after server is ready (set by create_app)
_open_browser_url: str | None = None


async def _open_browser_when_ready():
    """Open the browser after a brief delay to let uvicorn start accepting connections."""
    if _open_browser_url:
        await asyncio.sleep(0.5)
        webbrowser.open(_open_browser_url)


def _spawn_pty(
    source: str, session_id: str | None, cwd: str, cols: int = 120, rows: int = 30
) -> tuple[str, PtyProcess]:
    """Spawn a PTY running a session command.

    If session_id is provided, resume that session. Otherwise start a new one.
    """
    if session_id:
        if source == "claude":
            resume_id = shlex.quote(session_id)
            shell_cmd = (
                f"unset CLAUDECODE && claude --dangerously-skip-permissions --resume {resume_id}"
            )
        elif source == "codex":
            shell_cmd = f"codex resume {shlex.quote(session_id)}"
        else:
            raise ValueError(f"Unknown source: {source}")
    else:
        shell_cmd = "unset CLAUDECODE && claude --dangerously-skip-permissions"

    env = {**os.environ, "TERM": "xterm-256color"}
    env.pop("CLAUDECODE", None)
    pty = PtyProcess.spawn(
        ["/bin/zsh", "-c", shell_cmd],
        cwd=cwd,
        dimensions=(rows, cols),
        env=env,
    )
    tid = uuid.uuid4().hex[:12]
    _terminals[tid] = pty
    return tid, pty


def _serialize_dashboard(data: dict) -> dict:
    """Convert dashboard data with dataclass objects to JSON-serializable dicts."""

    def serialize_obj(obj):
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            d = {}
            for f in dataclasses.fields(obj):
                val = getattr(obj, f.name)
                d[f.name] = serialize_obj(val)
            return d
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, list):
            return [serialize_obj(item) for item in obj]
        return obj

    return {
        "repo_groups": [serialize_obj(g) for g in data.get("repo_groups", [])],
        "non_repo_groups": [serialize_obj(g) for g in data.get("non_repo_groups", [])],
        "last_scanned": data.get("last_scanned", ""),
        "scan_duration_ms": data.get("scan_duration_ms", 0),
    }


def _scan_and_group(scan_days: int | None = None) -> tuple[list, dict]:
    """Scan sessions and group them (no LLM calls). Returns (all_sessions, dashboard_data)."""
    start = time.monotonic()
    days = scan_days if scan_days is not None else _config.SCAN_WINDOW_DAYS
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Scan both sources (each scanner is independent — one failing shouldn't block the other)
    claude_sessions: list = []
    try:
        claude_sessions = scan_claude_sessions(since)
    except Exception:
        logger.exception("Claude session scan failed")

    codex_sessions: list = []
    try:
        codex_sessions = scan_codex_sessions(since)
    except Exception:
        logger.exception("Codex session scan failed")

    all_sessions = claude_sessions + codex_sessions

    logger.info(
        "Found %d sessions (%d Claude, %d Codex)",
        len(all_sessions),
        len(claude_sessions),
        len(codex_sessions),
    )

    # Resolve repo roots for sessions missing them
    for session in all_sessions:
        if session.repo_root is None:
            root = get_repo_root(session.cwd)
            if root:
                session.repo_root = root
                session.repo_name = os.path.basename(root)

    # Apply cached summaries, or generate quick fallbacks from session content
    cache = SummaryCache(CACHE_DIR / "summaries.json")
    for session in all_sessions:
        cached = cache.get(session.id)
        if cached:
            session.summary = cached["summary"]
            session.status = cached["status"]
        elif not session.summary:
            context = extract_context_for_summary(session.file_path, session.source)
            if context:
                fallback = _make_fallback(context)
                session.summary = fallback.summary
                session.status = fallback.status

    # Group by repo
    try:
        repo_groups, non_repo_groups = group_sessions(all_sessions)
    except Exception:
        logger.exception("Session grouping failed")
        repo_groups, non_repo_groups = [], []

    # Apply cached timelines or generate fallbacks
    try:
        apply_cached_timelines(repo_groups, cache)
        apply_cached_timelines(non_repo_groups, cache)
    except Exception:
        logger.exception("Timeline application failed")

    elapsed_ms = int((time.monotonic() - start) * 1000)

    data = {
        "repo_groups": repo_groups,
        "non_repo_groups": non_repo_groups,
        "last_scanned": datetime.now(timezone.utc).isoformat(),
        "scan_duration_ms": elapsed_ms,
    }
    return all_sessions, data


async def _summarize_and_regroup(all_sessions: list, scan_days: int | None = None) -> dict:
    """Run LLM summarization on sessions that need it, then regroup."""
    start = time.monotonic()
    cache = SummaryCache(CACHE_DIR / "summaries.json")
    needs_summary = [
        s for s in all_sessions if not s.summary or cache.needs_refresh(s.id, s.file_mtime)
    ]
    if not needs_summary:
        logger.info("All sessions already have cached summaries")
        return _scan_and_group(scan_days=scan_days)[1]

    logger.info("Summarizing %d sessions via LLM", len(needs_summary))

    results = await batch_summarize(all_sessions, cache)
    for session, result in zip(all_sessions, results):
        if result:
            session.summary = result.summary
            session.status = result.status

    # Regroup with updated summaries
    try:
        repo_groups, non_repo_groups = group_sessions(all_sessions)
    except Exception:
        logger.exception("Session grouping failed")
        repo_groups, non_repo_groups = [], []

    # Generate LLM-powered timelines for all groups
    try:
        await batch_generate_timelines(repo_groups + non_repo_groups, cache)
    except Exception:
        logger.exception("Timeline generation failed")

    elapsed_ms = int((time.monotonic() - start) * 1000)
    logger.info("Summarization complete in %dms", elapsed_ms)

    return {
        "repo_groups": repo_groups,
        "non_repo_groups": non_repo_groups,
        "last_scanned": datetime.now(timezone.utc).isoformat(),
        "scan_duration_ms": elapsed_ms,
    }


async def run_scan_pipeline(scan_days: int | None = None) -> dict:
    """Run the full scan → summarize → group pipeline.

    Args:
        scan_days: Number of days to scan. Defaults to config.SCAN_WINDOW_DAYS.

    Returns a dict with repo_groups, non_repo_groups, last_scanned, scan_duration_ms.
    """
    all_sessions, data = _scan_and_group(scan_days=scan_days)
    data = await _summarize_and_regroup(all_sessions, scan_days=scan_days)
    return data


def _launch_in_terminal(source: str, session_id: str, cwd: str) -> None:
    """Open a new terminal window with the resume command for a session."""
    if source == "claude":
        cmd = (
            f"cd {cwd} && unset CLAUDECODE"
            f" && claude --dangerously-skip-permissions --resume {session_id}"
        )
    elif source == "codex":
        cmd = f"cd {cwd} && codex resume {session_id}"
    else:
        raise ValueError(f"Unknown source: {source}")

    terminal = _config.TERMINAL_APP.lower()
    if terminal == "ghostty":
        # Use --command config key to tell Ghostty what to run in the new window.
        # -n opens a new instance (new window); --args passes flags to Ghostty.
        shell_cmd = f"/bin/zsh -c {shlex.quote(cmd)}"
        subprocess.run(
            ["open", "-na", "Ghostty", "--args", f"--command={shell_cmd}"],
            check=True,
        )
    elif terminal == "terminal":
        applescript = f'''
        tell application "Terminal"
            activate
            do script "{cmd}"
        end tell
        '''
        subprocess.run(["osascript", "-e", applescript], check=True)
    else:
        raise ValueError(
            f"Unknown terminal app: {_config.TERMINAL_APP}. "
            "Set AGENT_KITCHEN_TERMINAL to 'ghostty' or 'terminal'."
        )


async def _background_refresh_loop(interval: int = _config.REFRESH_INTERVAL_SECONDS) -> None:
    """Periodically re-run the scan pipeline and swap in-memory data atomically."""
    global _dashboard_data
    while True:
        await asyncio.sleep(interval)
        try:
            logger.info("Background refresh starting")
            new_data = await run_scan_pipeline()
            _dashboard_data = new_data
            logger.info("Background refresh complete")
        except Exception:
            logger.exception("Background refresh failed")


async def _initial_scan_then_refresh() -> None:
    """Run a fast scan immediately, then summarize, then enter periodic refresh."""
    global _dashboard_data
    try:
        logger.info("Initial scan starting (fast, no LLM)")
        all_sessions, data = _scan_and_group()
        _dashboard_data = data
        logger.info(
            "Initial scan complete — dashboard ready with %d sessions",
            sum(len(g.sessions) for g in data["repo_groups"])
            + sum(len(g.sessions) for g in data["non_repo_groups"]),
        )

        # Run LLM summarization in the background and update data when done
        logger.info("Starting background LLM summarization")
        summarized_data = await _summarize_and_regroup(all_sessions)
        _dashboard_data = summarized_data
        logger.info("Background summarization complete")
    except Exception:
        logger.exception("Initial scan failed")
    await _background_refresh_loop()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Start initial scan + background refresh on startup, cancel on shutdown."""
    global _refresh_task
    _refresh_task = asyncio.create_task(_initial_scan_then_refresh())
    asyncio.create_task(_open_browser_when_ready())
    yield
    _refresh_task.cancel()
    try:
        await _refresh_task
    except asyncio.CancelledError:
        pass
    _refresh_task = None


@asynccontextmanager
async def _scan_only_lifespan(app: FastAPI):
    """Scan and group sessions on startup without LLM summarization or background refresh."""
    global _dashboard_data
    try:
        logger.info("Scan-only mode: scanning sessions (no LLM, no background refresh)")
        _all_sessions, data = _scan_and_group()
        _dashboard_data = data
        logger.info(
            "Scan-only mode complete — %d sessions loaded from cache/fallback",
            sum(len(g.sessions) for g in data["repo_groups"])
            + sum(len(g.sessions) for g in data["non_repo_groups"]),
        )
    except Exception:
        logger.exception("Scan-only startup failed")
    asyncio.create_task(_open_browser_when_ready())
    yield


def create_app(
    *,
    enable_background_refresh: bool = True,
    summarize: bool = True,
    open_browser: str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        enable_background_refresh: If True, start periodic background rescan on startup.
            Set to False in tests to avoid background tasks.
        summarize: If False, skip LLM summarization and background refresh.
            Uses cached summaries and fallbacks only.
        open_browser: If set, open this URL in the browser once the server is ready.
    """
    global _open_browser_url
    _open_browser_url = open_browser
    if not enable_background_refresh:
        lifespan = None
    elif not summarize:
        lifespan = _scan_only_lifespan
    else:
        lifespan = _lifespan
    app = FastAPI(title="Agent Kitchen", lifespan=lifespan)

    @app.get("/api/sessions")
    async def get_sessions():
        global _dashboard_data
        if _dashboard_data is None:
            return JSONResponse(
                content={
                    "repo_groups": [],
                    "non_repo_groups": [],
                    "last_scanned": "",
                    "scan_duration_ms": 0,
                }
            )
        return JSONResponse(content=_serialize_dashboard(_dashboard_data))

    @app.get("/api/refresh")
    async def refresh(scan_days: int = Query(default=_config.SCAN_WINDOW_DAYS)):
        global _dashboard_data
        data = await run_scan_pipeline(scan_days=scan_days)
        _dashboard_data = data
        return JSONResponse(content=_serialize_dashboard(data))

    @app.get("/api/launch")
    async def launch(
        source: str = Query(...),
        session_id: str = Query(...),
        cwd: str = Query(...),
    ):
        if source not in ("claude", "codex"):
            return JSONResponse(
                status_code=400,
                content={"error": f"Invalid source: {source}. Must be 'claude' or 'codex'."},
            )
        try:
            _launch_in_terminal(source, session_id, cwd)
            return JSONResponse(content={"ok": True})
        except (OSError, subprocess.CalledProcessError) as e:
            return JSONResponse(
                status_code=500,
                content={"error": f"Failed to launch terminal: {e}"},
            )

    @app.websocket("/ws/terminal")
    async def terminal_ws(ws: WebSocket):
        source = ws.query_params.get("source", "")
        session_id = ws.query_params.get("session_id", "")
        cwd = ws.query_params.get("cwd", "")
        mode = ws.query_params.get("mode", "resume")

        if mode == "new":
            # New session only needs cwd; source defaults to claude
            source = source or "claude"
            session_id = None
            if not cwd:
                await ws.close(code=1008, reason="Missing cwd for new session")
                return
        elif source not in ("claude", "codex") or not session_id or not cwd:
            await ws.close(code=1008, reason="Missing or invalid query params")
            return

        await ws.accept()
        logger.info("Terminal WS accepted: source=%s session=%s cwd=%s", source, session_id, cwd)

        cols = int(ws.query_params.get("cols", 120))
        rows = int(ws.query_params.get("rows", 30))

        try:
            tid, pty = _spawn_pty(source, session_id, cwd, cols=cols, rows=rows)
        except Exception:
            logger.exception("Failed to spawn PTY")
            await ws.close(code=1011, reason="PTY spawn failed")
            return

        logger.info("PTY spawned: tid=%s pid=%d", tid, pty.pid)
        reader_task = None

        try:
            # PTY → WebSocket: read in a thread to avoid blocking the event loop
            async def pty_reader():
                loop = asyncio.get_event_loop()
                while True:
                    try:
                        data = await loop.run_in_executor(
                            None, lambda: pty.read(4096).decode("utf-8", errors="replace")
                        )
                        await ws.send_text(data)
                    except EOFError:
                        logger.info("PTY EOF for tid=%s", tid)
                        await ws.close()
                        break
                    except Exception:
                        logger.exception("PTY reader error for tid=%s", tid)
                        break

            reader_task = asyncio.create_task(pty_reader())

            # WebSocket → PTY: forward keystrokes, handle resize messages
            while True:
                text = await ws.receive_text()
                if text.startswith('{"type":"resize"'):
                    try:
                        msg = json.loads(text)
                        pty.setwinsize(msg["rows"], msg["cols"])
                    except (json.JSONDecodeError, KeyError):
                        pass
                else:
                    pty.write(text.encode("utf-8"))
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected for tid=%s", tid)
        except Exception:
            logger.exception("Terminal WS error for tid=%s", tid)
        finally:
            if reader_task:
                reader_task.cancel()
            if pty.isalive():
                pty.terminate(force=True)
            _terminals.pop(tid, None)
            logger.info("Terminal cleanup done for tid=%s", tid)

    _TOOL_CONTENT_MAX = 2000

    def _truncate_tool_content(data: dict) -> None:
        """Truncate large text payloads in tool_call_update messages.

        Agents can return entire file contents in tool results. The frontend
        only shows a preview, so we cap text here to avoid sending megabytes
        over the WebSocket.
        """
        if data.get("sessionUpdate") != "tool_call_update":
            return
        content = data.get("content")
        if not isinstance(content, list):
            return
        for item in content:
            if not isinstance(item, dict):
                continue
            inner = item.get("content")
            if isinstance(inner, dict):
                text = inner.get("text")
                if isinstance(text, str) and len(text) > _TOOL_CONTENT_MAX:
                    inner["text"] = text[:_TOOL_CONTENT_MAX] + "\n...(truncated)"

    @app.websocket("/ws/chat")
    async def chat_ws(ws: WebSocket):
        await ws.accept()

        # Wait for the "start" message with agent, cwd, optional sessionId
        try:
            start_msg = await ws.receive_json()
        except Exception:
            await ws.close(code=1008, reason="Expected JSON start message")
            return

        if start_msg.get("type") != "start":
            await ws.close(code=1008, reason="First message must be type=start")
            return

        agent_name = start_msg.get("agent", "claude")
        cwd = start_msg.get("cwd", "")
        session_id = start_msg.get("sessionId")
        auto_approve = start_msg.get("autoApprove", True)

        if agent_name not in AGENT_COMMANDS:
            await ws.send_json({"type": "error", "message": f"Unknown agent: {agent_name}"})
            await ws.close()
            return

        if not cwd or not Path(cwd).is_dir():
            await ws.send_json({"type": "error", "message": f"Invalid cwd: {cwd}"})
            await ws.close()
            return

        logger.info("Chat WS: agent=%s cwd=%s session=%s", agent_name, cwd, session_id)

        # Callback: forward ACP updates to WebSocket
        async def on_update(sid, update):
            try:
                # Serialize the update — it may be a Pydantic model or dict
                if hasattr(update, "model_dump"):
                    data = update.model_dump(by_alias=True, exclude_none=True)
                elif isinstance(update, dict):
                    data = update
                else:
                    data = {"raw": str(update)}
                data["type"] = "update"
                _truncate_tool_content(data)
                await ws.send_json(data)
            except Exception:
                logger.debug("Failed to forward update", exc_info=True)

        bridge = ACPBridge(
            agent_command=AGENT_COMMANDS[agent_name],
            cwd=cwd,
            on_update=on_update,
            auto_approve=auto_approve,
        )

        # Background task: monitor agent process health
        async def monitor_process():
            """Poll bridge liveness and notify the frontend when the agent dies."""
            while True:
                await asyncio.sleep(2)
                if not bridge.is_alive and bridge.session_id:
                    logger.info("Agent process died for session %s", bridge.session_id)
                    try:
                        await ws.send_json({"type": "session_terminated"})
                    except Exception:
                        pass
                    return

        monitor_task = None

        try:
            # Start the agent and session
            try:
                init_result = await bridge.start(session_id=session_id)
                await ws.send_json({"type": "session_init", **init_result})
                monitor_task = asyncio.create_task(monitor_process())
            except AuthRequiredError as e:
                await ws.send_json(
                    {
                        "type": "auth_required",
                        "agentName": e.agent_name,
                        "message": e.message,
                    }
                )
                # Wait for a retry message
                while True:
                    msg = await ws.receive_json()
                    if msg.get("type") == "retry":
                        try:
                            init_result = await bridge.start(session_id=session_id)
                            await ws.send_json({"type": "session_init", **init_result})
                            monitor_task = asyncio.create_task(monitor_process())
                            break
                        except AuthRequiredError as e2:
                            await ws.send_json(
                                {
                                    "type": "auth_required",
                                    "agentName": e2.agent_name,
                                    "message": e2.message,
                                }
                            )
                    else:
                        continue
            except Exception as e:
                await ws.send_json({"type": "error", "message": str(e)})
                await ws.close()
                return

            # Message loop: receive user messages and prompt the agent
            while True:
                msg = await ws.receive_json()
                msg_type = msg.get("type", "")

                if msg_type == "user_message":
                    text = msg.get("text", "").strip()
                    if not text:
                        continue
                    try:
                        # prompt() auto-restarts if the agent process died
                        was_dead = not bridge.is_alive
                        stop_reason = await bridge.prompt(text)
                        if was_dead:
                            # Notify frontend that session was restarted
                            await ws.send_json(
                                {
                                    "type": "session_restarted",
                                    "sessionId": bridge.session_id,
                                }
                            )
                            # Restart the monitor for the new process
                            if monitor_task and not monitor_task.done():
                                monitor_task.cancel()
                            monitor_task = asyncio.create_task(monitor_process())
                        await ws.send_json(
                            {
                                "type": "turn_complete",
                                "stopReason": stop_reason,
                            }
                        )
                    except Exception as e:
                        logger.exception("Prompt failed")
                        await ws.send_json({"type": "error", "message": str(e)})

                elif msg_type == "cancel":
                    await bridge.cancel()

        except WebSocketDisconnect:
            logger.info("Chat WS disconnected")
        except Exception:
            logger.exception("Chat WS error")
        finally:
            if monitor_task and not monitor_task.done():
                monitor_task.cancel()
            await bridge.close()
            logger.info("Chat WS cleanup done")

    # Mount static files (serve index.html at root)
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


def main():
    """Entry point for the agent-kitchen CLI command."""
    from agent_kitchen.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
