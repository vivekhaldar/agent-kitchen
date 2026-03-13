# Agent Kitchen

**A unified dashboard for all your AI coding agent sessions.**

If you use [Claude Code](https://docs.anthropic.com/en/docs/claude-code) or [Codex CLI](https://github.com/openai/codex), you've probably lost track of what's happening across your sessions. Which repos have active work? What was that session doing? Where did you leave off?

Agent Kitchen scans your local session files, groups them by git repo, and gives you a single view of everything — with LLM-generated summaries, live git status, and one-click resume.

## Features

- **Unified view** — Claude Code and Codex CLI sessions in one dashboard, grouped by git repo
- **LLM summaries** — Claude Haiku generates one-line summaries and classifies each session's status (done, in progress, waiting for input)
- **Live git status** — see the current branch, dirty files, and unpushed commits per repo
- **One-click resume** — click any session to resume it in a terminal window
- **Fuzzy search** — press `/` to search across all sessions
- **Browser terminal** — resume sessions directly in a browser-based terminal (xterm.js)
- **Fast startup** — shows cached/fallback summaries instantly, upgrades to LLM summaries in the background
- **No build step** — vanilla HTML/JS/CSS frontend, zero npm dependencies

## Quick Start

```bash
# Run directly — no install needed
uvx agent-kitchen web

# Or install it
uv pip install agent-kitchen
agent-kitchen web
```

The dashboard opens at `http://localhost:8099`.

## Usage

```bash
# Start the dashboard (opens browser automatically)
agent-kitchen web

# Custom port
agent-kitchen web --port 9000

# Scan further back in history
agent-kitchen web --scan-days 90

# Don't auto-open the browser
agent-kitchen web --no-open

# Enable background LLM summarization
agent-kitchen web --summarize
```

### Pre-indexing summaries

By default, the dashboard shows fallback summaries (the first user message). To get LLM-generated summaries, either pass `--summarize` to the web command, or pre-index with:

```bash
# Index all sessions from the last 60 days
agent-kitchen index

# See what would be indexed without making LLM calls
agent-kitchen index --dry-run

# Re-index everything, ignoring cache
agent-kitchen index --force

# Control LLM concurrency (default: 3)
agent-kitchen index --concurrency 5
```

Summaries are cached at `~/.cache/agent-kitchen/summaries.json` and shared between the indexer and the dashboard.

## Authentication (for LLM summaries)

LLM-powered summaries require a Claude API credential. Agent Kitchen checks for credentials in this order:

1. `ANTHROPIC_API_KEY` environment variable — standard Anthropic API key
2. `CLAUDE_CODE_OAUTH_TOKEN` environment variable — Claude Max subscription token
3. `pass` password manager at `dev/CLAUDE_SUBSCRIPTION_TOKEN` — fallback for `pass` users

```bash
# Option 1: API key
export ANTHROPIC_API_KEY=sk-ant-...
agent-kitchen web --summarize

# Option 2: Max subscription token
export CLAUDE_CODE_OAUTH_TOKEN=...
agent-kitchen web --summarize
```

If no credentials are found, the dashboard still works — you just won't get LLM-generated summaries.

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `AGENT_KITCHEN_PORT` | `8099` | Server port |
| `AGENT_KITCHEN_SCAN_DAYS` | `60` | Days of history to scan |
| `AGENT_KITCHEN_REFRESH_INTERVAL` | `60` | Background rescan interval (seconds) |
| `AGENT_KITCHEN_TERMINAL` | `ghostty` | Terminal app for session launch (`ghostty` or `terminal`) |

## Session Filtering

Only interactive sessions are shown. The scanner filters out:

- **Programmatic SDK sessions** — single-shot calls with ≤1 user turn (e.g., automated summarization pipelines)
- **Subagent sessions** — child sessions spawned by the Agent tool, stored in `subagents/` subdirectories

See [docs/session-formats.md](docs/session-formats.md) for details on session file formats.

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

## Requirements

- Python 3.12+
- macOS (terminal launch uses AppleScript; the dashboard itself works anywhere)
- `~/.claude` and/or `~/.codex` directories with session data

## License

Apache 2.0 — see [LICENSE](LICENSE).
