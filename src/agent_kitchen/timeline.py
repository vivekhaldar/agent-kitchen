# ABOUTME: Generates group-level timelines showing how work evolved over time.
# ABOUTME: Buckets sessions by day, optionally merges phases via LLM.

import asyncio
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from agent_kitchen.config import SUMMARY_CONCURRENCY
from agent_kitchen.models import NonRepoGroup, RepoGroup, Session, TimelinePhase

# Union type for groups that have timelines
TimelineGroup = RepoGroup | NonRepoGroup

logger = logging.getLogger(__name__)


def _format_period(d: date) -> str:
    """Format a single date as a human-readable period string."""
    today = datetime.now().astimezone().date()
    if d == today:
        return "Today"
    if d == today - timedelta(days=1):
        return "Yesterday"
    return d.strftime("%b %-d")


def _format_date_range(start: date, end: date) -> str:
    """Format a date range as a human-readable period string."""
    if start == end:
        return _format_period(start)
    today = datetime.now().astimezone().date()
    yesterday = today - timedelta(days=1)
    # Handle "Today" and "Yesterday" for single-day ranges
    if end == today and start == yesterday:
        return "Yesterday-Today"
    if start.month == end.month:
        return f"{start.strftime('%b')} {start.day}-{end.day}"
    return f"{start.strftime('%b %-d')}-{end.strftime('%b %-d')}"


def _aggregate_status(sessions: list[Session]) -> str:
    """Determine the overall status for a group of sessions."""
    statuses = {s.status for s in sessions}
    active = {"in progress", "likely in progress", "waiting for input"}
    done = {"done", "likely done"}
    if statuses <= done:
        return "done"
    if statuses & active:
        has_done = bool(statuses & done)
        return "mixed" if has_done else "in progress"
    return "mixed"


def _sessions_by_day(sessions: list[Session]) -> list[tuple[date, list[Session]]]:
    """Bucket sessions by started_at date, newest first."""
    by_day: dict[date, list[Session]] = defaultdict(list)
    for s in sessions:
        ts = s.started_at if s.started_at.tzinfo else s.started_at.replace(tzinfo=timezone.utc)
        day = ts.astimezone().date()
        by_day[day].append(s)
    # Sort days newest first
    sorted_days = sorted(by_day.keys(), reverse=True)
    return [(day, by_day[day]) for day in sorted_days]


def fallback_timeline(sessions: list[Session]) -> list[TimelinePhase]:
    """Generate a timeline without LLM: one phase per day, description from first session."""
    if not sessions:
        return []
    days = _sessions_by_day(sessions)
    phases = []
    for day, day_sessions in days:
        # Use the first session's summary (sessions are sorted by last_active desc within group)
        day_sessions.sort(key=lambda s: s.last_active, reverse=True)
        desc = day_sessions[0].summary or "Work session"
        if len(desc) > 80:
            desc = desc[:77] + "..."
        phases.append(
            TimelinePhase(
                period=_format_period(day),
                description=desc,
                session_count=len(day_sessions),
                status=_aggregate_status(day_sessions),
            )
        )
    return phases


# --- LLM-based timeline generation ---

TIMELINE_PROMPT_TEMPLATE = """\
Analyze the history of work in this repository from coding agent sessions.
Identify 2-5 phases by merging consecutive days doing similar work.

Repository: {repo_name}
Sessions by day (newest first):
{formatted_sessions}

Rules:
- Period: human-readable date range ("Today", "Yesterday", "Mar 10-11")
- Description: 1 short factual sentence, max 80 chars
- Phases ordered newest-first
- Maximum 5 phases; merge aggressively if many days
- Status: "done" if all work complete, "in progress" if active, "mixed" if both\
"""

TIMELINE_SCHEMA = {
    "type": "object",
    "properties": {
        "phases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "period": {"type": "string"},
                    "description": {"type": "string"},
                    "status": {"type": "string", "enum": ["done", "in progress", "mixed"]},
                },
                "required": ["period", "description", "status"],
            },
        },
    },
    "required": ["phases"],
}


def _format_sessions_for_prompt(sessions: list[Session]) -> str:
    """Format sessions grouped by day for the LLM prompt."""
    days = _sessions_by_day(sessions)
    lines = []
    for day, day_sessions in days:
        day_sessions.sort(key=lambda s: s.last_active, reverse=True)
        lines.append(f"{day.strftime('%b %-d')} ({len(day_sessions)} sessions):")
        for s in day_sessions:
            summary = s.summary or "Unknown"
            status = s.status or "unknown"
            lines.append(f"  - [{status}] {summary}")
    return "\n".join(lines)


async def _call_timeline_llm(prompt: str) -> dict:
    """Call Claude Haiku for timeline generation."""
    from agent_kitchen.llm import call_haiku_structured

    return await call_haiku_structured(prompt, TIMELINE_SCHEMA)


def _group_name(group: TimelineGroup) -> str:
    """Get a display name for a group."""
    if isinstance(group, RepoGroup):
        return group.repo_name
    return group.cwd.split("/")[-1] or group.cwd


def _group_cache_key(group: TimelineGroup) -> str:
    """Get a cache key for a group's timeline."""
    if isinstance(group, RepoGroup):
        return f"timeline:{group.repo_root}"
    return f"timeline:{group.cwd}"


async def generate_group_timeline(group: TimelineGroup) -> list[TimelinePhase]:
    """Generate a timeline for a group using the LLM."""
    sessions = group.sessions
    if not sessions:
        return []

    # Single-day groups don't need LLM
    days = _sessions_by_day(sessions)
    if len(days) <= 1:
        return fallback_timeline(sessions)

    formatted = _format_sessions_for_prompt(sessions)
    prompt = TIMELINE_PROMPT_TEMPLATE.format(
        repo_name=_group_name(group),
        formatted_sessions=formatted,
    )

    try:
        data = await _call_timeline_llm(prompt)
    except Exception:
        logger.warning("Timeline LLM call failed for %s", group.repo_name, exc_info=True)
        return fallback_timeline(sessions)

    phases_data = data.get("phases", [])
    if not phases_data:
        return fallback_timeline(sessions)

    # Convert LLM output to TimelinePhase objects
    valid_statuses = {"done", "in progress", "mixed"}
    phases = []
    for p in phases_data[:5]:
        desc = str(p.get("description", ""))
        if len(desc) > 80:
            desc = desc[:77] + "..."
        status = str(p.get("status", "mixed"))
        if status not in valid_statuses:
            status = "mixed"
        # Estimate session_count from matching days (approximate)
        phases.append(
            TimelinePhase(
                period=str(p.get("period", "")),
                description=desc,
                session_count=0,  # LLM doesn't return this; set below
                status=status,
            )
        )

    # Distribute session counts: total sessions / phases (rough)
    total = len(sessions)
    if phases:
        per_phase = total // len(phases)
        remainder = total % len(phases)
        for i, phase in enumerate(phases):
            phase.session_count = per_phase + (1 if i < remainder else 0)

    return phases


async def batch_generate_timelines(
    groups: list[TimelineGroup],
    cache,
    concurrency: int = SUMMARY_CONCURRENCY,
) -> None:
    """Generate timelines for all groups, using cache where possible.

    Mutates each group's timeline field in place.
    """
    if not groups:
        return

    semaphore = asyncio.Semaphore(concurrency)

    async def _generate_one(group: TimelineGroup) -> None:
        cache_key = _group_cache_key(group)
        max_mtime = max(s.file_mtime for s in group.sessions) if group.sessions else 0.0

        if not cache.needs_refresh(cache_key, max_mtime):
            cached = cache.get(cache_key)
            if cached and cached.get("status") == "timeline":
                try:
                    phases_data = json.loads(cached["summary"])
                    group.timeline = [TimelinePhase(**p) for p in phases_data]
                    return
                except (json.JSONDecodeError, TypeError, KeyError):
                    pass

        async with semaphore:
            group.timeline = await generate_group_timeline(group)

        # Cache the result
        phases_json = json.dumps(
            [
                {
                    "period": p.period,
                    "description": p.description,
                    "session_count": p.session_count,
                    "status": p.status,
                }
                for p in group.timeline
            ]
        )
        cache.set(cache_key, phases_json, "timeline", max_mtime)

    tasks = [_generate_one(group) for group in groups]
    await asyncio.gather(*tasks)
    cache.save()


def apply_cached_timelines(groups: list[TimelineGroup], cache) -> None:
    """Apply cached timelines to groups, or generate fallbacks."""
    for group in groups:
        cache_key = _group_cache_key(group)
        max_mtime = max(s.file_mtime for s in group.sessions) if group.sessions else 0.0

        if not cache.needs_refresh(cache_key, max_mtime):
            cached = cache.get(cache_key)
            if cached and cached.get("status") == "timeline":
                try:
                    phases_data = json.loads(cached["summary"])
                    group.timeline = [TimelinePhase(**p) for p in phases_data]
                    continue
                except (json.JSONDecodeError, TypeError, KeyError):
                    pass

        # No cache hit — use fallback
        group.timeline = fallback_timeline(group.sessions)
