# Agent Kitchen

A locally-running web dashboard that gives you a unified view of all your AI coding agent sessions across Claude Code and Codex CLI.

## What it does

- Scans `~/.claude` and `~/.codex` for session data
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
```

The dashboard runs at `http://localhost:8099` by default.

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

## Requirements

- Python 3.12+
- macOS (session launch uses Terminal.app via AppleScript)
- `pass` password manager with `dev/CLAUDE_SUBSCRIPTION_TOKEN` entry (for LLM summaries)
