// ABOUTME: Frontend unit tests for chat.js using Node's test runner and jsdom.
// ABOUTME: Tests message rendering, tool call splitting, image handling, and state management.

import { describe, it, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const CHAT_JS = readFileSync(
  resolve(__dirname, "../src/agent_kitchen/static/chat.js"),
  "utf-8"
);

/**
 * Create a jsdom environment with the minimal DOM and globals that chat.js needs,
 * load chat.js, and return the window object for testing.
 */
function createChatEnv() {
  const html = `<!DOCTYPE html><html><body>
    <div id="chat-window-layer" class="chat-window-layer"></div>
    <div id="chat-dock" class="chat-dock hidden"></div>
  </body></html>`;

  const dom = new JSDOM(html, {
    url: "http://localhost:8100",
    runScripts: "dangerously",
    pretendToBeVisual: true,
  });
  const { window } = dom;

  // Stub marked (returns text wrapped in <p>)
  window.marked = {
    parse: (text) => "<p>" + (text || "").replace(/\n\n/g, "</p><p>") + "</p>",
    setOptions: () => {},
  };

  // Stub DOMPurify (pass-through)
  window.DOMPurify = { sanitize: (html) => html };

  // Stub hljs
  window.hljs = { getLanguage: () => false, highlightAuto: (c) => ({ value: c }) };

  // Stub requestAnimationFrame (execute synchronously)
  window.requestAnimationFrame = (fn) => fn();

  // Load chat.js
  window.eval(CHAT_JS);

  return window;
}

/**
 * Helper: create a winData object with a messages container.
 */
function makeWin(window) {
  const container = window.document.createElement("div");
  container.className = "chat-window-messages";
  window.document.body.appendChild(container);
  return window._chatInternals.createWinData(container);
}

// ============================================================
// Tests
// ============================================================

describe("appendAgentText", () => {
  let win, api;

  beforeEach(() => {
    win = createChatEnv();
    api = win._chatInternals;
  });

  it("creates an assistant bubble with accumulated text", () => {
    const w = makeWin(win);
    api.appendAgentText(w, "Hello ");
    api.appendAgentText(w, "world");
    api.finalizeAssistantMessage(w);

    const bubbles = w.$messages.querySelectorAll(".chat-bubble.assistant");
    assert.equal(bubbles.length, 1);
    assert.ok(bubbles[0].textContent.includes("Hello world"));
  });

  it("resets accumulator after finalize", () => {
    const w = makeWin(win);
    api.appendAgentText(w, "first");
    api.finalizeAssistantMessage(w);

    assert.equal(w.currentTextAccum, "");
    assert.equal(w.currentTextEl, null);
  });
});

describe("renderToolCall splits text bubbles", () => {
  let win, api;

  beforeEach(() => {
    win = createChatEnv();
    api = win._chatInternals;
  });

  it("text before and after tool call become separate bubbles", () => {
    const w = makeWin(win);

    api.appendAgentText(w, "Before the tool call.");

    api.renderToolCall(w, {
      toolCallId: "tc-1",
      title: "Read File",
      kind: "read",
      status: "completed",
    });

    api.appendAgentText(w, "After the tool call.");
    api.finalizeAssistantMessage(w);

    const assistantBubbles = w.$messages.querySelectorAll(
      ".chat-bubble.assistant"
    );
    assert.equal(
      assistantBubbles.length,
      2,
      "Should have two separate assistant text bubbles"
    );
    assert.ok(assistantBubbles[0].textContent.includes("Before"));
    assert.ok(assistantBubbles[1].textContent.includes("After"));
  });

  it("tool card appears between text bubbles in DOM order", () => {
    const w = makeWin(win);

    api.appendAgentText(w, "Part one.");
    api.renderToolCall(w, { toolCallId: "tc-1", title: "exec", status: "completed" });
    api.appendAgentText(w, "Part two.");
    api.finalizeAssistantMessage(w);

    const children = Array.from(w.$messages.children);
    const types = children.map((el) => {
      if (el.classList.contains("chat-bubble")) return "bubble";
      if (el.classList.contains("chat-tool-card")) return "tool";
      return "other";
    });
    assert.deepEqual(types, ["bubble", "tool", "bubble"]);
  });

  it("multiple tool calls create distinct text segments", () => {
    const w = makeWin(win);

    api.appendAgentText(w, "Segment 1.");
    api.renderToolCall(w, { toolCallId: "tc-1", title: "Read", status: "completed" });
    api.appendAgentText(w, "Segment 2.");
    api.renderToolCall(w, { toolCallId: "tc-2", title: "Edit", status: "completed" });
    api.appendAgentText(w, "Segment 3.");
    api.finalizeAssistantMessage(w);

    const bubbles = w.$messages.querySelectorAll(".chat-bubble.assistant");
    assert.equal(bubbles.length, 3);
  });

  it("consecutive tool calls without text do not create empty bubbles", () => {
    const w = makeWin(win);

    api.renderToolCall(w, { toolCallId: "tc-1", title: "Read", status: "completed" });
    api.renderToolCall(w, { toolCallId: "tc-2", title: "Edit", status: "completed" });
    api.appendAgentText(w, "After both.");
    api.finalizeAssistantMessage(w);

    const bubbles = w.$messages.querySelectorAll(".chat-bubble.assistant");
    assert.equal(bubbles.length, 1, "Only one bubble for the text after tools");
  });
});

describe("appendUserBubble", () => {
  let win, api;

  beforeEach(() => {
    win = createChatEnv();
    api = win._chatInternals;
  });

  it("renders text in a user bubble", () => {
    const w = makeWin(win);
    api.appendUserBubble(w, "Hello agent", []);

    const bubble = w.$messages.querySelector(".chat-bubble.user");
    assert.ok(bubble);
    assert.ok(bubble.textContent.includes("Hello agent"));
  });

  it("renders images in user bubble", () => {
    const w = makeWin(win);
    api.appendUserBubble(w, "Look at this", [
      { data: "abc123", mimeType: "image/png" },
    ]);

    const imgs = w.$messages.querySelectorAll(".chat-bubble-img");
    assert.equal(imgs.length, 1);
    assert.ok(imgs[0].src.includes("data:image/png;base64,abc123"));
  });

  it("renders image-only message with no text", () => {
    const w = makeWin(win);
    api.appendUserBubble(w, "", [
      { data: "abc123", mimeType: "image/png" },
    ]);

    const bubble = w.$messages.querySelector(".chat-bubble.user");
    assert.ok(bubble);
    assert.equal(bubble.querySelectorAll(".chat-bubble-img").length, 1);
    assert.equal(bubble.querySelectorAll("span").length, 0);
  });

  it("tracks turn with fallback text for image-only messages", () => {
    const w = makeWin(win);
    api.appendUserBubble(w, "", [
      { data: "abc", mimeType: "image/png" },
    ]);

    assert.equal(w.userTurns.length, 1);
    assert.equal(w.userTurns[0].text, "(image)");
  });

  it("finalizes any open assistant message", () => {
    const w = makeWin(win);

    api.appendAgentText(w, "Agent says hi");
    assert.ok(w.currentTextEl, "Should have open text element");

    api.appendUserBubble(w, "User reply", []);
    assert.equal(w.currentTextEl, null, "Text element should be cleared");
    assert.equal(w.currentTextAccum, "", "Text accum should be cleared");
  });
});

describe("buildMessagePayload", () => {
  let win, api;

  beforeEach(() => {
    win = createChatEnv();
    api = win._chatInternals;
  });

  it("builds text-only payload", () => {
    const msg = api.buildMessagePayload("hello", []);
    assert.equal(msg.type, "user_message");
    assert.equal(msg.text, "hello");
    assert.equal(msg.images, undefined);
  });

  it("builds payload with images", () => {
    const msg = api.buildMessagePayload("describe", [
      { data: "abc", mimeType: "image/png" },
      { data: "def", mimeType: "image/jpeg" },
    ]);
    assert.equal(msg.images.length, 2);
    assert.equal(msg.images[0].data, "abc");
    assert.equal(msg.images[0].mimeType, "image/png");
    assert.equal(msg.images[1].mimeType, "image/jpeg");
  });

  it("builds image-only payload", () => {
    const msg = api.buildMessagePayload("", [
      { data: "abc", mimeType: "image/png" },
    ]);
    assert.equal(msg.text, "");
    assert.equal(msg.images.length, 1);
  });
});

describe("handleUpdate message routing", () => {
  let win, api;

  beforeEach(() => {
    win = createChatEnv();
    api = win._chatInternals;
  });

  it("agent_message_chunk accumulates text", () => {
    const w = makeWin(win);
    api.handleUpdate(w, {
      sessionUpdate: "agent_message_chunk",
      content: { text: "Hello " },
    });
    api.handleUpdate(w, {
      sessionUpdate: "agent_message_chunk",
      content: { text: "world" },
    });

    assert.equal(w.currentTextAccum, "Hello world");
  });

  it("tool_call finalizes text before inserting card", () => {
    const w = makeWin(win);

    api.handleUpdate(w, {
      sessionUpdate: "agent_message_chunk",
      content: { text: "Before." },
    });
    api.handleUpdate(w, {
      sessionUpdate: "tool_call",
      toolCallId: "tc-1",
      title: "Read",
      status: "pending",
    });

    assert.equal(w.currentTextAccum, "");
    assert.equal(w.currentTextEl, null);

    assert.equal(w.$messages.querySelectorAll(".chat-bubble.assistant").length, 1);
    assert.equal(w.$messages.querySelectorAll(".chat-tool-card").length, 1);
  });

  it("user_message_chunk creates user bubble", () => {
    const w = makeWin(win);
    api.handleUpdate(w, {
      sessionUpdate: "user_message_chunk",
      content: { text: "User said this" },
    });

    assert.equal(w.$messages.querySelectorAll(".chat-bubble.user").length, 1);
  });
});

describe("window state management", () => {
  let win, api;

  beforeEach(() => {
    win = createChatEnv();
    api = win._chatInternals;
  });

  function registerWin(w) {
    const state = api.getState();
    state.chatWindows[w.id] = w;
    state.focusedWindowId = w.id;
  }

  it("updateWindowInputState enables input for non-streaming window", () => {
    const w = makeWin(win);
    w.streaming = false;
    api.updateWindowInputState(w);

    assert.equal(w.$input.disabled, false, "Input should be enabled");
    assert.equal(w.$input.placeholder, "Send a message...");
  });

  it("updateWindowInputState shows stop button during streaming", () => {
    const w = makeWin(win);
    w.streaming = true;
    w.messageQueue = [];
    api.updateWindowInputState(w);

    assert.equal(w.$input.disabled, false, "Input should stay enabled for queue");
    assert.equal(w.$input.placeholder, "Agent working... Esc to stop");
    assert.ok(w.$send.classList.contains("hidden"), "Send should be hidden");
    assert.ok(!w.$stop.classList.contains("hidden"), "Stop should be visible");
  });
});

describe("collapseCompletedTools", () => {
  let win, api;

  beforeEach(() => {
    win = createChatEnv();
    api = win._chatInternals;
  });

  it("collapses 3+ consecutive completed tool cards", () => {
    const w = makeWin(win);
    api.renderToolCall(w, { toolCallId: "tc-1", title: "Read", status: "completed" });
    api.renderToolCall(w, { toolCallId: "tc-2", title: "Edit", status: "completed" });
    api.renderToolCall(w, { toolCallId: "tc-3", title: "Bash", status: "completed" });

    api.collapseCompletedTools(w);

    const groups = w.$messages.querySelectorAll(".chat-tool-group");
    assert.equal(groups.length, 1, "Should wrap 3 completed tools in a group");
    assert.ok(groups[0].querySelector("summary").textContent.includes("3 tool calls completed"));
    assert.equal(groups[0].querySelectorAll(".chat-tool-card").length, 3);
  });

  it("does not collapse fewer than 3 completed tool cards", () => {
    const w = makeWin(win);
    api.renderToolCall(w, { toolCallId: "tc-1", title: "Read", status: "completed" });
    api.renderToolCall(w, { toolCallId: "tc-2", title: "Edit", status: "completed" });

    api.collapseCompletedTools(w);

    const groups = w.$messages.querySelectorAll(".chat-tool-group");
    assert.equal(groups.length, 0, "Should not collapse only 2 tools");
  });

  it("does not collapse non-consecutive completed tools", () => {
    const w = makeWin(win);
    api.renderToolCall(w, { toolCallId: "tc-1", title: "Read", status: "completed" });
    api.renderToolCall(w, { toolCallId: "tc-2", title: "Edit", status: "completed" });
    api.appendAgentText(w, "Some text between tools.");
    api.finalizeAssistantMessage(w);
    api.renderToolCall(w, { toolCallId: "tc-3", title: "Bash", status: "completed" });

    api.collapseCompletedTools(w);

    const groups = w.$messages.querySelectorAll(".chat-tool-group");
    assert.equal(groups.length, 0, "Non-consecutive tools should not be collapsed");
  });

  it("does not collapse in-progress tools", () => {
    const w = makeWin(win);
    api.renderToolCall(w, { toolCallId: "tc-1", title: "Read", status: "completed" });
    api.renderToolCall(w, { toolCallId: "tc-2", title: "Edit", status: "completed" });
    api.renderToolCall(w, { toolCallId: "tc-3", title: "Bash", status: "in_progress" });

    api.collapseCompletedTools(w);

    const groups = w.$messages.querySelectorAll(".chat-tool-group");
    assert.equal(groups.length, 0, "Run with in-progress tool should not be collapsed");
  });
});

describe("handleServerMessage", () => {
  let win, api;

  beforeEach(() => {
    win = createChatEnv();
    api = win._chatInternals;
  });

  it("turn_complete finalizes assistant message", () => {
    const w = makeWin(win);
    const state = api.getState();
    state.chatWindows[w.id] = w;
    state.focusedWindowId = null;

    api.appendAgentText(w, "Some text");
    assert.ok(w.currentTextEl);

    api.handleServerMessage(w, { type: "turn_complete", stopReason: "end_turn" });
    assert.equal(w.currentTextEl, null);
    assert.equal(w.streaming, false);
  });
});
