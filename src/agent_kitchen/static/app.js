// ABOUTME: Dashboard client — fetches session data, renders UI, handles interactions.
// ABOUTME: Vanilla JS with no build step; polls /api/sessions for updates.

(function () {
  "use strict";

  // --- State ---
  let dashboardData = null;
  let lastScannedTime = null;
  let expandedRepos = new Set(); // repo_root values that are expanded

  // --- DOM refs ---
  const $loading = document.getElementById("loading");
  const $repoGroups = document.getElementById("repo-groups");
  const $nonRepoGroups = document.getElementById("non-repo-groups");
  const $chronoView = document.getElementById("chrono-view");
  const $emptyState = document.getElementById("empty-state");
  const $lastScan = document.getElementById("last-scan");
  const $btnRefresh = document.getElementById("btn-refresh");
  const $scanDays = document.getElementById("scan-days");

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

  // --- Rendering ---

  function renderSessionRow(session) {
    const cssCls = statusCssClass(session.status);
    const row = document.createElement("div");
    row.className = "session-row";
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

  function renderRepoGroup(group) {
    if (group.sessions.length === 0) return null;

    var isExpanded = expandedRepos.has(group.repo_root);
    var container = document.createElement("div");
    container.className = "repo-group";

    // Git meta string
    var metaParts = [];
    if (group.git_branch) metaParts.push(group.git_branch);
    if (group.git_dirty) metaParts.push('<span class="dirty">dirty</span>');
    if (group.unpushed_commits > 0) metaParts.push('<span class="unpushed">' + group.unpushed_commits + " unpushed</span>");
    if (!group.git_dirty && group.unpushed_commits === 0) metaParts.push("clean");
    metaParts.push(group.sessions.length + " sessions");

    var header = document.createElement("div");
    header.className = "repo-header";
    header.innerHTML =
      '<div class="repo-header-left">' +
      '<span class="repo-chevron ' + (isExpanded ? "expanded" : "") + '">\u25B6</span>' +
      OCTOCAT_SVG +
      '<span class="repo-name">' + escapeHtml(group.repo_name) + "</span>" +
      '<span class="repo-meta">(' + metaParts.join(", ") + ")</span>" +
      "</div>" +
      '<div class="repo-header-right">' + timeAgo(group.last_active) + "</div>";

    var sessionList = document.createElement("div");
    sessionList.className = "session-list" + (isExpanded ? "" : " collapsed");

    var visibleCount = INITIAL_VISIBLE;
    var showingAll = group.sessions.length <= INITIAL_VISIBLE;

    function renderVisibleSessions() {
      sessionList.innerHTML = "";
      var toShow = showingAll ? group.sessions : group.sessions.slice(0, visibleCount);
      toShow.forEach(function (session) {
        sessionList.appendChild(renderSessionRow(session));
      });
      if (!showingAll) {
        var moreBtn = document.createElement("div");
        moreBtn.className = "show-more-btn";
        moreBtn.textContent = "Show all " + group.sessions.length + " sessions";
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

    header.addEventListener("click", function () {
      if (expandedRepos.has(group.repo_root)) {
        expandedRepos.delete(group.repo_root);
        sessionList.classList.add("collapsed");
        header.querySelector(".repo-chevron").classList.remove("expanded");
      } else {
        expandedRepos.add(group.repo_root);
        sessionList.classList.remove("collapsed");
        sessionList.style.maxHeight = sessionList.scrollHeight + "px";
        header.querySelector(".repo-chevron").classList.add("expanded");
      }
    });

    // Set initial max-height for expanded groups
    if (isExpanded) {
      requestAnimationFrame(function () {
        sessionList.style.maxHeight = sessionList.scrollHeight + "px";
      });
    }

    container.appendChild(header);
    container.appendChild(sessionList);
    return container;
  }

  function renderNonRepoGroup(group) {
    if (group.sessions.length === 0) return null;

    var isExpanded = expandedRepos.has(group.cwd);
    var container = document.createElement("div");
    container.className = "repo-group";

    // Use last path component as display name
    var displayName = group.cwd.split("/").filter(Boolean).pop() || group.cwd;

    var header = document.createElement("div");
    header.className = "repo-header";
    header.innerHTML =
      '<div class="repo-header-left">' +
      '<span class="repo-chevron ' + (isExpanded ? "expanded" : "") + '">\u25B6</span>' +
      '<span class="repo-name">' + escapeHtml(displayName) + "</span>" +
      '<span class="repo-meta">(' + group.sessions.length + " sessions)</span>" +
      "</div>" +
      '<div class="repo-header-right">' + timeAgo(group.last_active) + "</div>";

    var sessionList = document.createElement("div");
    sessionList.className = "session-list" + (isExpanded ? "" : " collapsed");

    var showingAll = group.sessions.length <= INITIAL_VISIBLE;

    function renderVisibleSessions() {
      sessionList.innerHTML = "";
      var toShow = showingAll ? group.sessions : group.sessions.slice(0, INITIAL_VISIBLE);
      toShow.forEach(function (session) {
        sessionList.appendChild(renderSessionRow(session));
      });
      if (!showingAll) {
        var moreBtn = document.createElement("div");
        moreBtn.className = "show-more-btn";
        moreBtn.textContent = "Show all " + group.sessions.length + " sessions";
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

    header.addEventListener("click", function () {
      if (expandedRepos.has(group.cwd)) {
        expandedRepos.delete(group.cwd);
        sessionList.classList.add("collapsed");
        header.querySelector(".repo-chevron").classList.remove("expanded");
      } else {
        expandedRepos.add(group.cwd);
        sessionList.classList.remove("collapsed");
        sessionList.style.maxHeight = sessionList.scrollHeight + "px";
        header.querySelector(".repo-chevron").classList.add("expanded");
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
      var el = entry.type === "repo"
        ? renderRepoGroup(entry.group)
        : renderNonRepoGroup(entry.group);
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
    openTerminal(session);
  }

  // --- Terminal ---

  var activeTerminal = null; // { term, ws, fitAddon }
  var $terminalPanel = document.getElementById("terminal-panel");
  var $terminalContainer = document.getElementById("terminal-container");
  var $terminalTitle = document.getElementById("terminal-title");
  var $terminalClose = document.getElementById("terminal-close");

  function openTerminal(session) {
    closeTerminal();

    $terminalPanel.classList.remove("hidden");
    document.body.classList.add("terminal-open");

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

    term.open($terminalContainer);
    fitAddon.fit();

    var params = new URLSearchParams({
      source: session.source,
      session_id: session.id,
      cwd: session.cwd,
    });
    var wsProtocol = location.protocol === "https:" ? "wss:" : "ws:";
    var wsUrl = wsProtocol + "//" + location.host + "/ws/terminal?" + params.toString();
    var ws = new WebSocket(wsUrl);

    ws.onopen = function () {
      ws.send(JSON.stringify({
        type: "resize",
        cols: term.cols,
        rows: term.rows,
      }));
      term.focus();
    };

    ws.onmessage = function (evt) {
      term.write(evt.data);
    };

    ws.onclose = function () {
      term.write("\r\n\x1b[90m[session ended]\x1b[0m\r\n");
    };

    term.onData(function (data) {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(data);
      }
    });

    term.onResize(function (size) {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          type: "resize",
          cols: size.cols,
          rows: size.rows,
        }));
      }
    });

    window.addEventListener("resize", handleTerminalResize);

    $terminalTitle.textContent = sessionLabel(session);
    activeTerminal = { term: term, ws: ws, fitAddon: fitAddon };
  }

  function handleTerminalResize() {
    if (activeTerminal) {
      activeTerminal.fitAddon.fit();
    }
  }

  function closeTerminal() {
    if (!activeTerminal) return;
    activeTerminal.ws.close();
    activeTerminal.term.dispose();
    activeTerminal = null;
    $terminalPanel.classList.add("hidden");
    document.body.classList.remove("terminal-open");
    $terminalContainer.innerHTML = "";
    window.removeEventListener("resize", handleTerminalResize);
  }

  $terminalClose.addEventListener("click", closeTerminal);

  // --- Last scan timer ---

  function updateLastScan() {
    if (lastScannedTime) {
      $lastScan.textContent = "Last scan: " + timeAgo(lastScannedTime);
    }
  }

  // --- Event listeners ---

  $btnRefresh.addEventListener("click", refreshSessions);

  // --- Init ---

  fetchSessions();
})();
