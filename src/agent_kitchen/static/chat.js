// ABOUTME: Chat panel UI module — rich markdown rendering of ACP agent conversations.
// ABOUTME: Manages chat tabs, WebSocket connections, streaming text, and tool call cards.

(function () {
  "use strict";

  // --- Markdown setup ---
  marked.setOptions({
    highlight: function (code, lang) {
      if (lang && hljs.getLanguage(lang)) {
        return hljs.highlight(code, { language: lang }).value;
      }
      return hljs.highlightAuto(code).value;
    },
    breaks: true,
    gfm: true,
  });

  // --- State ---
  var chatTabs = {};
  var activeChatTabId = null;
  var chatTabIdCounter = 0;

  // --- DOM refs ---
  var $chatPanel = document.getElementById("chat-panel");
  var $chatTabs = document.getElementById("chat-tabs");
  var $chatMessages = document.getElementById("chat-messages");
  var $chatInput = document.getElementById("chat-input");
  var $chatSend = document.getElementById("chat-send");
  var $chatClose = document.getElementById("chat-close");
  var $chatCost = document.getElementById("chat-cost");
  var $imagePreview = document.getElementById("chat-image-preview");


  // --- Helpers ---

  function escapeHtml(s) {
    var div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  function renderMarkdown(text) {
    return DOMPurify.sanitize(marked.parse(text || ""));
  }

  function generateChatTabId() {
    return "chat-" + (chatTabIdCounter++);
  }

  function scrollToBottom() {
    if (!$chatMessages) return;
    $chatMessages.scrollTop = $chatMessages.scrollHeight;
  }

  // --- Tool call icons by kind ---
  var TOOL_ICONS = {
    read: "&#128196;",     // page
    edit: "&#9998;",       // pencil
    execute: "&#9654;",    // play
    search: "&#128269;",   // magnifier
    "delete": "&#128465;", // trash
    think: "&#128161;",    // lightbulb
    fetch: "&#127760;",    // globe
  };

  // --- Tab Management ---

  function renderChatTabs() {
    $chatTabs.innerHTML = "";
    Object.keys(chatTabs).forEach(function (tabId) {
      var tab = chatTabs[tabId];
      var el = document.createElement("div");
      el.className = "chat-tab" + (tabId === activeChatTabId ? " active" : "");
      el.innerHTML =
        '<span class="chat-tab-title">' + escapeHtml(tab.title) + "</span>" +
        '<span class="chat-tab-close">&times;</span>';

      el.querySelector(".chat-tab-title").addEventListener("click", function () {
        switchChatTab(tabId);
      });
      el.querySelector(".chat-tab-close").addEventListener("click", function (e) {
        e.stopPropagation();
        closeChatTab(tabId);
      });
      $chatTabs.appendChild(el);
    });
  }

  function switchChatTab(tabId) {
    if (!chatTabs[tabId]) return;
    if (activeChatTabId && chatTabs[activeChatTabId]) {
      chatTabs[activeChatTabId].container.classList.remove("active");
    }
    activeChatTabId = tabId;
    chatTabs[tabId].container.classList.add("active");
    renderChatTabs();
    renderTurnSidebar(chatTabs[tabId]);
    updateInputState();
    renderImagePreview();
    scrollToBottom();
    $chatInput.focus();
  }

  function closeChatTab(tabId) {
    var tab = chatTabs[tabId];
    if (!tab) return;
    if (tab.ws && tab.ws.readyState === WebSocket.OPEN) {
      tab.ws.close();
    }
    if (tab.container && tab.container.parentNode) {
      tab.container.parentNode.removeChild(tab.container);
    }
    delete chatTabs[tabId];

    var remaining = Object.keys(chatTabs);
    if (remaining.length > 0) {
      switchChatTab(remaining[remaining.length - 1]);
    } else {
      activeChatTabId = null;
      $chatPanel.classList.add("hidden");
      document.body.classList.remove("chat-open");
    }
    renderChatTabs();
  }

  // --- Chat Tab Creation ---

  function createChatTab(title, agent, cwd, existingSessionId, sessionSummary) {
    var tabId = generateChatTabId();

    var container = document.createElement("div");
    container.className = "chat-tab-container";
    $chatMessages.appendChild(container);

    var tabData = {
      id: tabId,
      ws: null,
      sessionId: null,
      agent: agent,
      cwd: cwd,
      container: container,
      title: title,
      streaming: false,
      currentTextAccum: "",
      currentTextEl: null,
      thinkingAccum: "",
      thinkingEl: null,
      renderScheduled: false,
      sessionSummary: sessionSummary || null,
      userTurns: [],
      activeTurnIndex: -1,
      pendingImages: [],
    };
    chatTabs[tabId] = tabData;

    // Show panel
    $chatPanel.classList.remove("hidden");
    document.body.classList.add("chat-open");

    // Hide previous active tab
    if (activeChatTabId && chatTabs[activeChatTabId]) {
      chatTabs[activeChatTabId].container.classList.remove("active");
    }
    activeChatTabId = tabId;
    container.classList.add("active");

    renderChatTabs();
    connectWebSocket(tabData, existingSessionId);
    $chatInput.focus();

    return tabId;
  }

  // --- WebSocket ---

  function connectWebSocket(tabData, sessionId) {
    var wsProtocol = location.protocol === "https:" ? "wss:" : "ws:";
    var wsUrl = wsProtocol + "//" + location.host + "/ws/chat";
    var ws = new WebSocket(wsUrl);
    tabData.ws = ws;

    ws.onopen = function () {
      ws.send(JSON.stringify({
        type: "start",
        agent: tabData.agent,
        cwd: tabData.cwd,
        sessionId: sessionId || undefined,
      }));
    };

    ws.onmessage = function (evt) {
      try {
        var msg = JSON.parse(evt.data);
        handleServerMessage(tabData, msg);
      } catch (e) {
        console.error("Failed to parse chat message:", e);
      }
    };

    ws.onclose = function () {
      tabData.streaming = false;
      updateInputState();
    };

    ws.onerror = function () {
      appendSystemMessage(tabData, "Connection error");
    };
  }

  // --- Message Routing ---

  function handleServerMessage(tabData, msg) {
    switch (msg.type) {
      case "session_init":
        tabData.sessionId = msg.sessionId;
        if (msg.agentInfo) {
          appendSystemMessage(tabData, msg.agentInfo.title + " connected");
        }
        if (!msg.historyLoaded && tabData.sessionSummary) {
          appendInfoBanner(tabData, "Previous messages not available. " + tabData.sessionSummary);
        }
        break;

      case "update":
        handleUpdate(tabData, msg);
        break;

      case "turn_complete":
        finalizeAssistantMessage(tabData);
        tabData.streaming = false;
        updateInputState();
        break;

      case "error":
        appendSystemMessage(tabData, "Error: " + (msg.message || "Unknown error"));
        tabData.streaming = false;
        updateInputState();
        break;

      case "auth_required":
        appendAuthBanner(tabData, msg);
        break;

      case "session_terminated":
        tabData.streaming = false;
        tabData.terminated = true;
        appendSessionTerminated(tabData);
        updateInputState();
        break;

      case "session_restarted":
        tabData.terminated = false;
        tabData.sessionId = msg.sessionId || tabData.sessionId;
        appendSystemMessage(tabData, "Session resumed");
        break;

      default:
        console.log("Unknown chat message type:", msg.type);
    }
  }

  function handleUpdate(tabData, msg) {
    var su = msg.sessionUpdate;
    switch (su) {
      case "agent_message_chunk":
        var text = (msg.content && msg.content.text) || "";
        if (text) appendAgentText(tabData, text);
        break;

      case "agent_thought_chunk":
        var thought = (msg.content && msg.content.text) || "";
        if (thought) appendThinking(tabData, thought);
        break;

      case "user_message_chunk":
        var userText = (msg.content && msg.content.text) || "";
        if (userText) appendUserBubble(tabData, userText);
        break;

      case "tool_call":
        renderToolCall(tabData, msg);
        break;

      case "tool_call_update":
        updateToolCall(tabData, msg);
        break;

      case "usage_update":
        renderUsage(msg);
        break;

      case "plan":
        renderPlan(tabData, msg.entries || []);
        break;

      default:
        // Gracefully ignore unknown update types
        break;
    }
  }

  // --- Rendering: User Messages ---

  function appendUserBubble(tabData, text, images) {
    // Close any open agent message
    finalizeAssistantMessage(tabData);

    var turnIndex = tabData.userTurns.length;
    var bubble = document.createElement("div");
    bubble.className = "chat-bubble user";
    bubble.setAttribute("data-turn-index", turnIndex);
    if (images && images.length) {
      var imgStrip = document.createElement("div");
      imgStrip.className = "chat-bubble-images";
      images.forEach(function (img) {
        var el = document.createElement("img");
        el.src = "data:" + img.mimeType + ";base64," + img.data;
        el.className = "chat-bubble-img";
        imgStrip.appendChild(el);
      });
      bubble.appendChild(imgStrip);
    }
    if (text) {
      var textEl = document.createElement("span");
      textEl.textContent = text;
      bubble.appendChild(textEl);
    }
    tabData.container.appendChild(bubble);

    tabData.userTurns.push({ index: turnIndex, element: bubble, text: text || "(image)" });
    renderTurnSidebar(tabData);
    scrollToBottom();
  }

  // --- Rendering: Agent Text (streaming) ---

  function appendAgentText(tabData, text) {
    // Close thinking if open
    finalizeThinking(tabData);

    tabData.currentTextAccum += text;
    if (!tabData.currentTextEl) {
      var bubble = document.createElement("div");
      bubble.className = "chat-bubble assistant";
      var content = document.createElement("div");
      content.className = "chat-md-content";
      bubble.appendChild(content);
      tabData.container.appendChild(bubble);
      tabData.currentTextEl = content;
    }
    scheduleRender(tabData);
  }

  function scheduleRender(tabData) {
    if (tabData.renderScheduled) return;
    tabData.renderScheduled = true;
    requestAnimationFrame(function () {
      tabData.renderScheduled = false;
      if (tabData.currentTextEl) {
        tabData.currentTextEl.innerHTML = renderMarkdown(tabData.currentTextAccum);
      }
      scrollToBottom();
    });
  }

  function finalizeAssistantMessage(tabData) {
    if (tabData.currentTextEl && tabData.currentTextAccum) {
      tabData.currentTextEl.innerHTML = renderMarkdown(tabData.currentTextAccum);
    }
    tabData.currentTextEl = null;
    tabData.currentTextAccum = "";
    finalizeThinking(tabData);
  }

  // --- Rendering: Thinking ---

  function appendThinking(tabData, text) {
    tabData.thinkingAccum += text;
    if (!tabData.thinkingEl) {
      var block = document.createElement("details");
      block.className = "chat-thinking";
      block.innerHTML = "<summary>Thinking...</summary><div class='chat-thinking-content'></div>";
      tabData.container.appendChild(block);
      tabData.thinkingEl = block.querySelector(".chat-thinking-content");
    }
    tabData.thinkingEl.textContent = tabData.thinkingAccum;
    scrollToBottom();
  }

  function finalizeThinking(tabData) {
    tabData.thinkingEl = null;
    tabData.thinkingAccum = "";
  }

  // --- Rendering: Tool Calls ---

  function renderToolCall(tabData, tc) {
    // Close any open text/thinking so the tool card appears between bubbles
    finalizeAssistantMessage(tabData);

    var card = document.createElement("details");
    card.className = "chat-tool-card";
    card.setAttribute("data-tool-id", tc.toolCallId || "");

    var status = tc.status || "pending";
    var kind = tc.kind || "other";
    var icon = TOOL_ICONS[kind] || "&#128295;"; // wrench default
    var title = tc.title || "Tool call";
    var locationText = "";
    if (tc.locations && tc.locations.length > 0) {
      var loc = tc.locations[0];
      var path = (loc && loc.path) || "";
      locationText = '<span class="chat-tool-path">' + escapeHtml(path.split("/").pop()) + "</span>";
    }

    card.innerHTML =
      '<summary class="chat-tool-header">' +
        '<span class="chat-tool-icon">' + icon + "</span>" +
        '<span class="chat-tool-title">' + escapeHtml(title) + "</span>" +
        locationText +
        '<span class="chat-tool-status ' + escapeHtml(status) + '">' + escapeHtml(status) + "</span>" +
      "</summary>" +
      '<div class="chat-tool-body"></div>';

    tabData.container.appendChild(card);
    scrollToBottom();
  }

  function updateToolCall(tabData, update) {
    var card = tabData.container.querySelector(
      '.chat-tool-card[data-tool-id="' + (update.toolCallId || "") + '"]'
    );
    if (!card) return;

    // Update status badge
    if (update.status) {
      var badge = card.querySelector(".chat-tool-status");
      if (badge) {
        badge.textContent = update.status;
        badge.className = "chat-tool-status " + update.status;
      }
    }

    // Append content
    if (update.content && update.content.length > 0) {
      var body = card.querySelector(".chat-tool-body");
      if (body) {
        update.content.forEach(function (item) {
          if (item.type === "diff") {
            renderDiff(body, item);
          } else if (item.type === "content" && item.content) {
            var text = item.content.text || "";
            if (text) {
              var pre = document.createElement("pre");
              pre.className = "chat-tool-output";
              pre.textContent = text.length > 2000 ? text.substring(0, 2000) + "\n...(truncated)" : text;
              body.appendChild(pre);
            }
          }
        });
      }
    }
    scrollToBottom();
  }

  function renderDiff(container, diff) {
    var el = document.createElement("div");
    el.className = "chat-diff";
    var header = document.createElement("div");
    header.className = "chat-diff-header";
    header.textContent = diff.path || "file";
    el.appendChild(header);

    var pre = document.createElement("pre");
    pre.className = "chat-diff-content";
    // Simple diff display: show newText (the result)
    pre.textContent = diff.newText || "";
    el.appendChild(pre);
    container.appendChild(el);
  }

  // --- Rendering: Plan ---

  function renderPlan(tabData, entries) {
    // Remove any existing plan
    var existing = tabData.container.querySelector(".chat-plan");
    if (existing) existing.parentNode.removeChild(existing);

    if (!entries.length) return;

    var el = document.createElement("div");
    el.className = "chat-plan";
    el.innerHTML = "<div class='chat-plan-title'>Plan</div>";
    var list = document.createElement("ul");
    entries.forEach(function (entry) {
      var li = document.createElement("li");
      var statusIcon = entry.status === "completed" ? "&#10003;" : entry.status === "failed" ? "&#10007;" : "&#9744;";
      li.innerHTML = '<span class="chat-plan-icon">' + statusIcon + '</span> ' + escapeHtml(entry.content || "");
      li.className = "chat-plan-entry " + (entry.status || "pending");
      list.appendChild(li);
    });
    el.appendChild(list);
    tabData.container.appendChild(el);
    scrollToBottom();
  }

  // --- Rendering: Usage ---

  function renderUsage(msg) {
    if (!$chatCost) return;
    var parts = [];

    if (msg.cost && msg.cost.amount != null) {
      parts.push("$" + msg.cost.amount.toFixed(4));
    }

    if (msg.used != null && msg.size != null && msg.size > 0) {
      var usedK = Math.round(msg.used / 1000);
      var sizeK = Math.round(msg.size / 1000);
      var pct = Math.round((msg.used / msg.size) * 100);
      parts.push(usedK + "K / " + sizeK + "K (" + pct + "%)");
    }

    if (parts.length > 0) {
      $chatCost.textContent = parts.join(" | ");
      $chatCost.classList.remove("hidden", "context-warn", "context-critical");
      if (msg.used != null && msg.size != null && msg.size > 0) {
        var pct = (msg.used / msg.size) * 100;
        if (pct >= 90) {
          $chatCost.classList.add("context-critical");
        } else if (pct >= 75) {
          $chatCost.classList.add("context-warn");
        }
      }
    }
  }

  // --- Turn Navigation Sidebar ---

  var $turnSidebar = document.getElementById("chat-turn-sidebar");

  function renderTurnSidebar(tabData) {
    if (!$turnSidebar) return;
    var turns = tabData.userTurns;
    if (turns.length === 0) {
      $turnSidebar.classList.add("hidden");
      return;
    }

    $turnSidebar.classList.remove("hidden");
    var list = $turnSidebar.querySelector(".turn-list");
    if (!list) return;
    list.innerHTML = "";

    turns.forEach(function (turn) {
      var item = document.createElement("div");
      item.className = "turn-item" + (turn.index === tabData.activeTurnIndex ? " active" : "");
      item.setAttribute("data-turn-index", turn.index);

      var label = document.createElement("span");
      label.className = "turn-label";
      label.textContent = (turn.index + 1);

      var preview = document.createElement("span");
      preview.className = "turn-preview";
      var previewText = turn.text.length > 60 ? turn.text.substring(0, 60) + "..." : turn.text;
      preview.textContent = previewText;

      item.appendChild(label);
      item.appendChild(preview);
      item.addEventListener("click", function () {
        jumpToTurn(tabData, turn.index);
      });
      list.appendChild(item);
    });

    // Update counter
    var counter = $turnSidebar.querySelector(".turn-counter");
    if (counter) {
      var current = tabData.activeTurnIndex >= 0 ? (tabData.activeTurnIndex + 1) : "-";
      counter.textContent = current + " / " + turns.length;
    }
  }

  function jumpToTurn(tabData, turnIndex) {
    var turns = tabData.userTurns;
    if (turnIndex < 0 || turnIndex >= turns.length) return;

    tabData.activeTurnIndex = turnIndex;
    var el = turns[turnIndex].element;
    el.scrollIntoView({ behavior: "smooth", block: "center" });

    // Brief highlight flash
    el.classList.add("turn-highlight");
    setTimeout(function () { el.classList.remove("turn-highlight"); }, 1500);

    renderTurnSidebar(tabData);
  }

  function jumpToPreviousTurn(tabData) {
    if (!tabData || tabData.userTurns.length === 0) return;
    var next = tabData.activeTurnIndex <= 0 ? 0 : tabData.activeTurnIndex - 1;
    jumpToTurn(tabData, next);
  }

  function jumpToNextTurn(tabData) {
    if (!tabData || tabData.userTurns.length === 0) return;
    var max = tabData.userTurns.length - 1;
    var next = tabData.activeTurnIndex >= max ? max : tabData.activeTurnIndex + 1;
    jumpToTurn(tabData, next);
  }

  // Toggle sidebar visibility
  function toggleTurnSidebar() {
    if (!$turnSidebar) return;
    $turnSidebar.classList.toggle("collapsed");
  }

  // --- Rendering: System / Info / Auth ---

  function appendSystemMessage(tabData, text) {
    var el = document.createElement("div");
    el.className = "chat-system-msg";
    el.textContent = text;
    tabData.container.appendChild(el);
    scrollToBottom();
  }

  function appendInfoBanner(tabData, text) {
    var el = document.createElement("div");
    el.className = "chat-info-banner";
    el.textContent = text;
    tabData.container.appendChild(el);
    scrollToBottom();
  }

  function appendSessionTerminated(tabData) {
    var el = document.createElement("div");
    el.className = "chat-system-msg chat-terminated";
    el.textContent = "Session ended — send a message to resume";
    tabData.container.appendChild(el);
    scrollToBottom();
  }

  function appendAuthBanner(tabData, msg) {
    var el = document.createElement("div");
    el.className = "chat-auth-banner";
    el.innerHTML =
      '<div class="chat-auth-text">' + escapeHtml(msg.message || "Authentication required") + "</div>" +
      '<button class="chat-auth-retry">Retry</button>';
    el.querySelector(".chat-auth-retry").addEventListener("click", function () {
      if (tabData.ws && tabData.ws.readyState === WebSocket.OPEN) {
        tabData.ws.send(JSON.stringify({ type: "retry" }));
        el.parentNode.removeChild(el);
      }
    });
    tabData.container.appendChild(el);
    scrollToBottom();
  }

  // --- Input Handling ---

  function sendUserMessage() {
    if (!activeChatTabId || !chatTabs[activeChatTabId]) return;
    var tab = chatTabs[activeChatTabId];
    if (tab.streaming) return;

    var text = $chatInput.value.trim();
    var images = tab.pendingImages.slice();
    if (!text && !images.length) return;
    $chatInput.value = "";
    $chatInput.style.height = "auto";

    appendUserBubble(tab, text, images);
    tab.pendingImages = [];
    renderImagePreview();
    updateInputPlaceholder();

    if (tab.ws && tab.ws.readyState === WebSocket.OPEN) {
      var msg = { type: "user_message", text: text };
      if (images.length) {
        msg.images = images.map(function (img) {
          return { data: img.data, mimeType: img.mimeType };
        });
      }
      tab.ws.send(JSON.stringify(msg));
      tab.streaming = true;
      updateInputState();
    }
  }

  function updateInputState() {
    var tab = activeChatTabId ? chatTabs[activeChatTabId] : null;
    var streaming = tab && tab.streaming;
    $chatInput.disabled = streaming;
    $chatSend.disabled = streaming;
    if (streaming) {
      $chatInput.placeholder = "Waiting for response...";
    } else if (tab && tab.terminated) {
      $chatInput.placeholder = "Send a message to resume session...";
    } else {
      $chatInput.placeholder = "Send a message...";
    }
  }

  // Auto-grow textarea
  $chatInput.addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 150) + "px";
  });

  // Enter to send, Shift+Enter for newline
  $chatInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendUserMessage();
    }
  });

  $chatSend.addEventListener("click", sendUserMessage);

  // --- Image Paste ---

  $chatInput.addEventListener("paste", function (e) {
    var tab = activeChatTabId ? chatTabs[activeChatTabId] : null;
    if (!tab || tab.streaming) return;

    var items = e.clipboardData && e.clipboardData.items;
    if (!items) return;

    for (var i = 0; i < items.length; i++) {
      if (items[i].type.indexOf("image/") === 0) {
        e.preventDefault();
        var file = items[i].getAsFile();
        if (!file) continue;
        readImageFile(tab, file);
      }
    }
  });

  function readImageFile(tab, file) {
    var reader = new FileReader();
    reader.onload = function () {
      // result is "data:<mimeType>;base64,<data>"
      var parts = reader.result.split(",");
      var mimeType = file.type || "image/png";
      var data = parts[1];
      tab.pendingImages.push({ data: data, mimeType: mimeType });
      renderImagePreview();
      updateInputPlaceholder();
    };
    reader.readAsDataURL(file);
  }

  function updateInputPlaceholder() {
    var tab = activeChatTabId ? chatTabs[activeChatTabId] : null;
    if (!tab || tab.streaming || tab.terminated) return;
    var n = tab.pendingImages.length;
    if (n > 0) {
      $chatInput.placeholder = n + " image" + (n > 1 ? "s" : "") + " attached — add text or press Enter to send";
    } else {
      $chatInput.placeholder = "Send a message...";
    }
  }

  function renderImagePreview() {
    var tab = activeChatTabId ? chatTabs[activeChatTabId] : null;
    $imagePreview.innerHTML = "";
    if (!tab || !tab.pendingImages.length) {
      $imagePreview.classList.remove("active");
      return;
    }
    $imagePreview.classList.add("active");
    tab.pendingImages.forEach(function (img, idx) {
      var thumb = document.createElement("div");
      thumb.className = "image-preview-thumb";
      var imgEl = document.createElement("img");
      imgEl.src = "data:" + img.mimeType + ";base64," + img.data;
      thumb.appendChild(imgEl);
      var removeBtn = document.createElement("button");
      removeBtn.className = "image-preview-remove";
      removeBtn.textContent = "\u00d7";
      removeBtn.setAttribute("data-idx", idx);
      removeBtn.addEventListener("click", function () {
        tab.pendingImages.splice(idx, 1);
        renderImagePreview();
        updateInputPlaceholder();
      });
      thumb.appendChild(removeBtn);
      $imagePreview.appendChild(thumb);
    });
  }


  // Turn navigation keyboard shortcuts (Ctrl+Up/Down)
  document.addEventListener("keydown", function (e) {
    if (!activeChatTabId || !chatTabs[activeChatTabId]) return;
    var tab = chatTabs[activeChatTabId];
    if (!$chatPanel || $chatPanel.classList.contains("hidden")) return;

    if (e.ctrlKey && e.key === "ArrowUp") {
      e.preventDefault();
      jumpToPreviousTurn(tab);
    } else if (e.ctrlKey && e.key === "ArrowDown") {
      e.preventDefault();
      jumpToNextTurn(tab);
    } else if (e.ctrlKey && e.key === "t") {
      e.preventDefault();
      toggleTurnSidebar();
    }
  });

  $chatClose.addEventListener("click", function () {
    // Close all tabs
    Object.keys(chatTabs).forEach(function (tabId) {
      var tab = chatTabs[tabId];
      if (tab.ws && tab.ws.readyState === WebSocket.OPEN) tab.ws.close();
      if (tab.container && tab.container.parentNode) tab.container.parentNode.removeChild(tab.container);
    });
    chatTabs = {};
    activeChatTabId = null;
    $chatPanel.classList.add("hidden");
    if ($turnSidebar) $turnSidebar.classList.add("hidden");
    document.body.classList.remove("chat-open");
    renderChatTabs();
  });


  // --- Public API (called from app.js) ---

  window.AgentChat = {
    openChat: function (session) {
      // Check for existing tab with same session
      var ids = Object.keys(chatTabs);
      for (var i = 0; i < ids.length; i++) {
        if (chatTabs[ids[i]].sessionId === session.id) {
          switchChatTab(ids[i]);
          return;
        }
      }
      var agent = session.source || "claude";
      var title = session.summary || session.id.substring(0, 8);
      createChatTab(title, agent, session.cwd, session.id, session.summary);
    },

    openNewChat: function (cwd) {
      var agent = localStorage.getItem("ak-default-agent") || "claude";
      var displayName = cwd.split("/").filter(Boolean).pop() || cwd;
      createChatTab("New: " + displayName, agent, cwd, null, null);
    },
  };

  // Exposed for testing — not part of the public API
  window._chatInternals = {
    handleServerMessage: handleServerMessage,
    handleUpdate: handleUpdate,
    appendAgentText: appendAgentText,
    appendUserBubble: appendUserBubble,
    finalizeAssistantMessage: finalizeAssistantMessage,
    renderToolCall: renderToolCall,
    sendUserMessage: sendUserMessage,
    buildMessagePayload: function (text, images) {
      var msg = { type: "user_message", text: text };
      if (images && images.length) {
        msg.images = images.map(function (img) {
          return { data: img.data, mimeType: img.mimeType };
        });
      }
      return msg;
    },
    getState: function () {
      return { chatTabs: chatTabs, activeChatTabId: activeChatTabId };
    },
    createTabData: function (container) {
      return {
        id: "test-tab",
        ws: null,
        sessionId: null,
        agent: "claude",
        cwd: "/tmp",
        container: container,
        title: "Test",
        streaming: false,
        currentTextAccum: "",
        currentTextEl: null,
        thinkingAccum: "",
        thinkingEl: null,
        renderScheduled: false,
        sessionSummary: null,
        userTurns: [],
        activeTurnIndex: -1,
        pendingImages: [],
      };
    },
  };
})();
