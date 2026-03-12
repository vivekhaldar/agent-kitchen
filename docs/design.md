# Agent Kitchen — Design Document

## Problem

When running multiple AI coding agents (Claude Code, Codex CLI) on a local machine, there is no unified way to understand what's happening across all sessions. The current workflow — switching between terminal tabs and re-reading conversation history — doesn't scale.

Specific pain points:
- **No global view**: No way to see all sessions at a glance with their status.
- **Context recovery is expensive**: Resuming work in a session requires re-reading the conversation to remember where you left off.
- **No project-level view**: Sessions in the same repo are scattered, making it hard to see the evolution of work on a project.

## Solution

A locally-running web dashboard that:
1. Scans `~/.claude` and `~/.codex` for session data.
2. Uses an LLM (Claude Haiku) to generate one-line summaries and classify session status.
3. Presents sessions grouped by git repo, with the most recently active repos and sessions first.
4. Allows clicking a session to resume it in a terminal window.

## Non-Goals

- Not a terminal emulator — clicking a session launches it in your real terminal.
- No real-time process monitoring — status is inferred from session text, not process state.
- No notifications or daemon — the server runs when you want it and refreshes periodically.
- No multi-user or auth — this is a local tool.

---

## Data Sources

### Claude Code (`~/.claude/projects/`)

**Directory structure:**
```
~/.claude/projects/
├── -Users-haldar/                              # URL-encoded working directory
│   ├── a8356e6b-....jsonl                      # Session file (UUID name)
│   ├── 442d11c1-....jsonl
│   └── a1af50ee-.../subagents/agent-xxx.jsonl  # Subagent sessions (ignore these)
├── -Users-haldar-repos-gh-skillrunner/
│   └── ...
```

**JSONL record types** (one JSON object per line):

| type | Purpose | Key fields |
|------|---------|------------|
| `user` | User message | `message.content[].text`, `timestamp`, `sessionId`, `cwd`, `gitBranch`, `slug` |
| `assistant` | Agent response | `message.content[]` (text + tool_use blocks), `timestamp`, `sessionId` |
| `file-history-snapshot` | File backup metadata | (ignore) |
| `progress` | Progress event | (ignore) |

**Key fields for our purposes:**
- `sessionId` — UUID, same across all messages in one session.
- `timestamp` — ISO 8601, on every record.
- `cwd` — Working directory, on every user/assistant record.
- `gitBranch` — Git branch name, on user records.
- `slug` — Human-readable session name (e.g., "lively-herding-sonnet"), on user records.
- `message.content` — Array of content blocks. Text blocks have `type: "text"` and `text` field. Tool use blocks have `type: "tool_use"`.

**How to identify a session:**
- The filename (UUID) IS the session ID.
- All records in the file share the same `sessionId`.
- The `slug` field provides a human-readable name.

**How to map to a project directory:**
- The parent directory name is a URL-encoded path. Decode it: `-Users-haldar-repos-gh-foo` → `/Users/haldar/repos/gh/foo`.
- Also available in the `cwd` field of each record.

**How to get timestamps:**
- Session start: `timestamp` of the first record in the file.
- Last activity: `timestamp` of the last record in the file.
- Shortcut: Use file modification time (`os.path.getmtime()`) as a proxy for last activity, and file creation time or first-line parse for start time.

**Resuming a session:**
```bash
claude --continue --session-id <uuid>
```
Run this in a new terminal window/tab.

### Codex CLI (`~/.codex/sessions/`)

**Directory structure:**
```
~/.codex/sessions/
├── 2026/
│   ├── 01/
│   │   ├── 15/
│   │   │   ├── rollout-2026-01-15T10-30-00-<ulid>.jsonl
│   │   │   └── rollout-2026-01-15T14-22-33-<ulid>.jsonl
│   │   └── 16/
│   │       └── ...
│   ├── 02/
│   └── 03/
```

**File naming:** `rollout-{YYYY-MM-DD}T{HH-MM-SS}-{ULID}.jsonl`

The ULID in the filename IS the session ID.

**JSONL record types:**

| type | Purpose | Key fields |
|------|---------|------------|
| `session_meta` | Session initialization (1 per file) | `payload.id`, `payload.cwd`, `payload.git.branch`, `payload.git.repository_url`, `payload.git.commit_hash`, `payload.timestamp` |
| `turn_context` | Execution context (1 per file) | `payload.model`, `payload.cwd` |
| `event_msg` | Messages and events (many per file) | `payload.type` ("user_message", "agent_message", "task_complete", etc.), `payload.message` |
| `response_item` | Model responses (many per file) | `payload.type` ("reasoning", "message"), `payload.content` (may be encrypted — skip encrypted content) |

**Key fields for our purposes:**
- `payload.id` in `session_meta` — Session UUID.
- `payload.cwd` in `session_meta` — Working directory.
- `payload.git` in `session_meta` — Branch, commit hash, repo URL.
- `payload.message` in `event_msg` where `payload.type == "user_message"` — User's input text.
- `payload.message` in `event_msg` where `payload.type == "agent_message"` — Agent's response text.
- `timestamp` on every record — ISO 8601.

**Session index shortcut:**
`~/.codex/session_index.jsonl` contains pre-computed metadata:
```json
{"id":"<ulid>","thread_name":"Explain capabilities","updated_at":"2026-03-05T22:22:23Z"}
```
Use `thread_name` as a free summary when available (avoids an LLM call).

**How to get timestamps:**
- Session start: `payload.timestamp` in `session_meta`, or parse from filename.
- Last activity: `timestamp` of the last record, or `updated_at` from session index.

**Resuming a session:**
```bash
codex resume <session-id>
```
`resume` is a subcommand (not a flag). It accepts either a UUID or a thread name.
It also supports `-C <dir>` to set the working directory.

---

## Data Model

```python
@dataclass
class Session:
    id: str                    # Session UUID (filename for Claude, ULID for Codex)
    source: str                # "claude" or "codex"
    cwd: str                   # Working directory where session ran
    repo_root: str | None      # Git repo root (from `git rev-parse --show-toplevel`)
    repo_name: str | None      # Short name (last path component of repo_root)
    git_branch: str | None     # Branch at session time
    started_at: datetime       # When session began
    last_active: datetime      # Most recent message timestamp
    slug: str | None           # Human-readable name (Claude slug or Codex thread_name)
    summary: str               # LLM-generated one-line summary
    status: str                # One of: "done", "likely done", "in progress",
                               #         "likely in progress", "waiting for input"
    turn_count: int            # Number of user+assistant turns
    file_path: str             # Absolute path to the JSONL file
    file_mtime: float          # File modification time (for cache invalidation)

@dataclass
class RepoGroup:
    repo_root: str             # Absolute path to git repo root
    repo_name: str             # Short display name
    git_branch: str | None     # Current branch (live check)
    git_dirty: bool            # Has uncommitted changes (live check)
    unpushed_commits: int      # Commits ahead of remote (live check)
    sessions: list[Session]    # Sessions in this repo, sorted by last_active desc
    last_active: datetime      # Most recent session's last_active (for sorting repos)

@dataclass
class NonRepoGroup:
    """Sessions not inside any git repo, grouped by cwd."""
    cwd: str
    sessions: list[Session]
    last_active: datetime
```

---

## Architecture

```
agent-kitchen/
├── pyproject.toml             # Project config, dependencies
├── src/
│   └── agent_kitchen/
│       ├── __init__.py
│       ├── server.py          # FastAPI app, serves API + static files
│       ├── scanner.py         # Reads ~/.claude and ~/.codex, yields Session objects
│       ├── summarizer.py      # LLM calls to Haiku for summary + status
│       ├── cache.py           # Disk-based JSON cache with mtime invalidation
│       ├── git_status.py      # Git status checks per repo
│       ├── models.py          # Session, RepoGroup, NonRepoGroup dataclasses
│       └── config.py          # Settings (scan window, cache path, refresh interval)
├── static/
│   ├── index.html             # Single-page dashboard
│   ├── style.css
│   └── app.js                 # Fetch API data, render UI
├── docs/
│   └── design.md              # This file
└── tests/
    ├── test_scanner.py
    ├── test_summarizer.py
    └── test_cache.py
```

### Dependencies

```toml
[project]
dependencies = [
    "fastapi",
    "uvicorn",
    "claude-agent-sdk",        # For LLM calls via Max subscription
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "pre-commit", "ruff"]
```

### Authentication

The summarizer uses the Claude Agent SDK authenticated via **Max subscription** (not an API key).

The subscription token is stored in the `pass` password manager:
```bash
pass dev/CLAUDE_SUBSCRIPTION_TOKEN
```

At startup, the app retrieves the token by running `pass dev/CLAUDE_SUBSCRIPTION_TOKEN` and sets it as `CLAUDE_CODE_OAUTH_TOKEN` in the process environment before initializing the Agent SDK. This is handled in `config.py`:

```python
import subprocess

def get_claude_token() -> str:
    result = subprocess.run(
        ["pass", "dev/CLAUDE_SUBSCRIPTION_TOKEN"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Failed to retrieve Claude token from pass. "
            "Ensure `pass dev/CLAUDE_SUBSCRIPTION_TOKEN` is set."
        )
    return result.stdout.strip()
```

**Important**: If `ANTHROPIC_API_KEY` is also set in the environment, the SDK will use that (and bill the API account) instead of the Max subscription. The app should unset `ANTHROPIC_API_KEY` from the process environment at startup to avoid this.

At startup, verify auth is working by making a trivial Haiku call. If it fails, print a clear error message referencing the `pass` entry.

---

## Component Details

### scanner.py — Session Scanner

**Responsibility:** Walk the filesystem, read JSONL files, extract metadata, and yield `Session` objects.

**Claude Code scanning:**

```python
def scan_claude_sessions(since: datetime) -> Iterator[Session]:
    """
    Walk ~/.claude/projects/*/*.jsonl files.

    For each file:
    1. Check file mtime. Skip if older than `since`.
    2. Read the FIRST line to get: sessionId, cwd, gitBranch, slug, timestamp (started_at).
    3. Read the LAST line to get: timestamp (last_active).
    4. Count lines where type is "user" or "assistant" to get turn_count.
    5. Skip files inside subagents/ directories.
    6. Decode the parent directory name to recover the project path:
       "-Users-haldar-repos-gh-foo" → "/Users/haldar/repos/gh/foo"
       (Replace leading "-" with "/", then replace remaining "-" with "/")

    Yield a Session with source="claude", summary="" (to be filled by summarizer).
    """
```

**Path decoding detail:** The encoded directory name uses `-` as separator. To decode:
- The directory name starts with `-`, which maps to `/`.
- Each subsequent `-` maps to `/`.
- Example: `-Users-haldar-repos-gh-foo` → `/Users/haldar/repos/gh/foo`.
- Edge case: directory names with actual hyphens. Cross-reference with the `cwd` field from the first record to handle this correctly.

**Reading first/last line efficiently:**
- First line: `open(f).readline()`
- Last line: Seek to end of file, scan backwards for newline. Or use: `subprocess.run(["tail", "-1", path])`. For files under 50MB this is fine.

**Codex scanning:**

```python
def scan_codex_sessions(since: datetime) -> Iterator[Session]:
    """
    Walk ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl files.

    For each file:
    1. Check file mtime. Skip if older than `since`.
    2. Parse the filename to extract start timestamp and session ID (ULID).
    3. Read through the file looking for:
       - session_meta record: extract cwd, git info
       - event_msg records where payload.type is "user_message" or "agent_message": count turns
       - Last record's timestamp: last_active
    4. Look up the session ID in ~/.codex/session_index.jsonl for thread_name.
       (Load this file once at startup into a dict keyed by session ID.)

    Yield a Session with source="codex".
    If thread_name exists in the index, use it as the summary (skip LLM call later).
    """
```

**Optimization:** For the `since` filter, use directory structure to skip entire months/days. If scanning last 60 days from 2026-03-12, only look in `2026/01/`, `2026/02/`, `2026/03/`.

### summarizer.py — LLM Summarizer

**Responsibility:** Generate one-line summaries and classify status for sessions that need it.

**Which sessions need summarization:**
- Sessions where `summary` is empty (no cached summary, no Codex thread_name).
- Sessions where the cached summary is stale (file mtime > cache mtime).

**What to send to the LLM:**

Do NOT send the entire session. Extract the following and send as a single prompt:

```python
def extract_context_for_summary(session_file: str, source: str) -> str:
    """
    Extract a compact representation of the session for LLM analysis.

    Return a string containing:
    1. The first user message (to understand the original task).
    2. The last 5 user+assistant text messages (to understand current state).
       - Strip tool_use blocks — only include text content.
       - Truncate each message to 500 chars max.
    3. Total turn count.

    Target: under 2000 tokens total for the extracted context.
    """
```

**LLM prompt:**

```
You are analyzing a coding agent session to generate a brief summary and status.

Session context:
- Source: {claude|codex}
- Working directory: {cwd}
- Total turns: {turn_count}
- First user message: {first_message}
- Last messages:
{last_messages}

Respond in exactly this JSON format, nothing else:
{
  "summary": "<one-line summary of what this session is about, max 80 chars>",
  "status": "<one of: done, likely done, in progress, likely in progress, waiting for input>"
}

Rules for status:
- "done": The task was clearly completed. Agent confirmed completion or user acknowledged it.
- "likely done": The task appears complete but there's no explicit confirmation.
- "in progress": Work is actively ongoing, not yet complete.
- "likely in progress": Some work happened but it's unclear if more is needed.
- "waiting for input": The last assistant message asks the user a question or presents options.
```

**LLM call implementation:**

```python
from claude_agent_sdk import query, ClaudeAgentOptions

async def summarize_session(context: str) -> dict:
    """
    Call Claude Haiku via Agent SDK.
    Model: claude-haiku-4-5-20251001
    Max tokens: 200
    Parse JSON response into {"summary": str, "status": str}.
    If JSON parsing fails, retry once. If still fails, use fallback:
      summary = first 80 chars of first user message
      status = "likely in progress"
    """
```

**Batch processing:** Summarize sessions concurrently. Use `asyncio.gather` with a concurrency limit of 10 to avoid overwhelming the API. Show progress on the console during startup (e.g., "Summarizing 45 sessions...").

### cache.py — Summary Cache

**Responsibility:** Cache LLM-generated summaries to avoid redundant calls.

**Cache location:** `~/.cache/agent-kitchen/summaries.json`

**Cache structure:**
```json
{
  "version": 1,
  "entries": {
    "<session_id>": {
      "summary": "Implement retry logic for HTTP client",
      "status": "done",
      "file_mtime": 1710288000.0,
      "generated_at": "2026-03-12T10:00:00Z"
    }
  }
}
```

**Cache invalidation logic:**
```python
def needs_refresh(session: Session, cache: dict) -> bool:
    entry = cache["entries"].get(session.id)
    if entry is None:
        return True  # Not cached
    if session.file_mtime > entry["file_mtime"]:
        return True  # Session file was modified since last summary
    return False
```

**Cache operations:**
- `load_cache()` → Read from disk, return dict. If file doesn't exist, return empty cache.
- `save_cache(cache)` → Write to disk atomically (write to temp file, then rename).
- `get_summary(session_id)` → Return cached entry or None.
- `set_summary(session_id, summary, status, file_mtime)` → Update cache entry.

### git_status.py — Git Status Checker

**Responsibility:** For each unique repo root, get current git status.

**Implementation:**

```python
def get_repo_root(cwd: str) -> str | None:
    """Run `git rev-parse --show-toplevel` in cwd. Return path or None."""

def get_git_status(repo_root: str) -> dict:
    """
    Return:
    {
        "branch": str,          # Current branch name
        "dirty": bool,          # Any uncommitted changes?
        "unpushed": int,        # Number of commits ahead of remote
        "untracked": int,       # Number of untracked files
    }

    Commands:
    - Branch: git -C {repo_root} branch --show-current
    - Dirty: git -C {repo_root} status --porcelain (non-empty = dirty)
    - Unpushed: git -C {repo_root} rev-list @{upstream}..HEAD --count
      (if no upstream, set to 0)
    - Untracked: count lines starting with "??" in porcelain output
    """
```

**Caching:** Cache repo roots by `cwd` (many sessions share the same cwd → same repo). Do NOT cache git status — always check live, since it changes frequently.

### server.py — Web Server

**Responsibility:** Serve the API and static files.

**Endpoints:**

```
GET /api/sessions
    Response: {
        "repo_groups": [RepoGroup...],    # Sorted by last_active desc
        "non_repo_groups": [NonRepoGroup...],
        "last_scanned": "2026-03-12T10:00:00Z",
        "scan_duration_ms": 3400
    }

GET /api/refresh
    Triggers a rescan + re-summarize of changed sessions.
    Response: same as GET /api/sessions

GET /api/launch
    Query params: source=claude&session_id=<uuid>
    Opens a new terminal window with the resume command.
    Response: { "ok": true } or { "error": "..." }
```

**Static file serving:** Mount `static/` at `/` so `index.html` is served at the root.

**Startup flow:**
1. Print banner: "Agent Kitchen starting..."
2. Run initial scan (scanner.py).
3. Load cache, identify sessions needing summarization.
4. Run LLM summarization for stale/new sessions (print progress).
5. Save updated cache.
6. Build repo groups.
7. Check git status for each repo.
8. Start uvicorn on `localhost:8099`.
9. Print "Dashboard ready at http://localhost:8099"

**Background refresh:** Every 60 seconds (configurable), re-run the scan in the background. Only re-summarize sessions whose file mtime has changed. Update the in-memory data. The frontend polls `/api/sessions` to pick up changes.

**Launch implementation (macOS):**

```python
import subprocess

def launch_session(source: str, session_id: str, cwd: str):
    if source == "claude":
        cmd = f"cd {cwd} && claude --continue --session-id {session_id}"
    elif source == "codex":
        cmd = f"cd {cwd} && codex resume {session_id}"

    # Open in a new Terminal.app tab
    applescript = f'''
    tell application "Terminal"
        activate
        do script "{cmd}"
    end tell
    '''
    subprocess.run(["osascript", "-e", applescript])
```

### config.py — Configuration

```python
SCAN_WINDOW_DAYS = 60              # Only scan sessions from last N days
CACHE_DIR = "~/.cache/agent-kitchen"
REFRESH_INTERVAL_SECONDS = 60      # Background rescan interval
SERVER_PORT = 8099
HAIKU_MODEL = "claude-haiku-4-5-20251001"
SUMMARY_CONCURRENCY = 10           # Max concurrent LLM calls
```

These are defaults. Allow overriding via environment variables:
- `AGENT_KITCHEN_SCAN_DAYS`
- `AGENT_KITCHEN_PORT`
- `AGENT_KITCHEN_REFRESH_INTERVAL`

---

## Frontend (static/index.html + app.js + style.css)

### Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  Agent Kitchen                              Last scan: 2s ago   │
│                                             [Refresh]           │
├──────────────────────────────────────────────────────────────────┤
│  View: [● Grouped by repo]  [○ Chronological]                  │
│  Filter: [All ▼]  [claude ▼]  [codex ▼]                        │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ▼ skillrunner  (main, 2 dirty, 1 unpushed)           5m ago   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ ● Implement approval adapter    in progress   5m   claude │  │
│  │ ○ Fix test flakiness in CI      likely done   2h   codex  │  │
│  │ ○ Add retry logic to runner     done          1d   claude │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ▶ mcp-generative-ui  (feat/sse, clean)               15m ago  │
│                                                                  │
│  ▶ agent-kitchen  (main, clean)                        1h ago   │
│                                                                  │
│  ── Sessions outside git repos ──                               │
│  ▶ /Users/haldar  (3 sessions)                         2d ago   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Session Row

Each session row displays:
| Column | Source | Example |
|--------|--------|---------|
| Status dot | `status` field | ● (in progress/waiting) or ○ (done/likely done) |
| Summary | `summary` field | "Implement approval adapter" |
| Status label | `status` field | "in progress" (color-coded) |
| Time ago | `last_active` | "5m ago" |
| Source badge | `source` field | "claude" or "codex" |

### Interactions

- **Click session row** → Calls `GET /api/launch?source=claude&session_id=xxx`. Visual feedback: brief flash on the row.
- **Click repo header** → Expand/collapse session list for that repo.
- **View toggle** → Switch between grouped and flat chronological views.
- **Source filter** → Show only claude sessions, only codex, or all.
- **Refresh button** → Calls `GET /api/refresh`, shows spinner, updates data.

### Status Colors

| Status | Color | Dot |
|--------|-------|-----|
| in progress | blue | ● filled |
| likely in progress | light blue | ● filled |
| waiting for input | amber/orange | ● filled |
| done | green | ○ outline |
| likely done | light green | ○ outline |

### Auto-refresh

The frontend polls `GET /api/sessions` every 30 seconds. On each poll:
- Update the data.
- Update "Last scan: Xs ago" in the header.
- Don't re-render if data hasn't changed (compare a hash or timestamp).

### Tech Stack

- **No build step.** Vanilla HTML + CSS + JavaScript.
- Use `fetch()` for API calls.
- Use CSS Grid or Flexbox for layout.
- Use CSS custom properties for theming (dark mode by default — this is a dev tool).
- Responsive but primarily designed for desktop widths.

---

## Task Breakdown

### Phase 1: Core infrastructure (get data flowing)

**Task 1.1: Project scaffolding** [DONE]
- [x] Create `pyproject.toml` with dependencies (fastapi, uvicorn, claude-agent-sdk).
- [x] Create `src/agent_kitchen/__init__.py`.
- [x] Create `config.py` with default constants.
- [x] Create `models.py` with the Session, RepoGroup, NonRepoGroup dataclasses.
- [x] Verify the project can be installed with `uv pip install -e .`.
- [x] Set up pre-commit hooks for auto-formatting and tests:
  - Install `pre-commit` as a dev dependency.
  - Create `.pre-commit-config.yaml` with:
    - **ruff** for linting and auto-formatting (replaces black + isort + flake8). Use `ruff check --fix` and `ruff format`.
    - **ruff** configuration in `pyproject.toml`: target Python 3.12, line length 99, isort-compatible import sorting.
  - Create a `pre-commit` git hook (via `pre-commit install`) that runs:
    1. `ruff check --fix` — lint and auto-fix (import sorting, unused imports, etc.).
    2. `ruff format` — auto-format all staged Python files.
    3. `pytest` — run the full test suite. Commit is blocked if any test fails.
  - Verify the hook works: stage a deliberately mis-formatted file, run `git commit`, confirm ruff fixes it and tests run.

**Task 1.2: Claude Code scanner** [DONE]
- [x] Implement `scan_claude_sessions(since: datetime) -> list[Session]`.
- [x] Walk `~/.claude/projects/*/` for JSONL files (skip subagent directories).
- [x] Decode directory names to recover cwd (naive decode as fallback; cwd from JSONL is authoritative).
- [x] Read first line for: sessionId, cwd, gitBranch, slug, started_at.
- [x] Read last line for: last_active.
- [x] Count user + assistant type lines for turn_count.
- [x] Filter by `since` using file mtime.
- [x] Write tests using fixture JSONL files (16 tests covering parsing, filtering, edge cases).

**Task 1.3: Codex scanner** [DONE]
- [x] Implement `scan_codex_sessions(since: datetime) -> list[Session]`.
- [x] Walk `~/.codex/sessions/YYYY/MM/DD/` for JSONL files.
- [x] Parse session_meta for cwd, git info, session ID.
- [x] Parse event_msg records for turn count (user_message + agent_message).
- [x] Load `~/.codex/session_index.jsonl` once for thread_name lookup.
- [x] Use thread_name as summary when available.
- [x] Filter by `since` using file mtime.
- [x] Write tests with fixture files (23 tests covering parsing, filtering, index lookup, edge cases).

**Task 1.4: Cache layer** [DONE]
- [x] Implement `SummaryCache` class with load, save, get, set, needs_refresh methods.
- [x] Cache location: `~/.cache/agent-kitchen/summaries.json`.
- [x] Atomic writes (write to .tmp, rename).
- [x] Write tests (16 tests covering roundtrip, invalidation, corruption, atomicity).

### Phase 2: LLM integration

**Task 2.1: Context extractor** [DONE]
- [x] Implement `extract_context_for_summary(file_path, source)`.
- [x] Extract first user message + last 5 text messages.
- [x] Strip tool_use blocks.
- [x] Truncate each message to 500 chars.
- [x] Target output under 2000 tokens.
- [x] Write tests with fixture data (19 tests covering Claude, Codex, and edge cases).

**Task 2.2: Summarizer** [DONE]
- [x] Implement `summarize_session(context: str) -> SummarizeResult` using Claude Agent SDK.
- [x] Use model `claude-haiku-4-5-20251001`.
- [x] Parse JSON response for summary + status (handles markdown code blocks, extra whitespace).
- [x] Handle failures gracefully (fallback to truncated first message on invalid JSON, LLM errors).
- [x] Implement batch summarization with `asyncio.gather` and concurrency limit of 10.
- [x] Auth setup already in config.py (`setup_auth()` sets `CLAUDE_CODE_OAUTH_TOKEN`, unsets `ANTHROPIC_API_KEY`).
- [x] Write tests (22 tests: mocked LLM for unit tests covering parsing, fallbacks, batching, caching).

### Phase 3: Git status and grouping

**Task 3.1: Git status checker** [DONE]
- [x] Implement `get_repo_root(cwd)` with caching.
- [x] Implement `get_git_status(repo_root)` — branch, dirty, unpushed, untracked.
- [x] Handle non-git directories gracefully (return None for repo_root).
- [x] Write tests (16 tests covering repo root detection, caching, all git status fields, edge cases).

**Task 3.2: Grouping logic** [DONE]
- [x] Implement `group_sessions()` in `grouping.py` that takes `list[Session]` and returns `tuple[list[RepoGroup], list[NonRepoGroup]]`.
- [x] Group sessions by `repo_root` (sessions without repo_root grouped by `cwd` into NonRepoGroup).
- [x] Sort repos by most recent `last_active` descending.
- [x] Sort sessions within each repo by `last_active` descending.
- [x] Attach live git status to each RepoGroup (called once per unique repo_root).
- [x] Write tests (17 tests covering grouping, sorting, git status attachment, edge cases).

### Phase 4: Server

**Task 4.1: FastAPI app** [DONE]
- [x] Implement `GET /api/sessions` — returns grouped session data as JSON.
- [x] Implement `GET /api/refresh` — triggers rescan, returns updated data.
- [x] Implement `GET /api/launch` — opens terminal with resume command (macOS AppleScript).
- [x] Mount `static/` directory for frontend files.
- [x] Implement startup sequence: scan → cache check → summarize → group → serve.
- [x] Print progress during startup ("Scanning... Found 45 sessions. Summarizing 12 new sessions...").
- [x] Write tests (23 tests covering all endpoints, serialization, scan pipeline, and error handling).

**Task 4.2: Background refresh** [DONE]
- [x] Run rescan every 60 seconds in a background asyncio task.
- [x] Only re-summarize sessions with changed mtime.
- [x] Update in-memory session data atomically (swap reference, don't mutate in place).

**Task 4.3: CLI entry point**
- Add a `[project.scripts]` entry so `agent-kitchen` command starts the server.
- Parse CLI args: `--port`, `--scan-days`, `--no-open` (don't auto-open browser).
- On startup, open `http://localhost:8099` in the default browser (unless `--no-open`).

### Phase 5: Frontend

**Task 5.1: HTML structure**
- Build the page layout as described in the Layout section above.
- Header with title, last-scan time, refresh button.
- View toggle (grouped / chronological).
- Source filter (all / claude / codex).
- Main content area for repo groups and session rows.

**Task 5.2: JavaScript — data fetching and rendering**
- On page load, fetch `GET /api/sessions`.
- Render repo groups: collapsible headers with git status badges.
- Render session rows with all columns.
- Implement click-to-launch: call `/api/launch`, show brief visual feedback.
- Implement expand/collapse for repo groups.
- Implement view toggle (re-render as flat list or grouped).
- Implement source filter (client-side filter, no API call needed).
- Auto-refresh every 30 seconds.

**Task 5.3: CSS styling**
- Dark theme (dark background, light text — standard dev tool aesthetic).
- Color-coded status dots and labels as specified.
- Source badges: "claude" in orange, "codex" in green.
- Hover effects on session rows.
- Smooth expand/collapse animation for repo groups.
- Responsive layout (works at 800px+ width).

### Phase 6: Polish and packaging

**Task 6.1: Error handling**
- Handle missing `~/.claude` or `~/.codex` directories gracefully (skip, don't crash).
- Handle malformed JSONL lines (skip the line, log a warning).
- Handle LLM auth failures with a clear error message.
- Handle git command failures (repo deleted, not a git repo, etc.).

**Task 6.2: Packaging for distribution**
- Ensure `pyproject.toml` has proper metadata (name, version, description, author).
- Add a README.md with: what it does, how to install, how to run.
- Verify `uvx agent-kitchen` works for one-command install+run.
- Test on a clean environment (fresh venv, no prior cache).

**Task 6.3: GitHub repo setup**
- Create private repo on GitHub.
- Add `.gitignore` (Python, __pycache__, .cache, etc.).
- Push initial code.

---

## Open Questions / Future Work

These are explicitly out of scope for the initial build but worth tracking:

1. **iTerm2 support**: The launch feature uses Terminal.app. iTerm2 users would need an iTerm2-specific AppleScript variant. Detect which terminal is running and use the appropriate script.
2. **Session search**: Text search across session content. Could use grep over JSONL files without needing a database.
3. **Token usage tracking**: Both formats include token counts. Could show cost/usage per session or per repo.
4. **Linux support**: Replace AppleScript with xdg-open or similar. The rest of the stack is cross-platform.
5. **Additional agent sources**: Aider, Cursor, Windsurf — each has its own session format that could be added as new scanner modules.
