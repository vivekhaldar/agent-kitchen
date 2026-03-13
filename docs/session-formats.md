# Session File Formats and Filtering

## Claude Code Sessions

### File locations

```
~/.claude/projects/
├── -Users-jane-repos-gh-foo/        # Encoded working directory
│   ├── a8356e6b-....jsonl             # Interactive session (UUID filename)
│   ├── 442d11c1-....jsonl
│   └── a1af50ee-.../                  # Parent session directory
│       └── subagents/
│           ├── agent-ab1a7922c....jsonl      # Spawned subagent
│           └── agent-acompact-2985....jsonl  # Compaction sidechain
├── -Users-jane-MAIL/
│   └── ...
```

### JSONL record types

Each session file contains one JSON object per line:

| type | Purpose | Key fields |
|------|---------|------------|
| `user` | User message | `message.content`, `timestamp`, `sessionId`, `cwd`, `gitBranch`, `slug`, `permissionMode`, `isSidechain`, `agentId` |
| `assistant` | Agent response | `message.content[]` (text + tool_use blocks), `timestamp` |
| `system` | System records (hooks, turn duration) | `subtype` |
| `progress` | Tool progress events | (ignored) |
| `file-history-snapshot` | File backup metadata | (ignored) |
| `queue-operation` | Internal queue ops | (ignored) |
| `last-prompt` | Cached prompt | (ignored) |

### Interactive vs programmatic sessions

Claude Code session files are created by both the interactive CLI and the Claude Agent SDK (used programmatically). Agent Kitchen only shows interactive sessions.

**How to distinguish them:**

| Signal | Interactive (CLI) | Programmatic (SDK) |
|--------|------------------|-------------------|
| User turn count | 2+ user messages | Exactly 1 user message |
| `permissionMode` field | Often present (`default`, `plan`, `bypassPermissions`) | Never present |
| Typical turn count | 4+ total turns (multi-turn conversation) | 2-3 total turns (request + response) |
| Session content | Human-written prompts, follow-ups | Templated prompts (e.g., "Classify this email...") |

**Filtering rule:** Sessions with ≤1 user turn are filtered out. This removes SDK single-shot calls (email classification, summarization, etc.) while keeping all interactive sessions. A session where the user typed one message and never followed up is also filtered — these are low-value for the dashboard.

### Subagent sessions

When Claude Code spawns subagents (via the Agent tool), each subagent gets its own JSONL file stored in:

```
{parent-session-id}/subagents/agent-{agent-id}.jsonl
```

These have `"isSidechain": true` and a non-null `"agentId"` in their user records. They are skipped by the scanner since `project_dir.glob("*.jsonl")` only matches files directly under the project directory.

### Context compaction

When a session's context window fills up, Claude Code compacts the conversation history. This creates a sidechain file:

```
{session-id}/subagents/agent-acompact-{id}.jsonl
```

Key points:
- The main session file keeps the same sessionId — compaction does NOT create a new session.
- The sidechain file contains the compacted history and has `"isSidechain": true`.
- The main session file continues to be appended to after compaction.
- From the dashboard's perspective, compaction is invisible — the session appears as one continuous session.

### Session continuation (`--continue` / `--resume`)

When a user resumes a previous session with `claude --continue` or `claude --resume`, Claude Code creates an **entirely new session file** with a new sessionId and a new slug. There is no metadata linking the new session to the old one — they are independent from the file system's perspective.

This means a logical "project session" that spans multiple continuations appears as separate sessions in the dashboard. There is currently no way to automatically link them. Potential heuristic signals (not implemented):
- Same `cwd` + sessions close in time
- The continued session's first message may contain a compaction summary referencing the previous session
- Sequential timestamps with no overlap

## Codex CLI Sessions

### File locations

```
~/.codex/sessions/
├── 2026/
│   ├── 01/
│   │   ├── 15/
│   │   │   └── rollout-2026-01-15T10-30-00-{ulid}.jsonl
│   │   └── 16/
│   │       └── ...
│   └── ...
└── session_index.jsonl    # Pre-computed metadata (thread names)
```

### JSONL record types

| type | Purpose | Key fields |
|------|---------|------------|
| `session_meta` | Session initialization (1 per file) | `payload.cwd`, `payload.git.branch`, `payload.git.repository_url`, `payload.timestamp` |
| `turn_context` | Execution context (1 per file) | `payload.model`, `payload.cwd` |
| `event_msg` | Messages and events | `payload.type` (`user_message`, `agent_message`, `task_complete`), `payload.message` |
| `response_item` | Model responses | `payload.type` (`reasoning`, `message`), `payload.content` (may be encrypted) |

### Session index

`~/.codex/session_index.jsonl` contains one record per session:
```json
{"id": "<ulid>", "thread_name": "Explain capabilities", "updated_at": "2026-03-05T22:22:23Z"}
```

The `thread_name` is used as the session summary, avoiding an LLM call.

## Summary Cache

LLM-generated summaries are cached at `~/.cache/agent-kitchen/summaries.json` to avoid redundant API calls.

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

Cache invalidation: a session's summary is regenerated when the session file's mtime is newer than the cached `file_mtime`. The cache is shared between the dashboard server and the `agent-kitchen-index` CLI.
