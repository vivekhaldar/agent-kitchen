# Agent Kitchen

A locally-running web dashboard that gives you a unified view of all your AI coding agent sessions across Claude Code and Codex CLI.

## What it does

- Scans `~/.claude` and `~/.codex` for interactive session data (filters out programmatic SDK calls)
- Uses Claude Haiku to generate one-line summaries and classify session status
- Groups sessions by git repo, sorted by most recent activity
- Shows live git status (branch, dirty files, unpushed commits) per repo
- Click any session to resume it in a new terminal window

## Install

```bash
uvx agent-kitchen
```

Or install from source:

```bash
git clone https://github.com/haldar/agent-kitchen.git
cd agent-kitchen
uv pip install -e .
agent-kitchen
```

## Usage

```bash
# Start the dashboard (opens browser automatically)
agent-kitchen

# Custom port
agent-kitchen --port 9000

# Scan further back in history
agent-kitchen --scan-days 90

# Don't auto-open the browser
agent-kitchen --no-open

# Use cached summaries only (no LLM calls, no background refresh)
agent-kitchen --no-summarize
```

The dashboard runs at `http://localhost:8099` by default.

## Indexing sessions

The dashboard shows fallback summaries (first user message) on startup, then upgrades to LLM-generated summaries in the background. For faster startup with pre-computed summaries, run the indexer first:

```bash
# Index all sessions from the last 60 days (default)
agent-kitchen-index

# Index a specific time range
agent-kitchen-index --scan-days 30

# See what would be indexed without making LLM calls
agent-kitchen-index --dry-run

# Re-index everything, ignoring cache
agent-kitchen-index --force

# Control LLM concurrency (default: 3)
agent-kitchen-index --concurrency 5
```

The indexer logs progress to stderr so you can watch it work. Summaries are cached at `~/.cache/agent-kitchen/summaries.json` and shared with the dashboard.

## Authentication

The summarizer uses Claude Haiku via the Claude Agent SDK, authenticated through a Max subscription. The token is retrieved from the `pass` password manager:

```bash
pass dev/CLAUDE_SUBSCRIPTION_TOKEN
```

If the token isn't available, the dashboard still works — sessions are displayed without LLM-generated summaries.

## Configuration

Environment variable overrides:

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_KITCHEN_PORT` | `8099` | Server port |
| `AGENT_KITCHEN_SCAN_DAYS` | `60` | Days of history to scan |
| `AGENT_KITCHEN_REFRESH_INTERVAL` | `60` | Background rescan interval (seconds) |

## Development

```bash
git clone https://github.com/haldar/agent-kitchen.git
cd agent-kitchen
uv pip install -e ".[dev]"

# Run tests
uv run pytest

# Lint and format
uvx ruff check --fix .
uvx ruff format .
```

## Session filtering

Only interactive sessions are shown. The scanner filters out:

- **Programmatic SDK sessions** — single-shot calls with ≤1 user turn (e.g., email classification, automated summarization). These are created by the Claude Agent SDK but aren't interactive coding sessions.
- **Subagent sessions** — child sessions spawned by the Agent tool, stored in `subagents/` subdirectories.

Context compaction (when a session's context window fills up) does NOT create a new session — the same file continues to be used. However, `claude --continue` / `claude --resume` creates a new independent session file with no linking metadata.

See [docs/session-formats.md](docs/session-formats.md) for details on session file formats and filtering logic.

## Requirements

- Python 3.12+
- macOS (session launch uses Terminal.app via AppleScript)
- `pass` password manager with `dev/CLAUDE_SUBSCRIPTION_TOKEN` entry (for LLM summaries)
