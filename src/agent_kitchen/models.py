# ABOUTME: Data model definitions for agent sessions and repo groupings.
# ABOUTME: Core dataclasses used across the scanner, summarizer, and server.

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Session:
    """A single AI coding agent session parsed from a JSONL file."""

    id: str
    source: str  # "claude" or "codex"
    cwd: str
    repo_root: str | None
    repo_name: str | None
    git_branch: str | None
    started_at: datetime
    last_active: datetime
    slug: str | None
    summary: str
    status: str  # "done", "likely done", "in progress", "likely in progress", "waiting for input"
    turn_count: int
    file_path: str
    file_mtime: float


@dataclass
class TimelinePhase:
    """A phase of work in a repo's history, spanning one or more days."""

    period: str  # "Today", "Mar 13-14", "Mar 10-12"
    description: str  # max 80 chars
    session_count: int
    status: str  # "done", "in progress", "mixed"


@dataclass
class RepoGroup:
    """Sessions grouped by git repository, with live git status."""

    repo_root: str
    repo_name: str
    git_branch: str | None
    git_dirty: bool
    unpushed_commits: int
    sessions: list[Session] = field(default_factory=list)
    last_active: datetime = field(default_factory=lambda: datetime.min)
    timeline: list[TimelinePhase] = field(default_factory=list)


@dataclass
class NonRepoGroup:
    """Sessions not inside any git repo, grouped by working directory."""

    cwd: str
    sessions: list[Session] = field(default_factory=list)
    last_active: datetime = field(default_factory=lambda: datetime.min)
    timeline: list[TimelinePhase] = field(default_factory=list)
