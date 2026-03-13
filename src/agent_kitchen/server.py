# ABOUTME: FastAPI web server serving the dashboard API and static files.
# ABOUTME: Orchestrates scanning, summarization, and grouping into a unified pipeline.

import asyncio
import dataclasses
import logging
import os
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from agent_kitchen.cache import SummaryCache
from agent_kitchen.config import CACHE_DIR, REFRESH_INTERVAL_SECONDS, SCAN_WINDOW_DAYS
from agent_kitchen.git_status import get_repo_root
from agent_kitchen.grouping import group_sessions
from agent_kitchen.scanner import scan_claude_sessions, scan_codex_sessions
from agent_kitchen.summarizer import batch_summarize

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


async def run_scan_pipeline() -> dict:
    """Run the full scan → summarize → group pipeline.

    Returns a dict with repo_groups, non_repo_groups, last_scanned, scan_duration_ms.
    """
    start = time.monotonic()
    since = datetime.now(timezone.utc) - timedelta(days=SCAN_WINDOW_DAYS)

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
    print(
        f"Found {len(all_sessions)} sessions "
        f"({len(claude_sessions)} Claude, {len(codex_sessions)} Codex)"
    )

    # Resolve repo roots for sessions missing them
    for session in all_sessions:
        if session.repo_root is None:
            root = get_repo_root(session.cwd)
            if root:
                session.repo_root = root
                session.repo_name = os.path.basename(root)

    # Summarize sessions that need it
    cache = SummaryCache(CACHE_DIR / "summaries.json")
    needs_summary = [
        s for s in all_sessions if not s.summary or cache.needs_refresh(s.id, s.file_mtime)
    ]
    if needs_summary:
        print(f"Summarizing {len(needs_summary)} sessions...")
        logger.info("Summarizing %d sessions", len(needs_summary))

    results = await batch_summarize(all_sessions, cache)
    for session, result in zip(all_sessions, results):
        if result:
            session.summary = result.summary
            session.status = result.status

    # Group by repo
    try:
        repo_groups, non_repo_groups = group_sessions(all_sessions)
    except Exception:
        logger.exception("Session grouping failed")
        repo_groups, non_repo_groups = [], []

    elapsed_ms = int((time.monotonic() - start) * 1000)

    return {
        "repo_groups": repo_groups,
        "non_repo_groups": non_repo_groups,
        "last_scanned": datetime.now(timezone.utc).isoformat(),
        "scan_duration_ms": elapsed_ms,
    }


def _launch_in_terminal(source: str, session_id: str, cwd: str) -> None:
    """Open a new Terminal.app window with the resume command for a session."""
    if source == "claude":
        cmd = f"cd {cwd} && claude --continue --session-id {session_id}"
    elif source == "codex":
        cmd = f"cd {cwd} && codex resume {session_id}"
    else:
        raise ValueError(f"Unknown source: {source}")

    applescript = f'''
    tell application "Terminal"
        activate
        do script "{cmd}"
    end tell
    '''
    subprocess.run(["osascript", "-e", applescript], check=True)


async def _background_refresh_loop(interval: int = REFRESH_INTERVAL_SECONDS) -> None:
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


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Start background refresh on startup, cancel on shutdown."""
    global _refresh_task
    _refresh_task = asyncio.create_task(_background_refresh_loop())
    yield
    _refresh_task.cancel()
    try:
        await _refresh_task
    except asyncio.CancelledError:
        pass
    _refresh_task = None


def create_app(*, enable_background_refresh: bool = True) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        enable_background_refresh: If True, start periodic background rescan on startup.
            Set to False in tests to avoid background tasks.
    """
    lifespan = _lifespan if enable_background_refresh else None
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
