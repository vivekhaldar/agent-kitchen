# ABOUTME: FastAPI web server serving the dashboard API and static files.
# ABOUTME: Orchestrates scanning, summarization, and grouping into a unified pipeline.

import asyncio
import dataclasses
import logging
import os
import shlex
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from agent_kitchen import config as _config
from agent_kitchen.cache import SummaryCache
from agent_kitchen.config import CACHE_DIR
from agent_kitchen.git_status import get_repo_root
from agent_kitchen.grouping import group_sessions
from agent_kitchen.scanner import scan_claude_sessions, scan_codex_sessions
from agent_kitchen.summarizer import _make_fallback, batch_summarize, extract_context_for_summary

logger = logging.getLogger(__name__)

# In-memory dashboard data, swapped atomically on each scan
_dashboard_data: dict | None = None

# Background refresh task handle, stored for cancellation on shutdown
_refresh_task: asyncio.Task | None = None


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


def _scan_and_group() -> tuple[list, dict]:
    """Scan sessions and group them (no LLM calls). Returns (all_sessions, dashboard_data)."""
    start = time.monotonic()
    since = datetime.now(timezone.utc) - timedelta(days=_config.SCAN_WINDOW_DAYS)

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

    elapsed_ms = int((time.monotonic() - start) * 1000)

    data = {
        "repo_groups": repo_groups,
        "non_repo_groups": non_repo_groups,
        "last_scanned": datetime.now(timezone.utc).isoformat(),
        "scan_duration_ms": elapsed_ms,
    }
    return all_sessions, data


async def _summarize_and_regroup(all_sessions: list) -> dict:
    """Run LLM summarization on sessions that need it, then regroup."""
    start = time.monotonic()
    cache = SummaryCache(CACHE_DIR / "summaries.json")
    needs_summary = [
        s for s in all_sessions if not s.summary or cache.needs_refresh(s.id, s.file_mtime)
    ]
    if not needs_summary:
        logger.info("All sessions already have cached summaries")
        return _scan_and_group()[1]

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

    elapsed_ms = int((time.monotonic() - start) * 1000)
    logger.info("Summarization complete in %dms", elapsed_ms)

    return {
        "repo_groups": repo_groups,
        "non_repo_groups": non_repo_groups,
        "last_scanned": datetime.now(timezone.utc).isoformat(),
        "scan_duration_ms": elapsed_ms,
    }


async def run_scan_pipeline() -> dict:
    """Run the full scan → summarize → group pipeline.

    Returns a dict with repo_groups, non_repo_groups, last_scanned, scan_duration_ms.
    """
    all_sessions, data = _scan_and_group()
    data = await _summarize_and_regroup(all_sessions)
    return data


def _launch_in_terminal(source: str, session_id: str, cwd: str) -> None:
    """Open a new terminal window with the resume command for a session."""
    if source == "claude":
        cmd = f"cd {cwd} && unset CLAUDECODE && claude --resume {session_id}"
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
    yield


def create_app(*, enable_background_refresh: bool = True, summarize: bool = True) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        enable_background_refresh: If True, start periodic background rescan on startup.
            Set to False in tests to avoid background tasks.
        summarize: If False, skip LLM summarization and background refresh.
            Uses cached summaries and fallbacks only.
    """
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
    async def refresh():
        global _dashboard_data
        data = await run_scan_pipeline()
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
