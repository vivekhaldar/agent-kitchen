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
    <div id="chat-panel" class="chat-panel hidden">
      <div class="chat-panel-header">
        <div class="chat-tabs" id="chat-tabs"></div>
        <div class="chat-panel-controls">
          <span class="chat-cost hidden" id="chat-cost"></span>
          <button class="chat-panel-close" id="chat-close">&times;</button>
        </div>
      </div>
      <div class="chat-body">
        <div class="chat-turn-sidebar hidden" id="chat-turn-sidebar">
          <div class="turn-sidebar-header">
            <span class="turn-sidebar-title">Turns</span>
            <span class="turn-counter">-</span>
          </div>
          <div class="turn-list"></div>
        </div>
        <div class="chat-messages" id="chat-messages"></div>
      </div>
      <div class="chat-image-preview" id="chat-image-preview"></div>
      <div class="chat-input-bar">
        <textarea id="chat-input" class="chat-input" rows="1"></textarea>
        <button id="chat-send" class="chat-send-btn">&uarr;</button>
        <button id="chat-stop" class="chat-stop-btn hidden">&square;</button>
      </div>
    </div>
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
 * Helper: create a tabData object attached to a container div.
 */
function makeTab(window) {
  const container = window.document.createElement("div");
  container.className = "chat-tab-container active";
  window.document.getElementById("chat-messages").appendChild(container);
  return window._chatInternals.createTabData(container);
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
    const tab = makeTab(win);
    api.appendAgentText(tab, "Hello ");
    api.appendAgentText(tab, "world");
    api.finalizeAssistantMessage(tab);

    const bubbles = tab.container.querySelectorAll(".chat-bubble.assistant");
    assert.equal(bubbles.length, 1);
    assert.ok(bubbles[0].textContent.includes("Hello world"));
  });

  it("resets accumulator after finalize", () => {
    const tab = makeTab(win);
    api.appendAgentText(tab, "first");
    api.finalizeAssistantMessage(tab);

    assert.equal(tab.currentTextAccum, "");
    assert.equal(tab.currentTextEl, null);
  });
});

describe("renderToolCall splits text bubbles", () => {
  let win, api;

  beforeEach(() => {
    win = createChatEnv();
    api = win._chatInternals;
  });

  it("text before and after tool call become separate bubbles", () => {
    const tab = makeTab(win);

    // Agent sends text
    api.appendAgentText(tab, "Before the tool call.");

    // Agent makes a tool call — this should finalize the text
    api.renderToolCall(tab, {
      toolCallId: "tc-1",
      title: "Read File",
      kind: "read",
      status: "completed",
    });

    // Agent sends more text
    api.appendAgentText(tab, "After the tool call.");
    api.finalizeAssistantMessage(tab);

    const assistantBubbles = tab.container.querySelectorAll(
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
    const tab = makeTab(win);

    api.appendAgentText(tab, "Part one.");
    api.renderToolCall(tab, { toolCallId: "tc-1", title: "exec", status: "completed" });
    api.appendAgentText(tab, "Part two.");
    api.finalizeAssistantMessage(tab);

    const children = Array.from(tab.container.children);
    const types = children.map((el) => {
      if (el.classList.contains("chat-bubble")) return "bubble";
      if (el.classList.contains("chat-tool-card")) return "tool";
      return "other";
    });
    assert.deepEqual(types, ["bubble", "tool", "bubble"]);
  });

  it("multiple tool calls create distinct text segments", () => {
    const tab = makeTab(win);

    api.appendAgentText(tab, "Segment 1.");
    api.renderToolCall(tab, { toolCallId: "tc-1", title: "Read", status: "completed" });
    api.appendAgentText(tab, "Segment 2.");
    api.renderToolCall(tab, { toolCallId: "tc-2", title: "Edit", status: "completed" });
    api.appendAgentText(tab, "Segment 3.");
    api.finalizeAssistantMessage(tab);

    const bubbles = tab.container.querySelectorAll(".chat-bubble.assistant");
    assert.equal(bubbles.length, 3);
  });

  it("consecutive tool calls without text do not create empty bubbles", () => {
    const tab = makeTab(win);

    api.renderToolCall(tab, { toolCallId: "tc-1", title: "Read", status: "completed" });
    api.renderToolCall(tab, { toolCallId: "tc-2", title: "Edit", status: "completed" });
    api.appendAgentText(tab, "After both.");
    api.finalizeAssistantMessage(tab);

    const bubbles = tab.container.querySelectorAll(".chat-bubble.assistant");
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
    const tab = makeTab(win);
    api.appendUserBubble(tab, "Hello agent", []);

    const bubble = tab.container.querySelector(".chat-bubble.user");
    assert.ok(bubble);
    assert.ok(bubble.textContent.includes("Hello agent"));
  });

  it("renders images in user bubble", () => {
    const tab = makeTab(win);
    api.appendUserBubble(tab, "Look at this", [
      { data: "abc123", mimeType: "image/png" },
    ]);

    const imgs = tab.container.querySelectorAll(".chat-bubble-img");
    assert.equal(imgs.length, 1);
    assert.ok(imgs[0].src.includes("data:image/png;base64,abc123"));
  });

  it("renders image-only message with no text", () => {
    const tab = makeTab(win);
    api.appendUserBubble(tab, "", [
      { data: "abc123", mimeType: "image/png" },
    ]);

    const bubble = tab.container.querySelector(".chat-bubble.user");
    assert.ok(bubble);
    // Should have image but no text span
    assert.equal(bubble.querySelectorAll(".chat-bubble-img").length, 1);
    assert.equal(bubble.querySelectorAll("span").length, 0);
  });

  it("tracks turn with fallback text for image-only messages", () => {
    const tab = makeTab(win);
    api.appendUserBubble(tab, "", [
      { data: "abc", mimeType: "image/png" },
    ]);

    assert.equal(tab.userTurns.length, 1);
    assert.equal(tab.userTurns[0].text, "(image)");
  });

  it("finalizes any open assistant message", () => {
    const tab = makeTab(win);

    // Start an assistant message
    api.appendAgentText(tab, "Agent says hi");
    assert.ok(tab.currentTextEl, "Should have open text element");

    // User bubble should finalize it
    api.appendUserBubble(tab, "User reply", []);
    assert.equal(tab.currentTextEl, null, "Text element should be cleared");
    assert.equal(tab.currentTextAccum, "", "Text accum should be cleared");
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
    const tab = makeTab(win);
    api.handleUpdate(tab, {
      sessionUpdate: "agent_message_chunk",
      content: { text: "Hello " },
    });
    api.handleUpdate(tab, {
      sessionUpdate: "agent_message_chunk",
      content: { text: "world" },
    });

    assert.equal(tab.currentTextAccum, "Hello world");
  });

  it("tool_call finalizes text before inserting card", () => {
    const tab = makeTab(win);

    api.handleUpdate(tab, {
      sessionUpdate: "agent_message_chunk",
      content: { text: "Before." },
    });
    api.handleUpdate(tab, {
      sessionUpdate: "tool_call",
      toolCallId: "tc-1",
      title: "Read",
      status: "pending",
    });

    // Text should be finalized
    assert.equal(tab.currentTextAccum, "");
    assert.equal(tab.currentTextEl, null);

    // Both bubble and card should be in DOM
    assert.equal(tab.container.querySelectorAll(".chat-bubble.assistant").length, 1);
    assert.equal(tab.container.querySelectorAll(".chat-tool-card").length, 1);
  });

  it("user_message_chunk creates user bubble", () => {
    const tab = makeTab(win);
    api.handleUpdate(tab, {
      sessionUpdate: "user_message_chunk",
      content: { text: "User said this" },
    });

    assert.equal(tab.container.querySelectorAll(".chat-bubble.user").length, 1);
  });
});

describe("tab switching isolates state", () => {
  let win, api;

  beforeEach(() => {
    win = createChatEnv();
    api = win._chatInternals;
  });

  function registerTab(tab) {
    const state = api.getState();
    state.chatTabs[tab.id] = tab;
    state.activeChatTabId = tab.id;
  }

  it("switching to non-streaming tab enables input", () => {
    const tab1 = makeTab(win);
    tab1.id = "tab-1";
    tab1.streaming = true;
    registerTab(tab1);

    const tab2 = makeTab(win);
    tab2.id = "tab-2";
    tab2.streaming = false;
    const state = api.getState();
    state.chatTabs[tab2.id] = tab2;

    api.switchChatTab("tab-2");

    const input = win.document.getElementById("chat-input");
    assert.equal(input.disabled, false, "Input should be enabled for non-streaming tab");
  });

  it("switching to streaming tab keeps input enabled with queue hint", () => {
    const tab1 = makeTab(win);
    tab1.id = "tab-1";
    tab1.streaming = false;
    registerTab(tab1);

    const tab2 = makeTab(win);
    tab2.id = "tab-2";
    tab2.streaming = true;
    tab2.messageQueue = [];
    const state = api.getState();
    state.chatTabs[tab2.id] = tab2;

    api.switchChatTab("tab-2");

    const input = win.document.getElementById("chat-input");
    assert.equal(input.disabled, false, "Input should stay enabled for streaming tab");
    assert.equal(input.placeholder, "Agent working... Esc to stop");
  });

  it("turn sidebar reflects the active tab's turns", () => {
    const tab1 = makeTab(win);
    tab1.id = "tab-1";
    api.appendUserBubble(tab1, "Tab 1 message", []);
    registerTab(tab1);

    const tab2 = makeTab(win);
    tab2.id = "tab-2";
    const state = api.getState();
    state.chatTabs[tab2.id] = tab2;

    api.switchChatTab("tab-2");

    const sidebar = win.document.getElementById("chat-turn-sidebar");
    const turnItems = sidebar.querySelectorAll(".turn-item");
    assert.equal(turnItems.length, 0, "Tab 2 has no turns, sidebar should be empty");
  });

  it("switching back shows the original tab's turns", () => {
    const tab1 = makeTab(win);
    tab1.id = "tab-1";
    api.appendUserBubble(tab1, "Message in tab 1", []);
    registerTab(tab1);

    const tab2 = makeTab(win);
    tab2.id = "tab-2";
    const state = api.getState();
    state.chatTabs[tab2.id] = tab2;

    api.switchChatTab("tab-2");
    api.switchChatTab("tab-1");

    const sidebar = win.document.getElementById("chat-turn-sidebar");
    const turnItems = sidebar.querySelectorAll(".turn-item");
    assert.equal(turnItems.length, 1, "Tab 1 has one turn");
  });
});

describe("collapseCompletedTools", () => {
  let win, api;

  beforeEach(() => {
    win = createChatEnv();
    api = win._chatInternals;
  });

  it("collapses 3+ consecutive completed tool cards", () => {
    const tab = makeTab(win);
    api.renderToolCall(tab, { toolCallId: "tc-1", title: "Read", status: "completed" });
    api.renderToolCall(tab, { toolCallId: "tc-2", title: "Edit", status: "completed" });
    api.renderToolCall(tab, { toolCallId: "tc-3", title: "Bash", status: "completed" });

    api.collapseCompletedTools(tab);

    const groups = tab.container.querySelectorAll(".chat-tool-group");
    assert.equal(groups.length, 1, "Should wrap 3 completed tools in a group");
    assert.ok(groups[0].querySelector("summary").textContent.includes("3 tool calls completed"));
    // Original cards should be inside the group
    assert.equal(groups[0].querySelectorAll(".chat-tool-card").length, 3);
  });

  it("does not collapse fewer than 3 completed tool cards", () => {
    const tab = makeTab(win);
    api.renderToolCall(tab, { toolCallId: "tc-1", title: "Read", status: "completed" });
    api.renderToolCall(tab, { toolCallId: "tc-2", title: "Edit", status: "completed" });

    api.collapseCompletedTools(tab);

    const groups = tab.container.querySelectorAll(".chat-tool-group");
    assert.equal(groups.length, 0, "Should not collapse only 2 tools");
  });

  it("does not collapse non-consecutive completed tools", () => {
    const tab = makeTab(win);
    api.renderToolCall(tab, { toolCallId: "tc-1", title: "Read", status: "completed" });
    api.renderToolCall(tab, { toolCallId: "tc-2", title: "Edit", status: "completed" });
    api.appendAgentText(tab, "Some text between tools.");
    api.finalizeAssistantMessage(tab);
    api.renderToolCall(tab, { toolCallId: "tc-3", title: "Bash", status: "completed" });

    api.collapseCompletedTools(tab);

    const groups = tab.container.querySelectorAll(".chat-tool-group");
    assert.equal(groups.length, 0, "Non-consecutive tools should not be collapsed");
  });

  it("does not collapse in-progress tools", () => {
    const tab = makeTab(win);
    api.renderToolCall(tab, { toolCallId: "tc-1", title: "Read", status: "completed" });
    api.renderToolCall(tab, { toolCallId: "tc-2", title: "Edit", status: "completed" });
    api.renderToolCall(tab, { toolCallId: "tc-3", title: "Bash", status: "in_progress" });

    api.collapseCompletedTools(tab);

    const groups = tab.container.querySelectorAll(".chat-tool-group");
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
    const tab = makeTab(win);
    // Simulate active tab for updateInputState
    const state = api.getState();
    state.chatTabs[tab.id] = tab;
    state.activeChatTabId = null; // avoid input state errors

    api.appendAgentText(tab, "Some text");
    assert.ok(tab.currentTextEl);

    api.handleServerMessage(tab, { type: "turn_complete", stopReason: "end_turn" });
    assert.equal(tab.currentTextEl, null);
    assert.equal(tab.streaming, false);
  });
});
