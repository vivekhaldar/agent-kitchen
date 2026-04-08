# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

```bash
# Run directly (no install)
uvx agent-kitchen web

# Development install
uv pip install -e ".[dev]"

# Run tests
uv run pytest                                          # all tests (Python)
uv run pytest tests/test_scanner.py                    # single file
uv run pytest tests/test_scanner.py::test_decode_simple # single test
uv run pytest -v -s                                    # verbose with stdout
node --test tests/test_chat.mjs                        # frontend JS tests

# Lint and format
uvx ruff check --fix .
uvx ruff format .

# Pre-commit hooks (ruff check, ruff format, pytest — all must pass)
pre-commit run --all-files
```

## Architecture

Agent Kitchen is a local web dashboard that unifies AI coding agent sessions from Claude Code and Codex CLI. It's a processing pipeline:

```
JSONL files (~/.claude, ~/.codex)
  → Scanner (parse sessions)
  → Git Status (annotate repos)
  → Cache (reuse prior summaries)
  → LLM Summarizer (Claude Haiku via Agent SDK)
  → Grouping (by repo, sorted by recency)
  → FastAPI Server (JSON API + static frontend)
```

### Key modules (src/agent_kitchen/)

- **scanner.py** — Reads `~/.claude/projects/` and `~/.codex/sessions/` JSONL files into `Session` objects. Each source is scanned independently (one failure doesn't block the other). Filters out programmatic SDK sessions (≤2 user turns) and subagent sessions (`subagents/` dirs).
- **summarizer.py** — Extracts compact context from JSONL (first message + last 5 messages, tool_use stripped, ~2000 tokens), then calls Claude Haiku for structured output `{summary, status}`. Falls back to first user message on LLM failure.
- **cache.py** — Disk cache at `~/.cache/agent-kitchen/summaries.json`. Invalidation by file mtime (not TTL). Atomic writes via temp file + rename. Merges on-disk state before writing to handle concurrent access.
- **git_status.py** — Subprocess calls to git for branch, dirty, unpushed, untracked. Repo root lookups are cached; status is always live.
- **grouping.py** — Pure function: partitions sessions into `RepoGroup` (with git status) and `NonRepoGroup` (by cwd), sorted by most recent activity.
- **server.py** — FastAPI app. Orchestrates the pipeline via `_scan_and_group()` (fast, no LLM) and `run_scan_pipeline()` (full). Dashboard state (`_dashboard_data`) is swapped atomically, never mutated. Includes WebSocket endpoints for both PTY terminal and ACP chat sessions.
- **acp_bridge.py** — Spawns coding agents via the Agent Client Protocol (ACP). Manages agent subprocess lifecycle and relays streaming updates (text, tool calls, status) to a callback.
- **config.py** — Constants with env var overrides. `setup_auth()` checks `ANTHROPIC_API_KEY`, then `CLAUDE_CODE_OAUTH_TOKEN`, then `pass` password manager.
- **indexer.py** — Standalone pre-indexer for batch LLM summarization with progress logging.
- **cli.py** — Subcommands: `web` (dashboard) and `index` (pre-summarize).

### Frontend (src/agent_kitchen/static/)

Vanilla HTML/JS/CSS, no build step.

- **app.js** — Dashboard client. Fetches `/api/sessions`, renders repo groups as elevated cards with collapsible headers, status pills, metadata with dot separators, fuzzy search (`/` key), time segment filter, and keyboard navigation (`j`/`k`/`Enter`).
- **chat.js** — Rich chat panel. Opens ACP agent conversations via WebSocket, renders markdown with syntax highlighting (marked + highlight.js), collapsible tool call cards with status-colored borders, turn-by-turn sidebar navigation (Ctrl+↑/↓), image paste support, token usage display, and session lifecycle management (death detection, restart).
- **style.css** — Monochrome theme with orange accent. Card-based layout, rounded status pills, command-palette search overlay with backdrop blur, polished dark mode. Sticky header with favicon.
- **index.html** — Shell with panels for dashboard, terminal (xterm.js), and chat.
- **favicon.svg** — Layered flame icon.

## Key Design Decisions

- **Independent error domains**: Claude and Codex scanners are in separate try/except blocks. One source failing never blocks the other.
- **Lazy summarization**: Dashboard shows fallback summaries (first user message) instantly on startup, then upgrades to LLM summaries in the background.
- **Atomic state swaps**: `_dashboard_data` is replaced entirely on each scan cycle, never mutated in place.
- **Concurrency control**: LLM calls use `asyncio.Semaphore(3)` to avoid API rate limits.
- **CLAUDECODE env var**: The summarizer unsets `CLAUDECODE` before Agent SDK calls to allow running inside a Claude Code session without nested-session errors.

## Gotchas

- Claude project directory names encode paths with `-` as separator (e.g., `-Users-jane-repos-foo` → `/Users/jane/repos/foo`). This breaks for paths with actual hyphens — the `cwd` field in JSONL records is authoritative.
- Codex sessions get a free summary from `session_index.jsonl` (thread_name), so many skip LLM calls entirely.
- The `--summarize` flag on `agent-kitchen web` is **off by default** — without it, only cached/fallback summaries are shown.
- Terminal launch is macOS-only (AppleScript for Terminal.app, `open -na` for Ghostty).
- ACP stdio buffer is set to 10MB to handle large agent responses; the server truncates tool call content before relaying over WebSocket to keep payloads manageable.
- The `CLAUDECODE` env var must be unset before spawning ACP agent subprocesses to avoid nested-session errors when running inside Claude Code.

## Testing Conventions

- Test fixtures are real JSONL files in `tests/fixtures/`.
- LLM calls are mocked — no real API calls in tests.
- Async tests use `@pytest.mark.asyncio`.
- Frontend JS tests use Node's built-in test runner with jsdom (`node --test tests/test_chat.mjs`).
- Pre-commit hooks run ruff check, ruff format, pytest, and JS tests — all must pass.
- All source files start with `# ABOUTME:` comments (two lines describing the file's purpose).
