# ABOUTME: Pre-indexes and LLM-summarizes all Claude/Codex sessions.
# ABOUTME: Populates the summary cache so the dashboard loads instantly.

import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from agent_kitchen.cache import SummaryCache
from agent_kitchen.config import CACHE_DIR, setup_auth
from agent_kitchen.scanner import scan_claude_sessions, scan_codex_sessions
from agent_kitchen.summarizer import (
    _make_fallback,
    extract_context_for_summary,
    summarize_session,
)

logger = logging.getLogger(__name__)


async def run_indexer(
    scan_days: int,
    concurrency: int,
    dry_run: bool,
    force: bool,
) -> None:
    since = datetime.now(timezone.utc) - timedelta(days=scan_days)

    # Scan
    logger.info("Scanning sessions from the last %d days...", scan_days)
    scan_start = time.monotonic()

    claude_sessions = []
    try:
        claude_sessions = scan_claude_sessions(since)
        logger.info("Found %d Claude sessions", len(claude_sessions))
    except Exception:
        logger.exception("Claude session scan failed")

    codex_sessions = []
    try:
        codex_sessions = scan_codex_sessions(since)
        logger.info("Found %d Codex sessions", len(codex_sessions))
    except Exception:
        logger.exception("Codex session scan failed")

    all_sessions = claude_sessions + codex_sessions
    scan_elapsed = time.monotonic() - scan_start
    logger.info("Scan complete: %d sessions in %.1fs", len(all_sessions), scan_elapsed)

    if not all_sessions:
        logger.info("Nothing to index.")
        return

    # Load cache
    cache_path = CACHE_DIR / "summaries.json"
    cache = SummaryCache(cache_path)

    # Determine which sessions need summarization
    if force:
        needs_work = all_sessions
        logger.info("--force: will re-summarize all %d sessions", len(needs_work))
    else:
        needs_work = [s for s in all_sessions if cache.needs_refresh(s.id, s.file_mtime)]
        cached_count = len(all_sessions) - len(needs_work)
        logger.info(
            "%d sessions already cached, %d need summarization",
            cached_count,
            len(needs_work),
        )

    if not needs_work:
        logger.info("All sessions are up to date. Nothing to do.")
        return

    if dry_run:
        logger.info("Dry run — would summarize %d sessions. Exiting.", len(needs_work))
        return

    # Set up auth for LLM calls
    try:
        setup_auth()
        logger.info("LLM authentication configured")
    except RuntimeError as e:
        logger.error("Auth failed: %s", e)
        logger.error("Cannot run LLM summarization without authentication.")
        sys.exit(1)

    # Summarize with progress logging
    semaphore = asyncio.Semaphore(concurrency)
    completed = 0
    failed = 0
    total = len(needs_work)
    save_interval = 25  # save cache every N completions
    index_start = time.monotonic()

    async def summarize_one(session) -> None:
        nonlocal completed, failed
        context = extract_context_for_summary(session.file_path, session.source)
        if not context:
            result = _make_fallback("")
            cache.set(session.id, result.summary, result.status, session.file_mtime)
            completed += 1
            return

        try:
            async with semaphore:
                result = await summarize_session(context, session.source, session.cwd)
        except Exception:
            logger.warning("Failed to summarize %s (cwd=%s)", session.id[:8], session.cwd)
            result = _make_fallback(context)
            failed += 1

        cache.set(session.id, result.summary, result.status, session.file_mtime)
        completed += 1

        # Progress log
        elapsed = time.monotonic() - index_start
        rate = completed / elapsed if elapsed > 0 else 0
        eta = (total - completed) / rate if rate > 0 else 0
        logger.info(
            "[%d/%d] %s %-8s | %s | %.0f/min, ETA %.0fs",
            completed,
            total,
            session.source,
            session.id[:8],
            result.summary[:60],
            rate * 60,
            eta,
        )

        # Periodic save
        if completed % save_interval == 0:
            cache.save()
            logger.info("Cache saved (%d entries)", len(cache.entries))

    # Process in batches to avoid spawning thousands of coroutines at once
    batch_size = concurrency * 3
    for i in range(0, total, batch_size):
        batch = needs_work[i : i + batch_size]
        tasks = [summarize_one(s) for s in batch]
        await asyncio.gather(*tasks)

    # Final save
    cache.save()
    total_elapsed = time.monotonic() - index_start
    logger.info(
        "Done! Indexed %d sessions in %.0fs (%d failed). Cache: %s (%d entries)",
        completed,
        total_elapsed,
        failed,
        cache_path,
        len(cache.entries),
    )
