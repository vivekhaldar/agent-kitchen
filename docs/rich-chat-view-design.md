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

The existing terminal view stays as a fallback (toggle between chat and TTY mode).

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
| Qwen Code | `npx @qwen-code/qwen-code --acp` |
| Augment Code | `npx @augmentcode/auggie --acp` |

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

The client advertises only the capabilities it actually implements. Agent Kitchen does NOT advertise `terminal: true` because it does not implement the terminal callbacks (`terminal/create`, `terminal/output`, `terminal/kill`, etc.).

**Limitation/risk**: ACP guarantees that agents won't *call* `terminal/*` when the client doesn't advertise the capability, but it does NOT guarantee that command execution tools remain fully functional without it. Some agents may degrade (e.g., unable to run shell commands, or running them with reduced visibility). Whether Claude Code and Codex ACP wrappers work correctly without terminal capability has **not been verified** and is a Phase 1 validation task. If agents require terminal capability for basic operation, we would need to implement the terminal callbacks — significant additional scope (~5 callbacks with process lifecycle management).

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

The bridge must inspect the response to determine what the agent supports:
- **`agentCapabilities.loadSession`**: Whether `session/load` is available (default: false). Stored per-agent.
- **`authMethods`**: If non-empty, the agent may require authentication before `session/new` succeeds.

#### Authentication

If `authMethods` is returned in the initialize response, or if `session/new` fails with an auth error, the bridge sends an `{"type": "auth_required", "agentName": "...", "message": "..."}` message to the browser. The frontend shows a prompt directing the user to authenticate the agent externally, then retries.

Agent Kitchen does NOT call ACP's `authenticate` method or implement in-band OAuth flows — it delegates auth to each agent's own CLI (e.g., `claude auth login`, `gh auth login`). **Limitation**: This assumes each ACP wrapper shares auth state with its CLI counterpart. This is a reasonable assumption for Claude Code and Codex (which use the same credential stores), but is **not verified for all supported agents** (Copilot, Gemini, Qwen, Augment). If an agent's ACP wrapper manages its own auth independently from the CLI, the "run CLI login" guidance would be wrong. Phase 1 validation should test auth flows for each agent we claim to support.

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

```json
// Client → Agent
{"jsonrpc":"2.0","id":2,"method":"session/prompt","params":{
  "sessionId":"sess_abc123",
  "prompt":[{"type":"text","text":"Fix the bug in server.py"}]
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

**Fallback when `loadSession` is false**: The bridge creates a new session via `session/new` instead. The chat panel shows an info banner: "Previous messages not available — starting fresh continuation." The user can still send new prompts. The existing Agent Kitchen scanner summary is shown above the chat as context for what the session was about.

### ACP Update Types → Frontend Mapping

**Core ACP updates** (part of the spec, all agents should emit these):

| ACP `sessionUpdate` | Frontend Action |
|---|---|
| `agent_message_chunk` | Append text to streaming assistant bubble, re-render markdown |
| `agent_thought_chunk` | Append to collapsible "thinking" block |
| `user_message_chunk` | Render user bubble (during session/load replay) |
| `tool_call` | Create collapsible tool call card (name, kind, status, locations) |
| `tool_call_update` | Update tool card status, append output content |
| `plan` | Render plan checklist (optional, above messages) |
| `available_commands_update` | Ignore (editor-specific) |
| `current_mode_update` | Show mode badge (e.g., "plan mode") |
| `config_option_update` | Ignore |
| `session_info_update` | Update session title |

**Extension updates** (not in the core ACP spec — agent-specific, may not be present):

| Update | Frontend Action | Notes |
|---|---|---|
| `usage_update` | Update cost/token badge in panel header | Only some ACP wrappers emit this. The frontend renders it when present but never depends on it. The cost badge simply stays hidden if no usage events arrive. |

The frontend must handle unknown `sessionUpdate` types gracefully — log and ignore. As ACP evolves, new update types will appear.

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

// Start in restrictive mode (all tool permissions denied)
{"type": "start", "agent": "claude", "cwd": "/path/to/repo", "autoApprove": false}

// Send a user message
{"type": "user_message", "text": "Fix the bug in server.py"}

// Cancel the current turn
{"type": "cancel"}
```

### Server → Client

```json
// Session established — historyLoaded tells the frontend whether history was replayed
// (the backend already decided load vs new based on capabilities + session ID availability)
{"type": "session_init", "sessionId": "sess_abc123", "agentInfo": {"name": "claude-code", ...},
 "historyLoaded": true}

// Agent requires authentication before proceeding
{"type": "auth_required", "agentName": "claude-code",
 "message": "Claude Code requires authentication. Run 'claude auth login' in your terminal."}

// Forwarded ACP update (thin wrapper — unknown sessionUpdate types are passed through)
{"type": "update", "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "..."}}
{"type": "update", "sessionUpdate": "tool_call", "toolCallId": "call_001", ...}
{"type": "update", "sessionUpdate": "tool_call_update", "toolCallId": "call_001", ...}

// Turn complete
{"type": "turn_complete", "stopReason": "end_turn"}

// Error
{"type": "error", "message": "Agent process crashed"}
```

The server forwards ACP `session/update` params directly as WebSocket messages (adding `"type": "update"`). This keeps the backend thin — it doesn't interpret the ACP updates, just relays them.

### Session ID Mapping (Scanner ↔ ACP)

The existing dashboard discovers sessions by scanning native log files (`~/.claude/projects/`, `~/.codex/sessions/`). Each `Session` object has a scanner-assigned `id` derived from the log file path (see `scanner.py`). ACP sessions have their own IDs assigned by the agent wrapper.

These are **different ID spaces**. The mapping strategy:

- **Resuming a scanner session (PROTOTYPE ASSUMPTION — needs validation)**: The `start` message includes the scanner `sessionId`. The bridge passes this to `session/load` (if supported) or `session/new`. Whether the ACP wrappers accept native agent session IDs (e.g., Claude's JSONL-derived IDs) for `session/load` is **unverified**. The Claude ACP wrapper uses the Agent SDK internally, which may assign its own IDs that don't match the scanner's. This must be validated during Phase 1 prototyping. If the mapping doesn't work, the fallback is always a fresh `session/new` with the scanner summary shown as context.

- **New sessions created via chat**: These get an ACP-assigned session ID. The agent writes its own log files (Claude Code writes JSONL to `~/.claude/projects/`), so the session will appear in the scanner on next refresh. The dashboard does NOT need to track ACP session IDs long-term — they're ephemeral to the chat panel lifetime.

- **No ID registry needed**: The chat panel holds the ACP session ID in memory for the WebSocket lifetime. When the tab closes, the ID is discarded. The scanner remains the source of truth for the session list.

- **Phase 1 validation task**: Before building the full resume flow, prototype `session/load` with a known Claude session ID and verify the ACP wrapper accepts it. If it doesn't, session resume degrades to "fresh session + summary context" for all agents, and `session/load` is only used for sessions originally created through the chat panel (where we control the ID).

## Files to Modify

| File | Change |
|---|---|
| `src/agent_kitchen/acp_bridge.py` | **New file**: ACP client that spawns agents and bridges to WebSocket |
| `src/agent_kitchen/server.py` | Add `/ws/chat` endpoint that delegates to `acp_bridge` |
| `src/agent_kitchen/static/chat.js` | **New file**: chat UI module (tabs, WebSocket, rendering, input) |
| `src/agent_kitchen/static/index.html` | CDN deps (marked, highlight.js, DOMPurify), chat panel HTML |
| `src/agent_kitchen/static/style.css` | Chat panel layout, bubble styles, markdown styles, tool cards |
| `src/agent_kitchen/static/app.js` | View mode toggle, route session clicks to chat vs terminal |
| `pyproject.toml` | Add `agent-client-protocol` dependency |

## Task Breakdown

### Phase 1: Backend — ACP Bridge

**1.0 — Validation spikes** (before committing to the full design)

Two assumptions must be validated before building the full implementation:

1. **Terminal capability**: Spawn Claude Code ACP wrapper without `terminal: true` in client capabilities. Send a prompt that requires command execution (e.g., "run `ls`"). Verify the agent can still execute commands. If it can't, we need to implement terminal callbacks or find a workaround.

2. **Session ID mapping**: Get a known Claude session ID from the scanner. Spawn the Claude ACP wrapper and attempt `session/load` with that ID. Verify whether the wrapper accepts native Claude session IDs or only its own internally-assigned IDs.

If (1) fails, terminal callback implementation becomes a required Phase 1 task. If (2) fails, session resume degrades to "fresh session + summary context" for scanner-discovered sessions.

**1.1 — Add `agent-client-protocol` dependency** (`pyproject.toml`)
- Add `"agent-client-protocol"` to dependencies

**1.2 — Create `acp_bridge.py`** (new file, ~150 lines)

Core class: `ACPBridge` — manages an ACP agent subprocess lifecycle and bridges updates to a callback.

```python
class ACPBridge:
    """Bridges an ACP agent subprocess to a WebSocket callback."""
    
    def __init__(self, agent_command: list[str], cwd: str, on_update: Callable,
                 auto_approve: bool = False):
        self._cwd = Path(cwd).resolve()
        self._auto_approve = auto_approve
        self._agent_caps = {}  # populated after initialize
        ...
    
    async def start(self, session_id: str | None = None) -> dict:
        """Spawn agent, initialize, create/load session.
        
        Returns {"sessionId": str, "capabilities": dict, "agentInfo": dict}.
        Raises AuthRequiredError if agent needs authentication.
        """
        # 1. acp.spawn_agent_process(client, *agent_command)
        # 2. conn.initialize(...) — advertise only fs capabilities, NOT terminal
        # 3. Store agent_caps from response (loadSession, etc.)
        # 4. If session_id and agent_caps.loadSession:
        #        conn.load_session(sessionId=session_id, cwd=cwd)
        #    elif session_id:
        #        conn.new_session(cwd=cwd)  # fallback: fresh session, no history
        #    else:
        #        conn.new_session(cwd=cwd)
        # 5. If session/new fails with auth error: raise AuthRequiredError
    
    @property
    def can_load_session(self) -> bool:
        return self._agent_caps.get("loadSession", False)
    
    async def prompt(self, text: str) -> str:
        """Send user message, stream updates via on_update callback. Returns stop_reason."""
    
    async def cancel(self):
        """Cancel current turn."""
    
    async def close(self):
        """Terminate agent process."""
```

The `on_update` callback is called for every `session/update` notification. It receives the raw update dict — the WebSocket handler wraps it and sends to browser.

ACP Client implementation (passed to `spawn_agent_process`):

```python
class KitchenACPClient(acp.Client):
    """ACP client callbacks for Agent Kitchen.
    
    FS access is scoped to the session's cwd to prevent out-of-repo writes.
    Terminal callbacks are NOT implemented — we don't advertise terminal
    capability, so agents won't call these methods.
    """
    
    def __init__(self, on_update: Callable, cwd: Path, auto_approve: bool = False):
        self._on_update = on_update
        self._cwd = cwd.resolve()
        self._auto_approve = auto_approve
    
    def _validate_path(self, path: str) -> Path:
        """Ensure path is under cwd. Raises ValueError if not."""
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self._cwd):
            raise ValueError(f"Path {path} is outside session root {self._cwd}")
        return resolved
    
    async def session_update(self, session_id, update, **kwargs):
        await self._on_update(session_id, update)
    
    async def request_permission(self, options, session_id, tool_call, **kwargs):
        """Handle permission requests from the agent.
        
        ACP permission responses use RequestPermissionOutcome:
          - {"outcome": "selected", "optionId": "<id>"}  — approve a specific option
          - {"outcome": "cancelled"}                      — deny/cancel
        
        When auto_approve is True, selects the first option by its optionId
        (matching --dangerously-skip-permissions behavior). When False,
        always cancels.
        """
        if self._auto_approve and options:
            return {"outcome": "selected", "optionId": options[0].get("optionId")}
        else:
            return {"outcome": "cancelled"}
    
    async def read_text_file(self, path, line=None, limit=None, **kwargs):
        """Read a file, scoped to session cwd. Supports ACP slice params."""
        validated = self._validate_path(path)
        text = validated.read_text()
        if line is not None:
            lines = text.splitlines(keepends=True)
            start = max(0, line - 1)  # ACP lines are 1-indexed
            end = start + limit if limit else len(lines)
            text = "".join(lines[start:end])
        return {"content": text}
    
    async def write_text_file(self, path, content, **kwargs):
        """Write a file, scoped to session cwd."""
        validated = self._validate_path(path)
        validated.write_text(content)
    
    # Terminal callbacks intentionally not implemented.
    # We don't advertise terminal capability in initialize,
    # so compliant agents won't call these.
```

**Permission model**: Auto-approve defaults to **true** because Agent Kitchen is a local dashboard for the user's own machine — denying tool calls blocks normal coding agent functionality. The WebSocket `start` message includes an `"autoApprove": true/false` flag. The dashboard UI shows a lock icon toggle to switch to restrictive mode (deny all), which is useful for read-only observation or untrusted agents. Interactive per-tool approval (prompting the user in the browser for each permission request) is deferred to a future iteration.

**1.3 — Agent registry** (in `acp_bridge.py`, ~20 lines)

Map agent names to their ACP spawn commands:

```python
AGENT_COMMANDS = {
    "claude": ["npx", "@agentclientprotocol/claude-agent-acp"],
    "codex": ["npx", "@zed-industries/codex-acp"],
    "copilot": ["npx", "@github/copilot-language-server", "--acp"],
    "gemini": ["npx", "@google/gemini-cli", "--experimental-acp"],
}
```

**1.4 — `/ws/chat` WebSocket endpoint** (`server.py`, ~80 lines)
- Accept WebSocket, wait for `start` message (extract `agent`, `cwd`, optional `sessionId`, `autoApprove`)
- Create `ACPBridge` with the agent command, cwd, on_update callback, and auto_approve flag
- Call `bridge.start()`:
  - On success → send `session_init` (includes `capabilities` from agent) to client
  - On `AuthRequiredError` → send `auth_required` with agent name and instructions
- On `user_message`: call `bridge.prompt(text)` → updates stream via callback → send `turn_complete`
- On `cancel`: call `bridge.cancel()`
- On `retry` (after auth): re-attempt `bridge.start()`
- Cleanup: `bridge.close()` on disconnect

### Phase 2: Frontend HTML

**2.1 — CDN dependencies** (`index.html`)
- `marked` (~30KB) — markdown rendering
- `highlight.js` core + python/js/ts/bash/json/css/xml (~50KB gzipped) — syntax highlighting
- `DOMPurify` — XSS protection

**2.2 — Chat panel HTML** (`index.html`)
```html
<div id="chat-panel" class="chat-panel hidden">
  <div class="chat-panel-header">
    <div class="chat-tabs" id="chat-tabs"></div>
    <div class="chat-panel-controls">
      <span class="chat-cost" id="chat-cost"></span>
      <button class="view-toggle" id="view-toggle" title="Switch to terminal view">TTY</button>
      <button class="chat-panel-close" id="chat-close">&times;</button>
    </div>
  </div>
  <div class="chat-messages" id="chat-messages"></div>
  <div class="chat-input-bar">
    <textarea id="chat-input" class="chat-input" placeholder="Send a message..." rows="1"></textarea>
    <button id="chat-send" class="chat-send-btn">↑</button>
  </div>
</div>
```

### Phase 3: Chat Module (`chat.js`)

**3.1 — Tab management** (~100 lines)
- `createChatTab(title, agent, cwd, existingSessionId)`
- `switchChatTab(tabId)`, `closeChatTab(tabId)`, `renderChatTabs()`
- Tab data shape:
  ```
  {id, ws, sessionId, agent, cwd, container, title, 
   streaming, currentTextAccum, currentTextEl, thinkingEl, thinkingAccum}
  ```

**3.2 — WebSocket + message routing** (~120 lines)
- `connectWebSocket(tabData)` — connect to `/ws/chat`, send `start` with agent + cwd
- `sendUserMessage(tabId, text)` — add user bubble, send `user_message`
- `handleServerMessage(tabId, msg)` — route by `msg.type`:
  - `session_init` → store sessionId, show agent info
  - `update` → route by `msg.sessionUpdate` (see mapping table above)
  - `turn_complete` → finalize, re-enable input
  - `error` → show error in chat

**3.3 — Rendering** (~200 lines)

- `renderUserBubble(text)` — user message div
- `renderAgentTextChunk(tabData, text)` — append to streaming text accumulator, schedule markdown re-render via `requestAnimationFrame`
- `renderThinkingChunk(tabData, text)` — append to collapsible "thinking" block
- `renderToolCall(tabData, toolCall)` — create collapsible card:
  - Header: icon by `kind` (read/edit/execute/search), title, status badge, file locations
  - Body (collapsed): rawInput JSON
- `updateToolCall(tabData, update)` — find card by `toolCallId`, update status, append content/diffs
- `renderDiff(path, oldText, newText)` — inline diff view for edit tool calls
- `renderPlan(entries)` — checklist of plan entries with status icons
- `renderUsage(cost, used, size)` — cost badge in panel header
- `finalizeAssistantMessage(tabData)` — clean final markdown render after turn completes
- `scrollToBottom(container)`

Markdown rendering approach:
- Configure `marked` with `highlight.js` for code blocks
- All output through `DOMPurify.sanitize()` before DOM insertion
- During streaming: accumulate text, re-render via `requestAnimationFrame` (max 1 render/frame)
- After turn complete: one final clean render pass

**3.4 — Input handling** (~50 lines)
- Auto-growing textarea (adjust rows on input)
- Enter to send, Shift+Enter for newline
- Disable input + show spinner while streaming
- Cancel button appears during streaming

**3.5 — Public API** (~30 lines)
- `window.AgentChat = { openChat(session), openNewChat(cwd) }`

### Phase 4: Integration (`app.js`)

**4.1 — View mode toggle + routing**
- `localStorage` preference: `"chat"` (default) or `"terminal"`
- Modify `launchSession(session)`:
  - Chat mode → `AgentChat.openChat(session)`
  - Terminal mode → existing `openTerminal(session)` 
- Modify `openNewSession(cwd)`:
  - Chat mode → `AgentChat.openNewChat(cwd)` — agent is determined by chat module (see below)
  - Terminal mode → existing behavior
- View toggle button switches panels and saves preference
- Note: `openNewSession()` at `app.js:624` only has `cwd` in scope — it does not have access to `session.source`. The agent selection is handled by the chat module, not the caller.

**4.2 — Agent selector for new sessions**
- `AgentChat.openNewChat(cwd)` shows an agent picker dropdown (claude/codex/copilot/gemini) in the chat panel header
- Default agent is read from `localStorage` (last used agent), with "claude" as initial default
- The repo group header's "+" button calls `openNewSession(cwd)`, which delegates to `AgentChat.openNewChat(cwd)` — the agent picker is internal to the chat module

### Phase 5: CSS (`style.css`)

**5.1 — Chat panel layout** (~60 lines)
- Same positioning as terminal panel (fixed bottom, 50vh, resizable)
- Messages: scrollable, flex column, padding
- Input bar: flex row, fixed at bottom

**5.2 — Chat content styles** (~150 lines)
- User bubble: right-aligned, accent (#FF4D00) background, white text
- Assistant bubble: left-aligned, dark surface (#1a1a1a) background
- Thinking block: collapsible, dim italic text, border-left accent
- Markdown: headings, code blocks (dark bg, syntax highlighted), tables, lists, blockquotes, inline code
- Tool call cards:
  - Collapsed header: icon + title + status badge + file path
  - Expanded body: rawInput, content, diff view
  - Status colors: pending (gray), in_progress (orange pulse), completed (green), failed (red)
- Streaming cursor: blinking block animation after current text
- Plan checklist: entries with status icons (pending/completed/failed)
- Cost badge: monospace, dim, in panel header

**5.3 — View toggle + agent selector** (~20 lines)

### Phase 6: Polish

**6.1 — Error handling**
- Agent process crash → show error in chat, offer restart
- WebSocket disconnect → show reconnect prompt
- ACP initialization failure → show friendly error with agent name

**6.2 — Cancel**
- Cancel button during streaming → sends `cancel` → agent receives `session/cancel`
- Show "cancelled" indicator on partial response

**6.3 — XSS safety**
- All `marked.parse()` output through `DOMPurify.sanitize()`
- Tool call rawInput/rawOutput displayed as `<pre>` with text content (not innerHTML)
- File paths displayed as text nodes

**6.4 — Session history on resume**
- The backend decides whether to use `session/load` or `session/new` based on agent capabilities and session ID
- The `session_init` WebSocket message includes `"historyLoaded": true/false` — an explicit result flag
- If `historyLoaded: true`: history was replayed as `user_message_chunk`/`agent_message_chunk`/`tool_call` updates before `session_init` — the frontend already rendered them
- If `historyLoaded: false`: frontend shows info banner with scanner summary ("This session was about: {summary}") above the empty chat

**6.5 — Authentication flow**
- Handle `auth_required` WebSocket message
- Show banner in chat panel: "Agent requires authentication" with instructions
- Provide a "Retry" button that re-sends the `start` message
- Show agent-specific auth instructions (e.g., "Run `claude auth login` in your terminal")

## Implementation Order

1. **Phase 1** (backend): validation spikes first (terminal capability, session ID mapping), then `pyproject.toml` dep, `acp_bridge.py`, `/ws/chat` endpoint
2. **Phase 2** (HTML): CDN deps, chat panel scaffold
3. **Phase 3.1-3.2** (chat core): tabs, WebSocket, message routing
4. **Phase 5.1** (layout CSS): make panel visible
5. **Phase 3.3** (rendering): markdown, tool cards, streaming — the core UX
6. **Phase 5.2** (content CSS): make it beautiful
7. **Phase 3.4-3.5** (input + API): complete interaction loop
8. **Phase 4** (integration): wire session clicks, view toggle
9. **Phase 6** (polish): errors, cancel, XSS, session history

## Verification

1. `uv run pytest` — existing tests pass
2. `uvx agent-kitchen web` — start dashboard
3. Click a Claude session → chat panel opens, history loads if agent supports `loadSession`
4. Click a session from an agent without `loadSession` → info banner shown, fresh session starts
5. Send a message → streaming markdown response with thinking blocks
6. Ask Claude to edit a file → tool call card with diff view
7. Agent cannot write outside session cwd → `ValueError` raised, error shown in chat
8. Send follow-up → multi-turn works
9. Open new session, select Codex → Codex agent responds in same UI
10. Toggle to TTY → xterm.js terminal still works
11. Click cancel during streaming → turn cancelled cleanly
12. Cost badge updates if agent emits `usage_update`, stays hidden otherwise
13. Default mode (autoApprove: true) → agent tool calls proceed normally
14. Lock icon toggle (autoApprove: false) → agent tool calls are cancelled (permissions denied)
15. Agent with expired auth → `auth_required` message shown, retry works after re-auth

## Key Design Decisions

- **ACP over proprietary formats**: ACP is supported by Claude Code, Codex, Copilot, Gemini, and others. One protocol, any agent. The Python SDK (`agent-client-protocol`) handles JSON-RPC 2.0 framing.
- **Graceful capability degradation**: ACP capabilities like `loadSession` are optional. The bridge checks what each agent supports and falls back cleanly (e.g., fresh session instead of history replay). Unknown `sessionUpdate` types and extension events like `usage_update` are rendered when present, ignored when absent.
- **Persistent agent process**: Unlike the earlier per-turn subprocess design, ACP keeps the agent process alive across turns. `session/prompt` is called multiple times on the same connection. Simpler, no startup overhead per turn.
- **Scoped FS access**: `read_text_file` and `write_text_file` callbacks validate that all paths resolve under the session's `cwd`. Out-of-repo access is rejected with an error. This is a meaningful safety boundary for a browser-triggered client.
- **Auto-approve with opt-out**: Auto-approve defaults to on — Agent Kitchen is a local dashboard and denying all tool calls blocks normal agent functionality. A lock icon toggle switches to restrictive mode (deny all). ACP permissions use `RequestPermissionOutcome` with `{outcome: "selected", optionId}` or `{outcome: "cancelled"}`. Interactive per-tool approval in the browser is deferred to a future iteration.
- **No terminal capability (risk)**: Agent Kitchen does not advertise `terminal: true` in the ACP handshake and does not implement terminal callbacks. This avoids a large implementation surface, but may cause agents to degrade if they depend on client-side terminal management for command execution. Must be validated in Phase 1.
- **Delegated authentication**: Auth is handled by each agent's own CLI, not via ACP's `authenticate` method. This assumes ACP wrappers share credential stores with their CLIs — verified for Claude/Codex, unverified for others. If an agent needs auth, the chat panel shows instructions and a retry button.
- **Scanner remains source of truth**: ACP session IDs are ephemeral to the chat panel. The scanner's native log file parsing remains the authoritative session list. New sessions created via chat appear in the scanner on next refresh because agents write their own log files.
- **Session resume is a prototype assumption**: Whether ACP wrappers accept native agent session IDs (from the scanner) for `session/load` is unverified. Phase 1 must validate this before the resume flow is built. Fallback is always "fresh session + scanner summary as context."
- **Backend as thin relay**: Server forwards ACP updates to WebSocket without deep interpretation. Resilient to protocol evolution.
- **`requestAnimationFrame` for streaming**: Markdown re-rendered at most once per frame to avoid jank.
- **DOMPurify**: Tool results contain arbitrary file contents. All rendered HTML must be sanitized.
