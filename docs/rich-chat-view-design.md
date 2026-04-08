# Rich Chat View for Agent Kitchen

## Context

The current Agent Kitchen dashboard uses xterm.js to embed Claude Code sessions as raw terminal output in the browser. This limits UI possibilities — no markdown rendering, no structured tool call display, no rich formatting. The goal is to replace the terminal panel with a graphical chat view (like Claude Desktop) that renders markdown beautifully, shows tool calls as collapsible cards, and streams text token-by-token.

**Key decision**: Instead of coupling to Claude Code's proprietary `--output-format stream-json`, we use the **Agent Client Protocol (ACP)** — an open standard for editor-to-agent communication created by Zed Industries. ACP is already supported by Claude Code, Codex CLI, GitHub Copilot, Gemini CLI, and others. Agent Kitchen treats ACP as the primary protocol, but must degrade gracefully when agents don't support optional ACP capabilities (e.g., `session/load`, `usage_update`).

## Architecture

```
Browser (chat.js)
  ↕ WebSocket (JSON messages)
FastAPI server (server.py)
  ↕ ACP (JSON-RPC 2.0 over stdio)
Any ACP Agent (Claude, Codex, Copilot, Gemini, ...)
```

**Before**: Persistent PTY process ↔ raw bytes over WebSocket ↔ xterm.js
**After**: ACP agent subprocess ↔ structured JSON-RPC over stdio ↔ FastAPI bridge ↔ WebSocket ↔ custom markdown renderer

The existing terminal view stays as a fallback (the chat panel and terminal panel are independent).

## Implementation Status

All phases of the original design were implemented. The sections below document the final state of the implementation, noting deviations from the original plan and additional features that emerged during development.

## Agent Client Protocol (ACP) Overview

ACP is JSON-RPC 2.0 over stdio. The server spawns an agent as a child process and communicates via stdin/stdout.

**Spec**: https://agentclientprotocol.com
**Python SDK**: `agent-client-protocol` on PyPI (`import acp`)

### Supported Agents

| Agent | ACP Command |
|---|---|
| Claude Code | `npx @agentclientprotocol/claude-agent-acp` |
| Codex CLI | `npx @zed-industries/codex-acp` |
| GitHub Copilot | `npx @github/copilot-language-server --acp` |
| Gemini CLI | `npx @google/gemini-cli --experimental-acp` |

### ACP Lifecycle

```
Client                              Agent (subprocess via stdio)
  │                                    │
  │──── initialize ───────────────────>│  (capabilities exchange)
  │<─── initialize response ──────────│
  │                                    │
  │──── session/new ──────────────────>│  (create session with cwd)
  │<─── {sessionId} ──────────────────│
  │                                    │
  │──── session/prompt ───────────────>│  (send user message)
  │<─── session/update (notification) ─│  (streaming: text chunks, tool calls, ...)
  │<─── session/update (notification) ─│
  │<─── session/update (notification) ─│
  │<─── prompt response ──────────────│  (stopReason: "end_turn")
  │                                    │
  │──── session/prompt ───────────────>│  (next turn)
  │     ...                            │
```

### ACP Wire Format

#### Initialize (handshake)

Agent Kitchen advertises only `fs` capabilities (readTextFile, writeTextFile). It does NOT advertise `terminal: true` — terminal callbacks are not implemented.

```json
// Client → Agent
{"jsonrpc":"2.0","id":0,"method":"initialize","params":{
  "protocolVersion":1,
  "clientCapabilities":{"fs":{"readTextFile":true,"writeTextFile":true}},
  "clientInfo":{"name":"agent-kitchen","title":"Agent Kitchen","version":"0.1.0"}
}}

// Agent → Client
{"jsonrpc":"2.0","id":0,"result":{
  "protocolVersion":1,
  "agentCapabilities":{"loadSession":true,"promptCapabilities":{"image":true,"embeddedContext":true}},
  "agentInfo":{"name":"claude-code","title":"Claude Code","version":"1.0.0"},
  "authMethods":[{"type":"oauth","url":"https://..."}]
}}
```

The bridge inspects the response to determine what the agent supports:
- **`agentCapabilities.loadSession`**: Whether `session/load` is available (default: false). Stored per-bridge.
- **`authMethods`**: If non-empty, the agent may require authentication before `session/new` succeeds.

#### Authentication

Agent Kitchen delegates auth to each agent's CLI (e.g., `claude auth login`, `gh auth login`). If `session/new` fails with an auth-related error, the bridge raises `AuthRequiredError`, and the frontend shows instructions with a retry button. ACP's in-band `authenticate` method is not used.

#### Session Creation

```json
// Client → Agent
{"jsonrpc":"2.0","id":1,"method":"session/new","params":{
  "cwd":"/Users/vivek/repos/gh/agent-kitchen",
  "mcpServers":[]
}}

// Agent → Client
{"jsonrpc":"2.0","id":1,"result":{"sessionId":"sess_abc123"}}
```

#### Prompt (user message)

Prompts support text and image content blocks:

```json
// Client → Agent
{"jsonrpc":"2.0","id":2,"method":"session/prompt","params":{
  "sessionId":"sess_abc123",
  "prompt":[
    {"type":"text","text":"Fix the bug in server.py"},
    {"type":"image","data":"base64...","mimeType":"image/png"}
  ]
}}

// Agent → Client (response, after all updates streamed)
{"jsonrpc":"2.0","id":2,"result":{"stopReason":"end_turn"}}
```

#### Session Update Notifications (streamed during prompt)

All updates are JSON-RPC notifications (no `id`, no response expected):

**Text streaming:**
```json
{"jsonrpc":"2.0","method":"session/update","params":{
  "sessionId":"sess_abc123",
  "update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"The issue is in "}}
}}
```

**Thinking/reasoning:**
```json
{"jsonrpc":"2.0","method":"session/update","params":{
  "sessionId":"sess_abc123",
  "update":{"sessionUpdate":"agent_thought_chunk","content":{"type":"text","text":"Let me check the error..."}}
}}
```

**Tool call started:**
```json
{"jsonrpc":"2.0","method":"session/update","params":{
  "sessionId":"sess_abc123",
  "update":{
    "sessionUpdate":"tool_call",
    "toolCallId":"call_001",
    "title":"Reading server.py",
    "kind":"read",
    "status":"pending",
    "locations":[{"path":"/Users/vivek/repos/gh/agent-kitchen/src/agent_kitchen/server.py"}],
    "rawInput":{"path":"server.py"}
  }
}}
```

**Tool call progress/completion:**
```json
{"jsonrpc":"2.0","method":"session/update","params":{
  "sessionId":"sess_abc123",
  "update":{
    "sessionUpdate":"tool_call_update",
    "toolCallId":"call_001",
    "status":"completed",
    "content":[{"type":"content","content":{"type":"text","text":"File contents..."}}]
  }
}}
```

**Usage/cost update** (extension — not part of core ACP spec, emitted by some agent wrappers):
```json
{"jsonrpc":"2.0","method":"session/update","params":{
  "sessionId":"sess_abc123",
  "update":{"sessionUpdate":"usage_update","size":200000,"used":45000,"cost":{"amount":0.15,"currency":"USD"}}
}}
```

**Plan update:**
```json
{"jsonrpc":"2.0","method":"session/update","params":{
  "sessionId":"sess_abc123",
  "update":{"sessionUpdate":"plan","entries":[
    {"content":"Read server.py","priority":"high","status":"completed"},
    {"content":"Fix the WebSocket handler","priority":"high","status":"pending"}
  ]}
}}
```

#### Session History Replay (optional capability)

`session/load` is only available when the agent advertises `loadSession: true` in its capabilities. The bridge checks this before attempting to load.

```json
// Client → Agent (load existing session — only if loadSession capability is true)
{"jsonrpc":"2.0","id":3,"method":"session/load","params":{
  "sessionId":"sess_abc123","cwd":"/path","mcpServers":[]
}}

// Agent replays history as session/update notifications:
//   user_message_chunk, agent_message_chunk, tool_call, tool_call_update, ...
// Then responds:
{"jsonrpc":"2.0","id":3,"result":null}
```

**Fallback when `loadSession` is false or fails**: The bridge creates a new session via `session/new`. If `session/load` fails, the bridge restarts the entire agent process (to avoid transport corruption) before creating a fresh session. The chat panel shows an info banner with the scanner summary as context for what the session was about.

### ACP Update Types → Frontend Mapping

**Core ACP updates** (part of the spec, all agents should emit these):

| ACP `sessionUpdate` | Frontend Action |
|---|---|
| `agent_message_chunk` | Append text to streaming assistant bubble, re-render markdown |
| `agent_thought_chunk` | Append to collapsible "thinking" block |
| `user_message_chunk` | Render user bubble (during session/load replay) |
| `tool_call` | Create collapsible tool call card (name, kind, status, locations) |
| `tool_call_update` | Update tool card status, append output content |
| `plan` | Render plan checklist |
| `current_mode_update` | Show mode badge |
| others | Gracefully ignored with a console.log |

**Extension updates** (not in the core ACP spec — agent-specific, may not be present):

| Update | Frontend Action | Notes |
|---|---|---|
| `usage_update` | Update cost/token badge in panel header | Only some ACP wrappers emit this. The cost badge stays hidden if no usage events arrive. Context warning colors at 75% (yellow) and 90% (red). |

### ToolCall Object

```
ToolCall {
  toolCallId: string           — unique ID
  title: string                — human-readable ("Reading server.py")
  kind?: "read"|"edit"|"delete"|"move"|"search"|"execute"|"think"|"fetch"|"other"
  status?: "pending"|"in_progress"|"completed"|"failed"
  content?: [{type:"content", content:{...}} | {type:"diff", path, oldText, newText}]
  locations?: [{path: string, line?: number}]
  rawInput?: any
  rawOutput?: any
}
```

## WebSocket Protocol (`/ws/chat`)

The FastAPI server bridges between the browser WebSocket and the ACP agent subprocess.

### Client → Server

```json
// Start a new session with a specific agent (autoApprove defaults to true)
{"type": "start", "agent": "claude", "cwd": "/path/to/repo"}

// Resume an existing session (history loaded only if agent supports loadSession)
{"type": "start", "agent": "claude", "cwd": "/path", "sessionId": "sess_abc123"}

// Send a user message (text and/or images)
{"type": "user_message", "text": "Fix the bug", "images": [{"data": "base64...", "mimeType": "image/png"}]}

// Cancel the current turn
{"type": "cancel"}

// Retry after auth failure
{"type": "retry"}
```

### Server → Client

```json
// Session established
{"type": "session_init", "sessionId": "sess_abc123", "agentInfo": {"name": "claude-code", ...},
 "historyLoaded": true}

// Agent requires authentication
{"type": "auth_required", "agentName": "claude-code",
 "message": "Claude Code requires authentication."}

// Forwarded ACP update (thin wrapper — unknown sessionUpdate types are passed through)
{"type": "update", "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "..."}}
{"type": "update", "sessionUpdate": "tool_call", "toolCallId": "call_001", ...}
{"type": "update", "sessionUpdate": "tool_call_update", "toolCallId": "call_001", ...}

// Turn complete
{"type": "turn_complete", "stopReason": "end_turn"}

// Agent process died (detected by background monitor polling every 2s)
{"type": "session_terminated"}

// Session auto-restarted (when user sends a message after agent death)
{"type": "session_restarted", "sessionId": "sess_new123"}

// Error
{"type": "error", "message": "Agent process crashed"}
```

The server forwards ACP `session/update` params directly as WebSocket messages (adding `"type": "update"`). Tool call update content is truncated server-side (2000 chars max) to keep WebSocket payloads manageable.

### Session ID Mapping (Scanner ↔ ACP)

The existing dashboard discovers sessions by scanning native log files (`~/.claude/projects/`, `~/.codex/sessions/`). ACP sessions have their own IDs assigned by the agent wrapper.

These are **different ID spaces**:

- **Resuming a scanner session**: The `start` message includes the scanner `sessionId`. The bridge passes this to `session/load` (if the agent supports `loadSession`) or falls back to `session/new`. If `session/load` fails, the bridge restarts the agent process and creates a fresh session.
- **New sessions created via chat**: These get an ACP-assigned session ID. The agent writes its own log files, so the session appears in the scanner on next refresh.
- **No ID registry needed**: The chat panel holds the ACP session ID in memory for the WebSocket lifetime. The scanner remains the source of truth for the session list.

## Files Modified

| File | Change |
|---|---|
| `src/agent_kitchen/acp_bridge.py` | ACP client that spawns agents and bridges to WebSocket (~270 lines) |
| `src/agent_kitchen/server.py` | `/ws/chat` endpoint, `/api/agents` endpoint, `build_content_blocks()`, tool content truncation (~200 lines added) |
| `src/agent_kitchen/static/chat.js` | Chat UI module: tabs, WebSocket, rendering, input, turn sidebar (~1050 lines) |
| `src/agent_kitchen/static/index.html` | CDN deps (marked, highlight.js, DOMPurify), chat panel HTML, turn sidebar |
| `src/agent_kitchen/static/style.css` | Chat panel layout, bubble styles, markdown styles, tool cards, turn sidebar, agent picker (~1200 lines of changes) |
| `src/agent_kitchen/static/app.js` | Session routing to chat, agent picker dropdown, dashboard session event listeners (~380 lines of changes) |
| `src/agent_kitchen/static/favicon.svg` | Layered flame icon |
| `pyproject.toml` | `agent-client-protocol` dependency |
| `tests/test_acp_lifecycle.py` | ACP bridge unit tests (mocked) |
| `tests/test_chat.mjs` | Frontend JS unit tests (jsdom, Node test runner) |
| `tests/test_image_paste.py` | Image content block building tests |
| `tests/test_server.py` | Server endpoint tests |

## What Was Built

### Backend: ACP Bridge (`acp_bridge.py`)

**`KitchenACPClient`** — ACP client callbacks:
- `session_update()` — forwards updates to the WebSocket callback
- `request_permission()` — auto-approves (selects first option) or denies all, based on `auto_approve` flag. Uses ACP's `RequestPermissionResponse` with `{outcome: "selected", optionId}` or `{outcome: "cancelled"}`
- `read_text_file()` / `write_text_file()` — scoped to session cwd via path validation. Supports ACP slice params (line, limit) for partial reads. `write_text_file` creates parent directories as needed.

**`ACPBridge`** — manages the agent subprocess lifecycle:
- `start(session_id?)` — spawns agent, initializes ACP (10MB stdio buffer), creates or loads session. On `session/load` failure, restarts the entire agent process to avoid transport corruption.
- `prompt(content_blocks)` — sends a user message with text and/or image blocks. Auto-restarts the bridge if the agent process has died.
- `restart()` — closes the dead bridge and starts a new one, attempting to resume the session.
- `cancel()` — cancels the current turn.
- `close()` — terminates the agent process.
- `is_alive` property — checks if the subprocess is still running.

**Agent registry** — maps agent names to ACP spawn commands (claude, codex, copilot, gemini).

### Backend: Server Additions (`server.py`)

- **`/ws/chat`** — WebSocket endpoint that creates an `ACPBridge`, handles the start/retry/user_message/cancel message loop, and relays ACP updates to the browser.
- **`/api/agents`** — returns the list of available agent types for the picker UI.
- **`build_content_blocks()`** — converts WebSocket `user_message` payloads (text + images) into ACP content blocks. Skips malformed images gracefully.
- **`_truncate_tool_content()`** — caps tool_call_update text at 2000 chars before WebSocket relay.
- **Background process monitor** — polls `bridge.is_alive` every 2s and sends `session_terminated` when the agent dies.

### Frontend: Chat Module (`chat.js`)

**Tab management**: Multiple concurrent chat sessions with tab switching. Each tab holds its own WebSocket, session state, message history DOM, streaming accumulators, pending images, and message queue.

**WebSocket + message routing**: Connects to `/ws/chat`, sends `start` with agent/cwd/sessionId, routes incoming messages by type (`session_init`, `update`, `turn_complete`, `error`, `auth_required`, `session_terminated`, `session_restarted`). Update messages are further routed by `sessionUpdate` type.

**Rendering**:
- User bubbles with optional inline image thumbnails, preserved newlines via `white-space: pre-wrap`
- Streaming assistant text with markdown rendering via `marked` + `highlight.js`, sanitized through `DOMPurify`, throttled via `requestAnimationFrame`
- Collapsible thinking blocks (`<details>` elements)
- Tool call cards with kind-based icons, status badges (pending/in_progress/completed/failed), file location display, expandable body with output or diff content
- Automatic tool collapsing: runs of 3+ consecutive completed tool cards are grouped into a `<details>` summary
- Plan rendering as a checklist with status icons
- Usage/cost display in panel header with context window percentage and warning colors (75% yellow, 90% red)
- System messages, info banners, auth banners with retry buttons, session termination notices

**Input handling**:
- Auto-growing textarea (up to 150px)
- Enter to send, Shift+Enter for newline
- Esc to cancel during streaming
- Stop button appears during streaming (replaces send button)
- Message queueing: users can type and send messages while the agent is working — queued messages are flushed sequentially after each turn completes
- Image paste: clipboard images are added as pending attachments with thumbnail previews and remove buttons

**Turn navigation sidebar**:
- Lists all user turns with numbered labels and text previews
- Click to scroll to a turn with highlight animation
- Ctrl+Up/Down keyboard shortcuts for sequential navigation
- Ctrl+T to toggle sidebar visibility
- Counter shows current/total turns

**Session lifecycle**:
- Death detection via `session_terminated` message from the server monitor
- "Session ended — send a message to resume" notice
- Auto-restart on next user message (bridge restarts, sends `session_restarted`)
- Auth flow: `auth_required` banner with retry button

**Public API**:
- `window.AgentChat.openChat(session)` — open or switch to a tab for an existing session
- `window.AgentChat.openNewChat(cwd, agent)` — open a new session tab
- `window.AgentChat.getActiveSessionIds()` — set of session IDs with open tabs
- `window.AgentChat.getActiveSessions()` — info about active tabs (streaming state, agent, cwd)

**Custom events** emitted on `window`:
- `agent-session-started` — when a session initializes
- `agent-session-updated` — when streaming state changes
- `agent-session-closed` — when a tab is closed

### Frontend: Dashboard Integration (`app.js`)

- Session clicks route to `AgentChat.openChat(session)` (chat is the default view)
- "+" buttons on repo group headers show an **agent picker dropdown** listing available agents from `/api/agents`. If only one agent is available, the picker is skipped.
- Default agent is persisted in `localStorage` (last used)
- Dashboard listens for `agent-session-started/updated/closed` custom events and updates session rows with live status indicators (streaming dots)

### Frontend: UI Polish (`style.css`)

The branch includes significant visual polish beyond the chat panel:
- Card-based layout for repo groups with elevated shadows and rounded corners
- Monochrome theme with orange accent (#FF4D00)
- Status pills with colored dots and pulse animation for active sessions
- Display font (Archivo Black) for repo group headers
- Typography scale via CSS variables
- Segmented time filter buttons (replacing a slider)
- Command-palette search overlay with backdrop blur
- Dark mode via `.dark` class on `<html>`, toggled by `d` key cycling auto/dark/light
- SVG favicon (layered flame icon)
- Responsive chat panel width

## Deviations from Original Design

1. **View toggle button removed**: The design called for a TTY/Chat toggle button. This was implemented then removed (commit `57eb516`) — the chat panel and terminal panel are independent, accessible via different session interaction paths.

2. **Auto-approve UI toggle not implemented**: The design specified a lock icon toggle for switching between auto-approve and restrictive mode. The `autoApprove` flag exists in the WebSocket protocol and bridge, but there's no UI control for it. Auto-approve defaults to true.

3. **Agent picker is a positioned dropdown, not in-chat**: The design placed agent selection inside the chat panel header. The implementation uses a positioned dropdown anchored to the "+" button in repo group headers, populated from `/api/agents`.

4. **Turn navigation sidebar added**: Not in the original design. Provides numbered turn list, click-to-scroll, keyboard shortcuts (Ctrl+Up/Down/T).

5. **Image paste support added**: Not in the original design. Users can paste clipboard images into the chat input. Images appear as thumbnails in a preview strip below the messages and are sent as ACP image content blocks.

6. **Message queueing added**: Not in the original design. Users can type and send messages while the agent is working. Messages are queued and flushed after each turn completes.

7. **Tool collapsing added**: Not in the original design. Runs of 3+ consecutive completed tool cards are automatically grouped into a collapsible `<details>` summary.

8. **10MB stdio buffer**: The ACP spawn uses a 10MB stdio buffer (up from the default 64KB) to handle large file reads by agents.

9. **Session/load failure recovery**: On `session/load` failure, the bridge restarts the entire agent process to avoid transport corruption, then creates a fresh session. The original design only mentioned falling back to `session/new`.

10. **Dashboard session events**: The chat module emits custom DOM events that the dashboard listens to for live status updates (streaming indicators on session rows). Not in the original design.

## Key Design Decisions

- **ACP over proprietary formats**: ACP is supported by Claude Code, Codex, Copilot, Gemini, and others. One protocol, any agent. The Python SDK (`agent-client-protocol`) handles JSON-RPC 2.0 framing.
- **Graceful capability degradation**: ACP capabilities like `loadSession` are optional. The bridge checks what each agent supports and falls back cleanly. Unknown `sessionUpdate` types are logged and ignored.
- **Persistent agent process**: ACP keeps the agent process alive across turns. `session/prompt` is called multiple times on the same connection.
- **Scoped FS access**: `read_text_file` and `write_text_file` callbacks validate that all paths resolve under the session's `cwd`. Out-of-repo access is rejected with a `ValueError`.
- **Auto-approve by default**: Agent Kitchen is a local dashboard — denying tool calls blocks normal agent functionality. ACP permissions use `RequestPermissionResponse` with `{outcome: "selected", optionId}` or `{outcome: "cancelled"}`.
- **No terminal capability**: Agent Kitchen does not advertise `terminal: true` in the ACP handshake. This avoids ~5 terminal lifecycle callbacks but may cause agents to degrade if they depend on client-side terminal management.
- **Delegated authentication**: Auth is handled by each agent's own CLI, not via ACP's `authenticate` method.
- **Scanner remains source of truth**: ACP session IDs are ephemeral to the chat panel. The scanner's native log file parsing remains the authoritative session list.
- **Backend as thin relay**: Server forwards ACP updates to WebSocket without deep interpretation. Tool content is truncated server-side (2000 char cap) to keep payloads manageable.
- **`requestAnimationFrame` for streaming**: Markdown re-rendered at most once per frame to avoid jank during fast token streaming.
- **DOMPurify**: All rendered HTML from markdown and tool results is sanitized before DOM insertion.

## Test Coverage

- **`tests/test_acp_lifecycle.py`** (10 tests): Bridge lifecycle, start/prompt/cancel/close, auth flow, session load fallback, process death detection and restart, permission handling.
- **`tests/test_chat.mjs`** (26 tests, 8 suites): Tab management, message routing, streaming text accumulation, tool call rendering and updating, tool collapsing, user message building, turn tracking, session init handling, turn_complete finalization.
- **`tests/test_image_paste.py`** (8 tests): `build_content_blocks()` with text, images, text+images, malformed images, empty inputs.
- **`tests/test_server.py`** (54 tests): Server endpoints including the new `/api/agents` and chat WebSocket.
