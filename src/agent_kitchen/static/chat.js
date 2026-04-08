// ABOUTME: Chat window UI module — floating, draggable chat windows for ACP agent conversations.
// ABOUTME: Each window is independent with its own input, messages, and WebSocket connection.

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
  var chatWindows = {};
  var focusedWindowId = null;
  var windowIdCounter = 0;
  var zIndexCounter = 101;
  var CASCADE_OFFSET = 30;

  // --- DOM refs ---
  var $windowLayer = document.getElementById("chat-window-layer");
  var $dock = document.getElementById("chat-dock");

  // --- Helpers ---

  function escapeHtml(s) {
    var div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  function renderMarkdown(text) {
    return DOMPurify.sanitize(marked.parse(text || ""));
  }

  function generateWindowId() {
    return "cw-" + (windowIdCounter++);
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

  // --- Window Placement ---

  function getNextWindowPosition() {
    var count = Object.keys(chatWindows).length;
    var baseX = 80 + (count * CASCADE_OFFSET) % 300;
    var baseY = 60 + (count * CASCADE_OFFSET) % 200;
    return { x: baseX, y: baseY };
  }

  function getDefaultWindowSize() {
    var w = Math.min(520, window.innerWidth - 40);
    var h = Math.min(600, window.innerHeight - 80);
    return { width: w, height: h };
  }

  // --- Focus Management ---

  function focusWindow(winId) {
    if (focusedWindowId && chatWindows[focusedWindowId]) {
      chatWindows[focusedWindowId].el.classList.remove("focused");
    }
    focusedWindowId = winId;
    if (chatWindows[winId]) {
      var win = chatWindows[winId];
      win.el.classList.add("focused");
      win.el.style.zIndex = ++zIndexCounter;
      win.unreadCount = 0;
      renderDock();
    }
  }

  // --- Dock Bar ---

  function renderDock() {
    $dock.innerHTML = "";
    var minimizedIds = Object.keys(chatWindows).filter(function (id) {
      return chatWindows[id].windowState === "minimized";
    });
    if (minimizedIds.length === 0) {
      $dock.classList.add("hidden");
      return;
    }
    $dock.classList.remove("hidden");
    minimizedIds.forEach(function (winId) {
      var win = chatWindows[winId];
      var pill = document.createElement("div");
      pill.className = "chat-dock-pill" + (win.streaming ? " streaming" : "");

      var status = document.createElement("span");
      status.className = "dock-status";
      pill.appendChild(status);

      var title = document.createElement("span");
      title.className = "dock-title";
      title.textContent = win.title;
      pill.appendChild(title);

      if (win.unreadCount > 0) {
        var unread = document.createElement("span");
        unread.className = "dock-unread";
        unread.textContent = win.unreadCount;
        pill.appendChild(unread);
      }

      var close = document.createElement("button");
      close.className = "dock-close";
      close.textContent = "\u00d7";
      close.addEventListener("click", function (e) {
        e.stopPropagation();
        closeWindow(winId);
      });
      pill.appendChild(close);

      pill.addEventListener("click", function () {
        restoreWindow(winId);
      });

      $dock.appendChild(pill);
    });
  }

  // --- Window State Changes ---

  function minimizeWindow(winId) {
    var win = chatWindows[winId];
    if (!win) return;
    win.windowState = "minimized";
    win.el.classList.add("hidden");
    if (focusedWindowId === winId) {
      focusedWindowId = null;
      // Focus next visible window
      var visible = Object.keys(chatWindows).filter(function (id) {
        return chatWindows[id].windowState !== "minimized";
      });
      if (visible.length > 0) focusWindow(visible[visible.length - 1]);
    }
    renderDock();
    saveWindowPositions();
  }

  function restoreWindow(winId) {
    var win = chatWindows[winId];
    if (!win) return;
    win.windowState = "floating";
    win.el.classList.remove("hidden", "maximized");
    focusWindow(winId);
    renderDock();
    saveWindowPositions();
  }

  function maximizeWindow(winId) {
    var win = chatWindows[winId];
    if (!win) return;
    if (win.windowState === "maximized") {
      // Restore to floating
      win.windowState = "floating";
      win.el.classList.remove("maximized");
    } else {
      win.windowState = "maximized";
      win.el.classList.add("maximized");
      win.el.classList.remove("hidden");
    }
    focusWindow(winId);
    renderDock();
    saveWindowPositions();
  }

  function closeWindow(winId) {
    var win = chatWindows[winId];
    if (!win) return;
    var closedSessionId = win.sessionId;
    if (win.ws && win.ws.readyState === WebSocket.OPEN) {
      win.ws.close();
    }
    if (win.el && win.el.parentNode) {
      win.el.parentNode.removeChild(win.el);
    }
    delete chatWindows[winId];
    if (closedSessionId) {
      emitSessionEvent("agent-session-closed", { sessionId: closedSessionId });
    }
    if (focusedWindowId === winId) {
      focusedWindowId = null;
      var remaining = Object.keys(chatWindows).filter(function (id) {
        return chatWindows[id].windowState !== "minimized";
      });
      if (remaining.length > 0) focusWindow(remaining[remaining.length - 1]);
    }
    renderDock();
    saveWindowPositions();
  }

  // --- Position Persistence ---

  function saveWindowPositions() {
    var positions = {};
    Object.keys(chatWindows).forEach(function (id) {
      var w = chatWindows[id];
      positions[id] = {
        x: w.x, y: w.y, width: w.width, height: w.height,
        windowState: w.windowState,
      };
    });
    try { localStorage.setItem("ak-chat-positions", JSON.stringify(positions)); } catch (e) { /* ignore */ }
  }

  // --- Drag ---

  function initDrag(winId, titlebar) {
    var startX, startY, origX, origY;

    titlebar.addEventListener("mousedown", function (e) {
      var win = chatWindows[winId];
      if (!win || win.windowState === "maximized") return;
      if (e.target.closest(".chat-window-btn")) return;
      e.preventDefault();
      focusWindow(winId);
      startX = e.clientX;
      startY = e.clientY;
      origX = win.x;
      origY = win.y;
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });

    function onMove(e) {
      var win = chatWindows[winId];
      if (!win) return;
      var dx = e.clientX - startX;
      var dy = e.clientY - startY;
      win.x = Math.max(0, Math.min(window.innerWidth - 100, origX + dx));
      win.y = Math.max(0, Math.min(window.innerHeight - 40, origY + dy));
      win.el.style.left = win.x + "px";
      win.el.style.top = win.y + "px";
    }

    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      saveWindowPositions();
    }
  }

  // --- Resize ---

  function initResize(winId, handle, direction) {
    var startX, startY, origW, origH, origX, origY;

    handle.addEventListener("mousedown", function (e) {
      var win = chatWindows[winId];
      if (!win || win.windowState === "maximized") return;
      e.preventDefault();
      e.stopPropagation();
      focusWindow(winId);
      startX = e.clientX;
      startY = e.clientY;
      origW = win.width;
      origH = win.height;
      origX = win.x;
      origY = win.y;
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });

    function onMove(e) {
      var win = chatWindows[winId];
      if (!win) return;
      var dx = e.clientX - startX;
      var dy = e.clientY - startY;
      if (direction === "right" || direction === "corner") {
        win.width = Math.max(360, origW + dx);
      }
      if (direction === "bottom" || direction === "corner") {
        win.height = Math.max(280, origH + dy);
      }
      win.el.style.width = win.width + "px";
      win.el.style.height = win.height + "px";
    }

    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      saveWindowPositions();
    }
  }

  // --- Window Creation ---

  function buildWindowDOM(winId, title) {
    var el = document.createElement("div");
    el.className = "chat-window";
    el.setAttribute("data-window-id", winId);

    el.innerHTML =
      '<div class="chat-window-titlebar">' +
        '<span class="chat-window-title">' + escapeHtml(title) + '</span>' +
        '<div class="chat-window-controls">' +
          '<span class="chat-window-cost hidden"></span>' +
          '<button class="chat-window-btn win-minimize" title="Minimize">&#8211;</button>' +
          '<button class="chat-window-btn win-maximize" title="Maximize">&#9744;</button>' +
          '<button class="chat-window-btn win-close" title="Close">&times;</button>' +
        '</div>' +
      '</div>' +
      '<div class="chat-window-body">' +
        '<div class="chat-turn-sidebar hidden">' +
          '<div class="turn-sidebar-header">' +
            '<span class="turn-sidebar-title">Turns</span>' +
            '<span class="turn-counter">-</span>' +
          '</div>' +
          '<div class="turn-list"></div>' +
          '<div class="turn-shortcuts">Ctrl+&uarr;/&darr; navigate</div>' +
        '</div>' +
        '<div class="chat-window-messages"></div>' +
      '</div>' +
      '<div class="chat-image-preview"></div>' +
      '<div class="chat-input-bar">' +
        '<textarea class="chat-input" placeholder="Send a message..." rows="1"></textarea>' +
        '<button class="chat-send-btn" aria-label="Send">&uarr;</button>' +
        '<button class="chat-stop-btn hidden" aria-label="Stop" title="Stop (Esc)">&square;</button>' +
      '</div>' +
      '<div class="chat-resize-handle right"></div>' +
      '<div class="chat-resize-handle bottom"></div>' +
      '<div class="chat-resize-handle corner"></div>';

    return el;
  }

  function createChatWindow(title, agent, cwd, existingSessionId, sessionSummary) {
    var winId = generateWindowId();
    var pos = getNextWindowPosition();
    var size = getDefaultWindowSize();

    var el = buildWindowDOM(winId, title);
    el.style.left = pos.x + "px";
    el.style.top = pos.y + "px";
    el.style.width = size.width + "px";
    el.style.height = size.height + "px";

    $windowLayer.appendChild(el);

    // Wire up DOM refs within this window
    var $titlebar = el.querySelector(".chat-window-titlebar");
    var $messages = el.querySelector(".chat-window-messages");
    var $input = el.querySelector(".chat-input");
    var $send = el.querySelector(".chat-send-btn");
    var $stop = el.querySelector(".chat-stop-btn");
    var $cost = el.querySelector(".chat-window-cost");
    var $imagePreview = el.querySelector(".chat-image-preview");
    var $turnSidebar = el.querySelector(".chat-turn-sidebar");

    var winData = {
      id: winId,
      el: el,
      ws: null,
      sessionId: null,
      agent: agent,
      cwd: cwd,
      title: title,
      streaming: false,
      messageQueue: [],
      currentTextAccum: "",
      currentTextEl: null,
      thinkingAccum: "",
      thinkingEl: null,
      renderScheduled: false,
      sessionSummary: sessionSummary || null,
      userTurns: [],
      activeTurnIndex: -1,
      pendingImages: [],
      windowState: "floating",
      x: pos.x,
      y: pos.y,
      width: size.width,
      height: size.height,
      unreadCount: 0,
      // DOM refs scoped to this window
      $messages: $messages,
      $input: $input,
      $send: $send,
      $stop: $stop,
      $cost: $cost,
      $imagePreview: $imagePreview,
      $turnSidebar: $turnSidebar,
    };
    chatWindows[winId] = winData;

    // Title bar buttons
    el.querySelector(".win-minimize").addEventListener("click", function () {
      minimizeWindow(winId);
    });
    el.querySelector(".win-maximize").addEventListener("click", function () {
      maximizeWindow(winId);
    });
    el.querySelector(".win-close").addEventListener("click", function () {
      closeWindow(winId);
    });
    // Double-click title bar to maximize
    $titlebar.addEventListener("dblclick", function (e) {
      if (e.target.closest(".chat-window-btn")) return;
      maximizeWindow(winId);
    });

    // Click anywhere in window to focus
    el.addEventListener("mousedown", function () {
      focusWindow(winId);
    });

    // Drag
    initDrag(winId, $titlebar);

    // Resize handles
    initResize(winId, el.querySelector(".chat-resize-handle.right"), "right");
    initResize(winId, el.querySelector(".chat-resize-handle.bottom"), "bottom");
    initResize(winId, el.querySelector(".chat-resize-handle.corner"), "corner");

    // Input handling (scoped to this window)
    $input.addEventListener("input", function () {
      this.style.height = "auto";
      this.style.height = Math.min(this.scrollHeight, 120) + "px";
    });

    $input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendWindowMessage(winId);
      } else if (e.key === "Escape") {
        if (winData.streaming) {
          e.preventDefault();
          cancelWindowAgent(winId);
        }
      }
    });

    // Turn navigation (Ctrl+Up/Down)
    $input.addEventListener("keydown", function (e) {
      if (e.ctrlKey && e.key === "ArrowUp") {
        e.preventDefault();
        jumpToPreviousTurn(winData);
      } else if (e.ctrlKey && e.key === "ArrowDown") {
        e.preventDefault();
        jumpToNextTurn(winData);
      } else if (e.ctrlKey && e.key === "t") {
        e.preventDefault();
        toggleTurnSidebar(winData);
      }
    });

    $send.addEventListener("click", function () { sendWindowMessage(winId); });
    $stop.addEventListener("click", function () { cancelWindowAgent(winId); });

    // Image paste
    $input.addEventListener("paste", function (e) {
      if (winData.streaming) return;
      var items = e.clipboardData && e.clipboardData.items;
      if (!items) return;
      for (var i = 0; i < items.length; i++) {
        if (items[i].type.indexOf("image/") === 0) {
          e.preventDefault();
          var file = items[i].getAsFile();
          if (!file) continue;
          readImageFile(winData, file);
        }
      }
    });

    focusWindow(winId);
    renderDock();
    connectWebSocket(winData, existingSessionId);
    $input.focus();

    return winId;
  }

  // --- WebSocket ---

  function connectWebSocket(winData, sessionId) {
    var wsProtocol = location.protocol === "https:" ? "wss:" : "ws:";
    var wsUrl = wsProtocol + "//" + location.host + "/ws/chat";
    var ws = new WebSocket(wsUrl);
    winData.ws = ws;

    ws.onopen = function () {
      ws.send(JSON.stringify({
        type: "start",
        agent: winData.agent,
        cwd: winData.cwd,
        sessionId: sessionId || undefined,
      }));
    };

    ws.onmessage = function (evt) {
      try {
        var msg = JSON.parse(evt.data);
        handleServerMessage(winData, msg);
      } catch (e) {
        console.error("Failed to parse chat message:", e);
      }
    };

    ws.onclose = function () {
      winData.streaming = false;
      updateWindowInputState(winData);
      renderDock();
    };

    ws.onerror = function () {
      appendSystemMessage(winData, "Connection error");
    };
  }

  // --- Message Routing ---

  function handleServerMessage(winData, msg) {
    // Track unread for minimized windows
    if (winData.windowState === "minimized" && msg.type === "update") {
      winData.unreadCount++;
      renderDock();
    }

    switch (msg.type) {
      case "session_init":
        winData.sessionId = msg.sessionId;
        if (msg.agentInfo) {
          appendSystemMessage(winData, msg.agentInfo.title + " connected");
        }
        if (!msg.historyLoaded && winData.sessionSummary) {
          appendInfoBanner(winData, "Previous messages not available. " + winData.sessionSummary);
        }
        emitSessionEvent("agent-session-started", {
          sessionId: msg.sessionId,
          agent: winData.agent,
          cwd: winData.cwd,
          title: winData.title,
        });
        break;

      case "update":
        handleUpdate(winData, msg);
        break;

      case "turn_complete":
        finalizeAssistantMessage(winData);
        collapseCompletedTools(winData);
        winData.streaming = false;
        flushMessageQueue(winData);
        updateWindowInputState(winData);
        renderDock();
        emitSessionEvent("agent-session-updated", {
          sessionId: winData.sessionId,
          streaming: winData.streaming,
        });
        break;

      case "error":
        appendSystemMessage(winData, "Error: " + (msg.message || "Unknown error"));
        winData.streaming = false;
        updateWindowInputState(winData);
        renderDock();
        break;

      case "auth_required":
        appendAuthBanner(winData, msg);
        break;

      case "session_terminated":
        winData.streaming = false;
        winData.terminated = true;
        appendSessionTerminated(winData);
        updateWindowInputState(winData);
        renderDock();
        break;

      case "session_restarted":
        winData.terminated = false;
        winData.sessionId = msg.sessionId || winData.sessionId;
        appendSystemMessage(winData, "Session resumed");
        break;

      default:
        console.log("Unknown chat message type:", msg.type);
    }
  }

  function handleUpdate(winData, msg) {
    var su = msg.sessionUpdate;
    switch (su) {
      case "agent_message_chunk":
        var text = (msg.content && msg.content.text) || "";
        if (text) appendAgentText(winData, text);
        break;

      case "agent_thought_chunk":
        var thought = (msg.content && msg.content.text) || "";
        if (thought) appendThinking(winData, thought);
        break;

      case "user_message_chunk":
        var userText = (msg.content && msg.content.text) || "";
        if (userText) appendUserBubble(winData, userText);
        break;

      case "tool_call":
        renderToolCall(winData, msg);
        break;

      case "tool_call_update":
        updateToolCall(winData, msg);
        break;

      case "usage_update":
        renderUsage(winData, msg);
        break;

      case "plan":
        renderPlan(winData, msg.entries || []);
        break;

      default:
        break;
    }
  }

  // --- Rendering: User Messages ---

  function scrollToBottom(winData) {
    if (!winData.$messages) return;
    winData.$messages.scrollTop = winData.$messages.scrollHeight;
  }

  function appendUserBubble(winData, text, images) {
    finalizeAssistantMessage(winData);

    var turnIndex = winData.userTurns.length;
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
    winData.$messages.appendChild(bubble);

    winData.userTurns.push({ index: turnIndex, element: bubble, text: text || "(image)" });
    renderTurnSidebar(winData);
    scrollToBottom(winData);
  }

  // --- Rendering: Agent Text (streaming) ---

  function appendAgentText(winData, text) {
    finalizeThinking(winData);

    winData.currentTextAccum += text;
    if (!winData.currentTextEl) {
      var bubble = document.createElement("div");
      bubble.className = "chat-bubble assistant";
      var content = document.createElement("div");
      content.className = "chat-md-content";
      bubble.appendChild(content);
      winData.$messages.appendChild(bubble);
      winData.currentTextEl = content;
    }
    scheduleRender(winData);
  }

  function scheduleRender(winData) {
    if (winData.renderScheduled) return;
    winData.renderScheduled = true;
    requestAnimationFrame(function () {
      winData.renderScheduled = false;
      if (winData.currentTextEl) {
        winData.currentTextEl.innerHTML = renderMarkdown(winData.currentTextAccum);
      }
      scrollToBottom(winData);
    });
  }

  function finalizeAssistantMessage(winData) {
    if (winData.currentTextEl && winData.currentTextAccum) {
      winData.currentTextEl.innerHTML = renderMarkdown(winData.currentTextAccum);
    }
    winData.currentTextEl = null;
    winData.currentTextAccum = "";
    finalizeThinking(winData);
  }

  // --- Rendering: Thinking ---

  function appendThinking(winData, text) {
    winData.thinkingAccum += text;
    if (!winData.thinkingEl) {
      var block = document.createElement("details");
      block.className = "chat-thinking";
      block.innerHTML = "<summary>Thinking...</summary><div class='chat-thinking-content'></div>";
      winData.$messages.appendChild(block);
      winData.thinkingEl = block.querySelector(".chat-thinking-content");
    }
    winData.thinkingEl.textContent = winData.thinkingAccum;
    scrollToBottom(winData);
  }

  function finalizeThinking(winData) {
    winData.thinkingEl = null;
    winData.thinkingAccum = "";
  }

  // --- Rendering: Tool Calls ---

  function renderToolCall(winData, tc) {
    finalizeAssistantMessage(winData);

    var card = document.createElement("details");
    card.className = "chat-tool-card";
    card.setAttribute("data-tool-id", tc.toolCallId || "");

    var status = tc.status || "pending";
    var kind = tc.kind || "other";
    var icon = TOOL_ICONS[kind] || "&#128295;";
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

    winData.$messages.appendChild(card);
    scrollToBottom(winData);
  }

  function updateToolCall(winData, update) {
    var card = winData.$messages.querySelector(
      '.chat-tool-card[data-tool-id="' + (update.toolCallId || "") + '"]'
    );
    if (!card) return;

    if (update.status) {
      var badge = card.querySelector(".chat-tool-status");
      if (badge) {
        badge.textContent = update.status;
        badge.className = "chat-tool-status " + update.status;
      }
    }

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
    scrollToBottom(winData);
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
    pre.textContent = diff.newText || "";
    el.appendChild(pre);
    container.appendChild(el);
  }

  // --- Rendering: Plan ---

  function renderPlan(winData, entries) {
    var existing = winData.$messages.querySelector(".chat-plan");
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
    winData.$messages.appendChild(el);
    scrollToBottom(winData);
  }

  // --- Rendering: Usage ---

  function renderUsage(winData, msg) {
    var $cost = winData.$cost;
    if (!$cost) return;
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
      $cost.textContent = parts.join(" | ");
      $cost.classList.remove("hidden", "context-warn", "context-critical");
      if (msg.used != null && msg.size != null && msg.size > 0) {
        var pct2 = (msg.used / msg.size) * 100;
        if (pct2 >= 90) {
          $cost.classList.add("context-critical");
        } else if (pct2 >= 75) {
          $cost.classList.add("context-warn");
        }
      }
    }
  }

  // --- Turn Navigation Sidebar ---

  function renderTurnSidebar(winData) {
    var sidebar = winData.$turnSidebar;
    if (!sidebar) return;
    var list = sidebar.querySelector(".turn-list");
    if (list) list.innerHTML = "";
    var turns = winData.userTurns;
    if (turns.length === 0) {
      sidebar.classList.add("hidden");
      return;
    }

    sidebar.classList.remove("hidden");
    if (!list) return;

    turns.forEach(function (turn) {
      var item = document.createElement("div");
      item.className = "turn-item" + (turn.index === winData.activeTurnIndex ? " active" : "");
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
        jumpToTurn(winData, turn.index);
      });
      list.appendChild(item);
    });

    var counter = sidebar.querySelector(".turn-counter");
    if (counter) {
      var current = winData.activeTurnIndex >= 0 ? (winData.activeTurnIndex + 1) : "-";
      counter.textContent = current + " / " + turns.length;
    }
  }

  function jumpToTurn(winData, turnIndex) {
    var turns = winData.userTurns;
    if (turnIndex < 0 || turnIndex >= turns.length) return;

    winData.activeTurnIndex = turnIndex;
    var el = turns[turnIndex].element;
    el.scrollIntoView({ behavior: "smooth", block: "center" });

    el.classList.add("turn-highlight");
    setTimeout(function () { el.classList.remove("turn-highlight"); }, 1500);

    renderTurnSidebar(winData);
  }

  function jumpToPreviousTurn(winData) {
    if (!winData || winData.userTurns.length === 0) return;
    var next = winData.activeTurnIndex <= 0 ? 0 : winData.activeTurnIndex - 1;
    jumpToTurn(winData, next);
  }

  function jumpToNextTurn(winData) {
    if (!winData || winData.userTurns.length === 0) return;
    var max = winData.userTurns.length - 1;
    var next = winData.activeTurnIndex >= max ? max : winData.activeTurnIndex + 1;
    jumpToTurn(winData, next);
  }

  function toggleTurnSidebar(winData) {
    if (!winData.$turnSidebar) return;
    winData.$turnSidebar.classList.toggle("collapsed");
  }

  // --- Tool Collapsing ---

  function collapseCompletedTools(winData) {
    var container = winData.$messages;
    var children = Array.from(container.children);
    var i = 0;

    while (i < children.length) {
      if (isCompletedToolCard(children[i])) {
        var runStart = i;
        while (i < children.length && isCompletedToolCard(children[i])) {
          i++;
        }
        var runLength = i - runStart;
        if (runLength >= 3) {
          var details = container.ownerDocument.createElement("details");
          details.className = "chat-tool-group";
          var summary = container.ownerDocument.createElement("summary");
          summary.textContent = runLength + " tool calls completed";
          details.appendChild(summary);

          container.insertBefore(details, children[runStart]);
          for (var j = runStart; j < runStart + runLength; j++) {
            details.appendChild(children[j]);
          }
        }
      } else {
        i++;
      }
    }
  }

  function isCompletedToolCard(el) {
    if (!el || !el.classList || !el.classList.contains("chat-tool-card")) return false;
    var statusEl = el.querySelector(".chat-tool-status");
    return statusEl && statusEl.classList.contains("completed");
  }

  // --- Rendering: System / Info / Auth ---

  function appendSystemMessage(winData, text) {
    var el = document.createElement("div");
    el.className = "chat-system-msg";
    el.textContent = text;
    winData.$messages.appendChild(el);
    scrollToBottom(winData);
  }

  function appendInfoBanner(winData, text) {
    var el = document.createElement("div");
    el.className = "chat-info-banner";
    el.textContent = text;
    winData.$messages.appendChild(el);
    scrollToBottom(winData);
  }

  function appendSessionTerminated(winData) {
    var el = document.createElement("div");
    el.className = "chat-system-msg chat-terminated";
    el.textContent = "Session ended \u2014 send a message to resume";
    winData.$messages.appendChild(el);
    scrollToBottom(winData);
  }

  function appendAuthBanner(winData, msg) {
    var el = document.createElement("div");
    el.className = "chat-auth-banner";
    el.innerHTML =
      '<div class="chat-auth-text">' + escapeHtml(msg.message || "Authentication required") + "</div>" +
      '<button class="chat-auth-retry">Retry</button>';
    el.querySelector(".chat-auth-retry").addEventListener("click", function () {
      if (winData.ws && winData.ws.readyState === WebSocket.OPEN) {
        winData.ws.send(JSON.stringify({ type: "retry" }));
        el.parentNode.removeChild(el);
      }
    });
    winData.$messages.appendChild(el);
    scrollToBottom(winData);
  }

  // --- Input Handling ---

  function sendWindowMessage(winId) {
    var win = chatWindows[winId];
    if (!win) return;

    var text = win.$input.value.trim();
    var images = win.pendingImages.slice();
    if (!text && !images.length) return;
    win.$input.value = "";
    win.$input.style.height = "auto";

    appendUserBubble(win, text, images);
    win.pendingImages = [];
    renderImagePreview(win);
    updateInputPlaceholder(win);

    var queuedMsg = { type: "user_message", text: text };
    if (images.length) {
      queuedMsg.images = images.map(function (img) {
        return { data: img.data, mimeType: img.mimeType };
      });
    }

    if (win.streaming) {
      if (!win.messageQueue) win.messageQueue = [];
      win.messageQueue.push(queuedMsg);
      updateWindowInputState(win);
      return;
    }

    sendToAgent(win, queuedMsg);
  }

  function sendToAgent(win, msg) {
    if (win.ws && win.ws.readyState === WebSocket.OPEN) {
      win.ws.send(JSON.stringify(msg));
      win.streaming = true;
      updateWindowInputState(win);
      renderDock();
      emitSessionEvent("agent-session-updated", {
        sessionId: win.sessionId,
        streaming: true,
      });
    }
  }

  function flushMessageQueue(win) {
    if (!win.messageQueue || win.messageQueue.length === 0) return;
    var next = win.messageQueue.shift();
    sendToAgent(win, next);
  }

  function cancelWindowAgent(winId) {
    var win = chatWindows[winId];
    if (!win || !win.streaming) return;
    if (win.ws && win.ws.readyState === WebSocket.OPEN) {
      win.ws.send(JSON.stringify({ type: "cancel" }));
    }
    win.messageQueue = [];
    win.streaming = false;
    appendSystemMessage(win, "Cancelled by user");
    finalizeAssistantMessage(win);
    updateWindowInputState(win);
    renderDock();
  }

  function updateWindowInputState(winData) {
    var streaming = winData.streaming;
    var queued = winData.messageQueue && winData.messageQueue.length > 0;
    winData.$input.disabled = false;
    if (streaming) {
      winData.$send.classList.add("hidden");
      winData.$stop.classList.remove("hidden");
    } else {
      winData.$send.classList.remove("hidden");
      winData.$stop.classList.add("hidden");
    }
    if (streaming && queued) {
      winData.$input.placeholder = queued + " queued...";
    } else if (streaming) {
      winData.$input.placeholder = "Agent working... Esc to stop";
    } else if (winData.terminated) {
      winData.$input.placeholder = "Send a message to resume session...";
    } else {
      winData.$input.placeholder = "Send a message...";
    }
  }

  // --- Image Handling ---

  function readImageFile(winData, file) {
    var reader = new FileReader();
    reader.onload = function () {
      var parts = reader.result.split(",");
      var mimeType = file.type || "image/png";
      var data = parts[1];
      winData.pendingImages.push({ data: data, mimeType: mimeType });
      renderImagePreview(winData);
      updateInputPlaceholder(winData);
    };
    reader.readAsDataURL(file);
  }

  function updateInputPlaceholder(winData) {
    if (winData.streaming || winData.terminated) return;
    var n = winData.pendingImages.length;
    if (n > 0) {
      winData.$input.placeholder = n + " image" + (n > 1 ? "s" : "") + " attached \u2014 add text or press Enter to send";
    } else {
      winData.$input.placeholder = "Send a message...";
    }
  }

  function renderImagePreview(winData) {
    var $preview = winData.$imagePreview;
    $preview.innerHTML = "";
    if (!winData.pendingImages.length) {
      $preview.classList.remove("active");
      return;
    }
    $preview.classList.add("active");
    winData.pendingImages.forEach(function (img, idx) {
      var thumb = document.createElement("div");
      thumb.className = "image-preview-thumb";
      var imgEl = document.createElement("img");
      imgEl.src = "data:" + img.mimeType + ";base64," + img.data;
      thumb.appendChild(imgEl);
      var removeBtn = document.createElement("button");
      removeBtn.className = "image-preview-remove";
      removeBtn.textContent = "\u00d7";
      removeBtn.addEventListener("click", function () {
        winData.pendingImages.splice(idx, 1);
        renderImagePreview(winData);
        updateInputPlaceholder(winData);
      });
      thumb.appendChild(removeBtn);
      $preview.appendChild(thumb);
    });
  }

  // --- Custom Events ---

  function emitSessionEvent(eventName, detail) {
    window.dispatchEvent(new CustomEvent(eventName, { detail: detail }));
  }

  // --- Public API (called from app.js) ---

  window.AgentChat = {
    getActiveSessionIds: function () {
      var ids = new Set();
      Object.keys(chatWindows).forEach(function (id) {
        var win = chatWindows[id];
        if (win.sessionId) ids.add(win.sessionId);
      });
      return ids;
    },

    getActiveSessions: function () {
      var sessions = {};
      Object.keys(chatWindows).forEach(function (id) {
        var win = chatWindows[id];
        if (win.sessionId) {
          sessions[win.sessionId] = {
            streaming: win.streaming,
            agent: win.agent,
            cwd: win.cwd,
            title: win.title,
          };
        }
      });
      return sessions;
    },

    openChat: function (session) {
      // Check for existing window with same session
      var ids = Object.keys(chatWindows);
      for (var i = 0; i < ids.length; i++) {
        var win = chatWindows[ids[i]];
        if (win.sessionId === session.id) {
          if (win.windowState === "minimized") {
            restoreWindow(ids[i]);
          } else {
            focusWindow(ids[i]);
          }
          return;
        }
      }
      var agent = session.source || "claude";
      var title = session.summary || session.id.substring(0, 8);
      createChatWindow(title, agent, session.cwd, session.id, session.summary);
    },

    openNewChat: function (cwd, agent) {
      agent = agent || localStorage.getItem("ak-default-agent") || "claude";
      var displayName = cwd.split("/").filter(Boolean).pop() || cwd;
      createChatWindow("New: " + displayName, agent, cwd, null, null);
    },
  };

  // --- Test Internals ---

  window._chatInternals = {
    handleServerMessage: handleServerMessage,
    handleUpdate: handleUpdate,
    appendAgentText: appendAgentText,
    appendUserBubble: appendUserBubble,
    finalizeAssistantMessage: finalizeAssistantMessage,
    renderToolCall: renderToolCall,
    collapseCompletedTools: collapseCompletedTools,
    sendWindowMessage: sendWindowMessage,
    updateWindowInputState: updateWindowInputState,
    focusWindow: focusWindow,
    minimizeWindow: minimizeWindow,
    restoreWindow: restoreWindow,
    maximizeWindow: maximizeWindow,
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
      return { chatWindows: chatWindows, focusedWindowId: focusedWindowId };
    },
    createWinData: function (messagesContainer) {
      var inputEl = messagesContainer.ownerDocument.createElement("textarea");
      var sendEl = messagesContainer.ownerDocument.createElement("button");
      var stopEl = messagesContainer.ownerDocument.createElement("button");
      stopEl.classList.add("hidden");
      var costEl = messagesContainer.ownerDocument.createElement("span");
      costEl.classList.add("hidden");
      var previewEl = messagesContainer.ownerDocument.createElement("div");
      var sidebarEl = messagesContainer.ownerDocument.createElement("div");
      sidebarEl.classList.add("hidden");
      sidebarEl.innerHTML = '<div class="turn-sidebar-header"><span class="turn-sidebar-title">Turns</span><span class="turn-counter">-</span></div><div class="turn-list"></div>';

      return {
        id: "test-win",
        el: messagesContainer.ownerDocument.createElement("div"),
        ws: null,
        sessionId: null,
        agent: "claude",
        cwd: "/tmp",
        title: "Test",
        streaming: false,
        messageQueue: [],
        currentTextAccum: "",
        currentTextEl: null,
        thinkingAccum: "",
        thinkingEl: null,
        renderScheduled: false,
        sessionSummary: null,
        userTurns: [],
        activeTurnIndex: -1,
        pendingImages: [],
        windowState: "floating",
        x: 0, y: 0, width: 500, height: 400,
        unreadCount: 0,
        $messages: messagesContainer,
        $input: inputEl,
        $send: sendEl,
        $stop: stopEl,
        $cost: costEl,
        $imagePreview: previewEl,
        $turnSidebar: sidebarEl,
      };
    },
  };
})();
