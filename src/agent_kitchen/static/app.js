// ABOUTME: Dashboard client — fetches session data, renders UI, handles interactions.
// ABOUTME: Vanilla JS with no build step; polls /api/sessions for updates.

(function () {
  "use strict";

  // --- State ---
  let dashboardData = null;
  let lastScannedTime = null;
  let expandedRepos = new Set(); // repo_root values that are expanded
  var timeFilterDays = Infinity; // max days back to show (Infinity = all)

  // --- DOM refs ---
  const $loading = document.getElementById("loading");
  const $repoGroups = document.getElementById("repo-groups");
  const $nonRepoGroups = document.getElementById("non-repo-groups");
  const $chronoView = document.getElementById("chrono-view");
  const $emptyState = document.getElementById("empty-state");
  const $lastScan = document.getElementById("last-scan");
  const $btnRefresh = document.getElementById("btn-refresh");
  const $scanDays = document.getElementById("scan-days");
  const $timeSegments = document.getElementById("time-segments");
  const $searchOverlay = document.getElementById("search-overlay");
  const $searchInput = document.getElementById("search-input");
  const $searchResults = document.getElementById("search-results");

  // --- Helpers ---

  function timeAgo(isoString) {
    if (!isoString) return "--";
    const diff = Date.now() - new Date(isoString).getTime();
    const seconds = Math.floor(diff / 1000);
    if (seconds < 60) return seconds + "s ago";
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return minutes + "m ago";
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return hours + "h ago";
    const days = Math.floor(hours / 24);
    return days + "d ago";
  }

  function statusCssClass(status) {
    return (status || "").replace(/\s+/g, "-");
  }

  function isActiveDot(status) {
    return ["in progress", "likely in progress", "waiting for input"].includes(status);
  }

  var INITIAL_VISIBLE = 10;

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str || "";
    return div.innerHTML;
  }

  function sessionLabel(session) {
    if (session.summary) return session.summary;
    if (session.slug) return session.slug;
    return session.id.substring(0, 8) + "\u2026";
  }

  function allSessions() {
    if (!dashboardData) return [];
    var sessions = [];
    (dashboardData.repo_groups || []).forEach(function (g) {
      g.sessions.forEach(function (s) {
        sessions.push({ session: s, repoName: g.repo_name || "" });
      });
    });
    (dashboardData.non_repo_groups || []).forEach(function (g) {
      var name = g.cwd.split("/").filter(Boolean).pop() || g.cwd;
      g.sessions.forEach(function (s) {
        sessions.push({ session: s, repoName: name });
      });
    });
    return sessions;
  }

  function fuzzyMatch(text, query) {
    var tLower = text.toLowerCase();
    var qLower = query.toLowerCase();
    var qi = 0;
    var indices = [];
    for (var ti = 0; ti < tLower.length && qi < qLower.length; ti++) {
      if (tLower[ti] === qLower[qi]) {
        indices.push(ti);
        qi++;
      }
    }
    if (qi < qLower.length) return null;
    // Score: prefer consecutive matches and matches at word boundaries
    var score = 0;
    for (var i = 0; i < indices.length; i++) {
      if (i > 0 && indices[i] === indices[i - 1] + 1) score += 10;
      if (indices[i] === 0 || text[indices[i] - 1] === " ") score += 5;
    }
    return { indices: indices, score: score };
  }

  function highlightMatches(text, indices) {
    if (!indices || indices.length === 0) return escapeHtml(text);
    var result = "";
    var idxSet = new Set(indices);
    var inMark = false;
    for (var i = 0; i < text.length; i++) {
      if (idxSet.has(i) && !inMark) {
        result += "<mark>";
        inMark = true;
      } else if (!idxSet.has(i) && inMark) {
        result += "</mark>";
        inMark = false;
      }
      result += escapeHtml(text[i]);
    }
    if (inMark) result += "</mark>";
    return result;
  }

  function parsePhaseDate(phase) {
    // Use start_date (ISO) if available, otherwise parse the period string
    if (phase.start_date) {
      return new Date(phase.start_date + "T00:00:00").getTime();
    }
    var period = phase.period || "";
    var now = new Date();
    if (period === "Today") return new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    if (period === "Yesterday") return new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1).getTime();
    // Parse "Mar 17" or "Mar 10-12" (use first date in range)
    var months = {Jan:0,Feb:1,Mar:2,Apr:3,May:4,Jun:5,Jul:6,Aug:7,Sep:8,Oct:9,Nov:10,Dec:11};
    var m = period.match(/^([A-Z][a-z]{2})\s+(\d+)/);
    if (m && months[m[1]] !== undefined) {
      var year = now.getFullYear();
      var d = new Date(year, months[m[1]], parseInt(m[2], 10));
      // If the parsed date is in the future, it's from last year
      if (d.getTime() > now.getTime() + 86400000) d.setFullYear(year - 1);
      return d.getTime();
    }
    return 0; // can't parse — don't filter
  }

  function filterSessionByTime(session) {
    if (timeFilterDays === Infinity) return true;
    if (!session.last_active) return false;
    var cutoff = Date.now() - timeFilterDays * 24 * 60 * 60 * 1000;
    return new Date(session.last_active).getTime() >= cutoff;
  }

  function filterGroupSessions(group) {
    return group.sessions.filter(filterSessionByTime);
  }

  // --- Rendering ---

  function isSessionFresh(session) {
    if (!session.last_active) return false;
    var hourAgo = Date.now() - 60 * 60 * 1000;
    return new Date(session.last_active).getTime() >= hourAgo;
  }

  function renderSessionRow(session) {
    const cssCls = statusCssClass(session.status);
    const row = document.createElement("div");
    row.className = "session-row" + (isSessionFresh(session) ? " session-fresh" : "");
    row.innerHTML =
      '<div class="status-dot ' + cssCls + '"></div>' +
      '<div class="session-summary">' + escapeHtml(sessionLabel(session)) + "</div>" +
      '<span class="status-label ' + cssCls + '">' + escapeHtml(session.status) + "</span>" +
      '<span class="time-ago">' + timeAgo(session.last_active) + "</span>" +
      '<span class="source-badge ' + session.source + '">' + session.source + "</span>";

    row.addEventListener("click", function () {
      launchSession(session, row);
    });

    return row;
  }

  // Octocat SVG icon for git repo groups
  var OCTOCAT_SVG = '<svg class="octocat-icon" viewBox="0 0 16 16" width="14" height="14" fill="currentColor">' +
    '<path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49' +
    '-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82' +
    '.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15' +
    '-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82' +
    ' 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48' +
    ' 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>';

  function buildGroupHeaderHtml(opts, isExpanded, sessionCount) {
    var chevron = '<span class="repo-chevron ' + (isExpanded ? "expanded" : "") + '" aria-hidden="true">\u25B6</span>';
    var icon = opts.icon || "";
    var name = '<span class="repo-name-display">' + escapeHtml(opts.displayName) + "</span>";
    var meta = '<span class="repo-meta-line">' + opts.metaText + "</span>";
    var btn = '<button class="btn-new-session" title="New Claude session in ' + escapeHtml(opts.cwd) + '" aria-label="New session in ' + escapeHtml(opts.displayName) + '">+</button>';

    return (
      '<div class="repo-header-left">' + chevron + icon +
        '<div class="repo-header-info">' + name + meta + '</div>' +
      "</div>" +
      '<div class="repo-header-right">' + btn + timeAgo(opts.lastActive) + "</div>"
    );
  }

  function renderGroup(opts, filteredSessions) {
    if (filteredSessions.length === 0) return null;

    var groupKey = opts.groupKey;
    var isExpanded = expandedRepos.has(groupKey);
    var container = document.createElement("div");
    container.className = "repo-group";

    var header = document.createElement("div");
    header.className = "repo-header";
    header.setAttribute("role", "button");
    header.setAttribute("aria-expanded", isExpanded ? "true" : "false");
    header.setAttribute("tabindex", "0");
    header.innerHTML = buildGroupHeaderHtml(opts, isExpanded, filteredSessions.length);

    var sessionList = document.createElement("div");
    sessionList.className = "session-list" + (isExpanded ? "" : " collapsed");

    // Render timeline if present, filtered by time slider
    if (opts.timeline && opts.timeline.length > 0) {
      var timelineEl = document.createElement("div");
      timelineEl.className = "repo-timeline";
      var cutoff = timeFilterDays === Infinity ? 0 : Date.now() - timeFilterDays * 24 * 60 * 60 * 1000;
      opts.timeline.forEach(function (phase) {
        if (cutoff > 0) {
          var phaseTime = parsePhaseDate(phase);
          if (phaseTime > 0 && phaseTime < cutoff) return;
        }
        var phaseEl = document.createElement("div");
        phaseEl.className = "timeline-phase";
        phaseEl.innerHTML =
          '<span class="timeline-period">' + escapeHtml(phase.period) + '</span>' +
          '<span class="timeline-desc">' + escapeHtml(phase.description) + '</span>';
        timelineEl.appendChild(phaseEl);
      });
      if (timelineEl.children.length > 0) {
        sessionList.appendChild(timelineEl);
      }
    }

    var showingAll = filteredSessions.length <= INITIAL_VISIBLE;

    function renderVisibleSessions() {
      var timeline = sessionList.querySelector(".repo-timeline");
      sessionList.innerHTML = "";
      if (timeline) sessionList.appendChild(timeline);

      var toShow = showingAll ? filteredSessions : filteredSessions.slice(0, INITIAL_VISIBLE);
      toShow.forEach(function (session) {
        sessionList.appendChild(renderSessionRow(session));
      });
      if (!showingAll) {
        var moreBtn = document.createElement("div");
        moreBtn.className = "show-more-btn";
        moreBtn.textContent = "Show all " + filteredSessions.length + " sessions";
        moreBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          showingAll = true;
          renderVisibleSessions();
          sessionList.style.maxHeight = sessionList.scrollHeight + "px";
        });
        sessionList.appendChild(moreBtn);
      }
    }
    renderVisibleSessions();

    header.querySelector(".btn-new-session").addEventListener("click", function (e) {
      e.stopPropagation();
      openNewSession(opts.cwd);
    });

    header.addEventListener("click", function () {
      if (expandedRepos.has(groupKey)) {
        expandedRepos.delete(groupKey);
        sessionList.classList.add("collapsed");
        header.querySelector(".repo-chevron").classList.remove("expanded");
        header.setAttribute("aria-expanded", "false");
      } else {
        expandedRepos.add(groupKey);
        sessionList.classList.remove("collapsed");
        sessionList.style.maxHeight = sessionList.scrollHeight + "px";
        header.querySelector(".repo-chevron").classList.add("expanded");
        header.setAttribute("aria-expanded", "true");
      }
    });

    if (isExpanded) {
      requestAnimationFrame(function () {
        sessionList.style.maxHeight = sessionList.scrollHeight + "px";
      });
    }

    container.appendChild(header);
    container.appendChild(sessionList);
    return container;
  }

  function renderRepoGroup(group, filteredSessions) {
    var metaParts = [];
    if (group.git_branch) metaParts.push(group.git_branch);
    if (group.git_dirty) metaParts.push('<span class="dirty">dirty</span>');
    if (group.unpushed_commits > 0) metaParts.push('<span class="unpushed">' + group.unpushed_commits + " unpushed</span>");
    if (!group.git_dirty && group.unpushed_commits === 0) metaParts.push("clean");
    metaParts.push(filteredSessions.length + " sessions");

    return renderGroup({
      groupKey: group.repo_root,
      displayName: group.repo_name,
      icon: OCTOCAT_SVG,
      metaText: metaParts.join(", "),
      cwd: group.repo_root,
      lastActive: group.last_active,
      timeline: group.timeline,
    }, filteredSessions);
  }

  function renderNonRepoGroup(group, filteredSessions) {
    var displayName = group.cwd.split("/").filter(Boolean).pop() || group.cwd;

    return renderGroup({
      groupKey: group.cwd,
      displayName: displayName,
      icon: "",
      metaText: filteredSessions.length + " sessions",
      cwd: group.cwd,
      lastActive: group.last_active,
      timeline: group.timeline,
    }, filteredSessions);
  }

  function render() {
    if (!dashboardData) return;

    $loading.classList.add("hidden");

    var hasRepos = (dashboardData.repo_groups || []).length > 0;
    var hasNonRepos = (dashboardData.non_repo_groups || []).length > 0;

    if (!hasRepos && !hasNonRepos) {
      $emptyState.classList.remove("hidden");
      $repoGroups.classList.add("hidden");
      $nonRepoGroups.classList.add("hidden");
      $chronoView.classList.add("hidden");
      return;
    }

    $emptyState.classList.add("hidden");
    $chronoView.classList.add("hidden");
    $repoGroups.classList.remove("hidden");
    $nonRepoGroups.classList.add("hidden");

    // Combine repo and non-repo groups into one list sorted by last_active
    var allGroups = [];
    (dashboardData.repo_groups || []).forEach(function (g) {
      allGroups.push({ type: "repo", group: g, last_active: g.last_active });
    });
    (dashboardData.non_repo_groups || []).forEach(function (g) {
      allGroups.push({ type: "non-repo", group: g, last_active: g.last_active });
    });
    allGroups.sort(function (a, b) {
      return new Date(b.last_active) - new Date(a.last_active);
    });

    // Auto-expand the first group on initial render
    if (expandedRepos.size === 0 && allGroups.length > 0) {
      var firstGroup = allGroups[0];
      var expandKey = firstGroup.type === "repo" ? firstGroup.group.repo_root : firstGroup.group.cwd;
      expandedRepos.add(expandKey);
    }

    $repoGroups.innerHTML = "";
    allGroups.forEach(function (entry) {
      var filtered = filterGroupSessions(entry.group);
      var el = entry.type === "repo"
        ? renderRepoGroup(entry.group, filtered)
        : renderNonRepoGroup(entry.group, filtered);
      if (el) $repoGroups.appendChild(el);
    });
  }

  // --- API ---

  function fetchSessions() {
    return fetch("/api/sessions")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        dashboardData = data;
        lastScannedTime = data.last_scanned;
        render();
        updateLastScan();
      })
      .catch(function (err) {
        console.error("Failed to fetch sessions:", err);
      });
  }

  function refreshSessions() {
    var days = parseInt($scanDays.value, 10) || 7;
    $btnRefresh.classList.add("spinning");
    $btnRefresh.textContent = "Refreshing...";
    return fetch("/api/refresh?scan_days=" + days)
      .then(function (res) { return res.json(); })
      .then(function (data) {
        dashboardData = data;
        lastScannedTime = data.last_scanned;
        render();
        updateLastScan();
      })
      .catch(function (err) {
        console.error("Failed to refresh:", err);
      })
      .finally(function () {
        $btnRefresh.classList.remove("spinning");
        $btnRefresh.textContent = "Refresh";
      });
  }

  function launchSession(session, rowEl) {
    rowEl.classList.add("launched");
    setTimeout(function () { rowEl.classList.remove("launched"); }, 500);
    var mode = localStorage.getItem("ak-view-mode") || "chat";
    if (mode === "chat" && window.AgentChat) {
      window.AgentChat.openChat(session);
    } else {
      openTerminal(session);
    }
  }

  // --- Terminal Tabs ---

  var terminals = {};       // tabId → tabData
  var activeTabId = null;
  var tabIdCounter = 0;
  var resizeListenerAttached = false;

  var $terminalPanel = document.getElementById("terminal-panel");
  var $terminalContainer = document.getElementById("terminal-container");
  var $terminalTabs = document.getElementById("terminal-tabs");
  var $terminalClose = document.getElementById("terminal-close");

  // Re-fit active terminal on any container size change (panel open/close,
  // tab switch, browser resize, etc.)
  var terminalResizeObserver = new ResizeObserver(function () {
    if (activeTabId && terminals[activeTabId]) {
      terminals[activeTabId].fitAddon.fit();
    }
  });
  terminalResizeObserver.observe($terminalContainer);

  function generateTabId() {
    return "tab-" + (tabIdCounter++);
  }

  function renderTabs() {
    $terminalTabs.innerHTML = "";
    var ids = Object.keys(terminals);
    ids.forEach(function (tabId) {
      var tab = terminals[tabId];
      var el = document.createElement("div");
      el.className = "terminal-tab" + (tabId === activeTabId ? " active" : "") + (tab.ended ? " ended" : "");
      el.innerHTML =
        '<span class="terminal-tab-title">' + escapeHtml(tab.title) + '</span>' +
        '<span class="terminal-tab-close">&times;</span>';

      el.querySelector(".terminal-tab-title").addEventListener("click", function () {
        switchTab(tabId);
      });
      el.querySelector(".terminal-tab-close").addEventListener("click", function (e) {
        e.stopPropagation();
        closeTab(tabId);
      });

      $terminalTabs.appendChild(el);
    });
  }

  function switchTab(tabId) {
    if (!terminals[tabId]) return;
    if (activeTabId && terminals[activeTabId]) {
      terminals[activeTabId].container.classList.remove("active");
    }
    activeTabId = tabId;
    terminals[tabId].container.classList.add("active");
    terminals[tabId].fitAddon.fit();
    terminals[tabId].term.focus();
    renderTabs();
  }

  function closeTab(tabId) {
    var tab = terminals[tabId];
    if (!tab) return;
    tab.ws.close();
    tab.term.dispose();
    tab.container.remove();
    delete terminals[tabId];

    if (activeTabId === tabId) {
      var remaining = Object.keys(terminals);
      if (remaining.length > 0) {
        switchTab(remaining[remaining.length - 1]);
      } else {
        activeTabId = null;
        $terminalPanel.classList.add("hidden");
        document.body.classList.remove("terminal-open");
        window.removeEventListener("resize", handleTerminalResize);
        resizeListenerAttached = false;
      }
    }
    renderTabs();
  }

  function createTerminalTab(title, wsParams, sessionId) {
    var tabId = generateTabId();

    var container = document.createElement("div");
    container.className = "terminal-tab-container";
    $terminalContainer.appendChild(container);

    var term = new Terminal({
      fontFamily: '"JetBrains Mono", "SF Mono", monospace',
      fontSize: 13,
      theme: {
        background: "#111111",
        foreground: "#FAFAFA",
        cursor: "#FF4D00",
      },
      cursorBlink: true,
    });

    var fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(new WebLinksAddon.WebLinksAddon());

    term.open(container);

    var tabData = {
      id: tabId,
      term: term,
      ws: null,
      fitAddon: fitAddon,
      container: container,
      title: title,
      ended: false,
      sessionId: sessionId,
    };
    terminals[tabId] = tabData;

    $terminalPanel.classList.remove("hidden");
    document.body.classList.add("terminal-open");

    // Hide previous active tab, show this one
    if (activeTabId && terminals[activeTabId]) {
      terminals[activeTabId].container.classList.remove("active");
    }
    activeTabId = tabId;
    container.classList.add("active");

    // Defer fit + WebSocket connection until container is fully laid out.
    // Double-rAF ensures the browser has completed layout after display change.
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        fitAddon.fit();

        // Pass terminal dimensions as query params so the PTY spawns at the
        // correct size from the start (avoids garbled initial render).
        var params = new URLSearchParams(wsParams);
        params.set("cols", term.cols);
        params.set("rows", term.rows);
        var wsProtocol = location.protocol === "https:" ? "wss:" : "ws:";
        var wsUrl =
          wsProtocol + "//" + location.host + "/ws/terminal?" + params.toString();
        var ws = new WebSocket(wsUrl);
        tabData.ws = ws;

        ws.onopen = function () {
          ws.send(
            JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows })
          );
          term.focus();
        };

        ws.onmessage = function (evt) {
          term.write(evt.data);
        };

        ws.onclose = function () {
          term.write("\r\n\x1b[90m[session ended]\x1b[0m\r\n");
          tabData.ended = true;
          renderTabs();
        };

        term.onData(function (data) {
          if (ws.readyState === WebSocket.OPEN) ws.send(data);
        });

        term.onResize(function (size) {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(
              JSON.stringify({ type: "resize", cols: size.cols, rows: size.rows })
            );
          }
        });
      });
    });

    if (!resizeListenerAttached) {
      window.addEventListener("resize", handleTerminalResize);
      resizeListenerAttached = true;
    }

    renderTabs();
    return tabId;
  }

  function openTerminal(session) {
    // If this session already has a tab, switch to it
    var ids = Object.keys(terminals);
    for (var i = 0; i < ids.length; i++) {
      if (terminals[ids[i]].sessionId === session.id) {
        switchTab(ids[i]);
        return;
      }
    }
    createTerminalTab(sessionLabel(session), {
      source: session.source,
      session_id: session.id,
      cwd: session.cwd,
    }, session.id);
  }

  function openNewSession(cwd) {
    var mode = localStorage.getItem("ak-view-mode") || "chat";
    if (mode === "chat" && window.AgentChat) {
      window.AgentChat.openNewChat(cwd);
    } else {
      var displayName = cwd.split("/").filter(Boolean).pop() || cwd;
      createTerminalTab("New: " + displayName, { mode: "new", cwd: cwd }, null);
    }
  }

  function handleTerminalResize() {
    if (activeTabId && terminals[activeTabId]) {
      terminals[activeTabId].fitAddon.fit();
    }
  }

  $terminalClose.addEventListener("click", function () {
    if (activeTabId) closeTab(activeTabId);
  });

  // --- Last scan timer ---

  function updateLastScan() {
    if (lastScannedTime) {
      $lastScan.textContent = "Last scan: " + timeAgo(lastScannedTime);
    }
  }

  // --- Time Segment Buttons ---

  $timeSegments.addEventListener("click", function (e) {
    var btn = e.target.closest(".time-seg");
    if (!btn) return;
    var days = parseInt(btn.getAttribute("data-days"), 10);
    timeFilterDays = days === 0 ? Infinity : days;

    $timeSegments.querySelectorAll(".time-seg").forEach(function (b) {
      b.classList.remove("active");
    });
    btn.classList.add("active");
    render();
  });

  // --- Search Overlay ---

  var searchSelectedIdx = 0;
  var searchMatches = [];
  var previouslyFocusedElement = null;

  function openSearch() {
    previouslyFocusedElement = document.activeElement;
    $searchOverlay.classList.remove("hidden");
    $searchInput.value = "";
    $searchResults.innerHTML = "";
    searchSelectedIdx = 0;
    searchMatches = [];
    $searchInput.focus();
  }

  function closeSearch() {
    $searchOverlay.classList.add("hidden");
    $searchInput.value = "";
    $searchResults.innerHTML = "";
    if (previouslyFocusedElement && previouslyFocusedElement.focus) {
      previouslyFocusedElement.focus();
    }
    previouslyFocusedElement = null;
  }

  function performSearch(query) {
    $searchResults.innerHTML = "";
    searchSelectedIdx = 0;

    if (!query.trim()) {
      searchMatches = [];
      return;
    }

    var all = allSessions();
    var scored = [];
    all.forEach(function (entry) {
      var label = sessionLabel(entry.session);
      var searchText = label + " " + entry.repoName + " " + (entry.session.status || "");
      var m = fuzzyMatch(searchText, query);
      if (m) {
        // Re-match just the label for highlighting
        var labelMatch = fuzzyMatch(label, query);
        scored.push({
          session: entry.session,
          repoName: entry.repoName,
          score: m.score,
          labelIndices: labelMatch ? labelMatch.indices : [],
        });
      }
    });

    scored.sort(function (a, b) { return b.score - a.score; });
    searchMatches = scored.slice(0, 50);

    if (searchMatches.length === 0) {
      $searchResults.innerHTML = '<div class="search-no-results">No matches</div>';
      return;
    }

    searchMatches.forEach(function (match, idx) {
      var row = document.createElement("div");
      row.className = "search-result-row" + (idx === 0 ? " selected" : "");
      var cssCls = statusCssClass(match.session.status);
      row.innerHTML =
        '<div class="status-dot ' + cssCls + '"></div>' +
        '<div>' +
          '<div class="search-result-summary">' +
            highlightMatches(sessionLabel(match.session), match.labelIndices) +
          '</div>' +
          '<span class="search-result-repo">' + escapeHtml(match.repoName) + '</span>' +
        '</div>' +
        '<span class="status-label ' + cssCls + '">' +
          escapeHtml(match.session.status) + '</span>' +
        '<span class="time-ago">' + timeAgo(match.session.last_active) + '</span>';

      row.addEventListener("click", function () {
        closeSearch();
        launchFromSearch(match.session);
      });
      $searchResults.appendChild(row);
    });
  }

  function launchFromSearch(session) {
    openTerminal(session);
  }

  function updateSearchSelection() {
    var rows = $searchResults.querySelectorAll(".search-result-row");
    rows.forEach(function (r, i) {
      r.classList.toggle("selected", i === searchSelectedIdx);
    });
    if (rows[searchSelectedIdx]) {
      rows[searchSelectedIdx].scrollIntoView({ block: "nearest" });
    }
  }

  $searchInput.addEventListener("input", function () {
    performSearch($searchInput.value);
  });

  $searchInput.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      e.preventDefault();
      closeSearch();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (searchMatches.length > 0) {
        searchSelectedIdx = Math.min(searchSelectedIdx + 1, searchMatches.length - 1);
        updateSearchSelection();
      }
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (searchMatches.length > 0) {
        searchSelectedIdx = Math.max(searchSelectedIdx - 1, 0);
        updateSearchSelection();
      }
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (searchMatches.length > 0 && searchMatches[searchSelectedIdx]) {
        closeSearch();
        launchFromSearch(searchMatches[searchSelectedIdx].session);
      }
    }
  });

  $searchOverlay.addEventListener("click", function (e) {
    if (e.target === $searchOverlay) closeSearch();
  });

  // --- Keyboard Help Overlay ---

  var $helpOverlay = document.getElementById("help-overlay");

  function openHelp() {
    $helpOverlay.classList.remove("hidden");
  }

  function closeHelp() {
    $helpOverlay.classList.add("hidden");
  }

  $helpOverlay.addEventListener("click", function (e) {
    if (e.target === $helpOverlay) closeHelp();
  });

  // --- Group Navigation (j/k) ---

  var focusedGroupIdx = -1;

  function getAllGroupElements() {
    return Array.from($repoGroups.querySelectorAll(".repo-group"));
  }

  function setGroupFocus(idx) {
    var groups = getAllGroupElements();
    // Clear previous focus
    groups.forEach(function (el) {
      el.classList.remove("group-focused");
    });
    if (idx < 0 || idx >= groups.length) {
      focusedGroupIdx = -1;
      return;
    }
    focusedGroupIdx = idx;
    groups[idx].classList.add("group-focused");
    groups[idx].scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function toggleFocusedGroup() {
    var groups = getAllGroupElements();
    if (focusedGroupIdx < 0 || focusedGroupIdx >= groups.length) return;
    var header = groups[focusedGroupIdx].querySelector(".repo-header");
    if (header) header.click();
  }

  // --- Global Keyboard Handler ---

  document.addEventListener("keydown", function (e) {
    // Don't trigger if typing in an input or textarea
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

    // Help overlay takes priority for close
    if (!$helpOverlay.classList.contains("hidden")) {
      if (e.key === "Escape" || e.key === "?") {
        e.preventDefault();
        closeHelp();
      }
      return;
    }

    // Escape closes active terminal tab (returns focus to dashboard)
    if (e.key === "Escape" && activeTabId) {
      e.preventDefault();
      closeTab(activeTabId);
      return;
    }

    // When terminal is focused, only Escape works (handled above)
    if (activeTabId) return;

    if (e.key === "/") {
      e.preventDefault();
      openSearch();
    } else if (e.key === "?") {
      e.preventDefault();
      openHelp();
    } else if (e.key === "r") {
      e.preventDefault();
      refreshSessions();
    } else if (e.key === "j") {
      e.preventDefault();
      var groups = getAllGroupElements();
      if (groups.length > 0) {
        setGroupFocus(Math.min(focusedGroupIdx + 1, groups.length - 1));
      }
    } else if (e.key === "k") {
      e.preventDefault();
      if (focusedGroupIdx > 0) {
        setGroupFocus(focusedGroupIdx - 1);
      }
    } else if (e.key === "Enter" && focusedGroupIdx >= 0) {
      e.preventDefault();
      toggleFocusedGroup();
    }
  });

  // --- Event listeners ---

  $btnRefresh.addEventListener("click", refreshSessions);

  // --- Init ---

  fetchSessions();
})();
