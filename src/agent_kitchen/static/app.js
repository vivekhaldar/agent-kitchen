// ABOUTME: Dashboard client — fetches session data, renders UI, handles interactions.
// ABOUTME: Vanilla JS with no build step; polls /api/sessions for updates.

(function () {
  "use strict";

  // --- State ---
  let dashboardData = null;
  let lastScannedTime = null;
  let expandedRepos = new Set(); // repo_root values that are expanded
  let currentView = "grouped"; // "grouped" or "chronological"
  let sourceFilter = "all"; // "all", "claude", or "codex"

  // --- DOM refs ---
  const $loading = document.getElementById("loading");
  const $repoGroups = document.getElementById("repo-groups");
  const $nonRepoGroups = document.getElementById("non-repo-groups");
  const $chronoView = document.getElementById("chrono-view");
  const $emptyState = document.getElementById("empty-state");
  const $lastScan = document.getElementById("last-scan");
  const $btnRefresh = document.getElementById("btn-refresh");
  const $filterSource = document.getElementById("filter-source");

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

  function filterSessions(sessions) {
    if (sourceFilter === "all") return sessions;
    return sessions.filter(function (s) { return s.source === sourceFilter; });
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

  function renderRepoGroup(group) {
    var filtered = filterSessions(group.sessions);
    if (filtered.length === 0) return null;

    var isExpanded = expandedRepos.has(group.repo_root);
    var container = document.createElement("div");
    container.className = "repo-group";

    // Git meta string
    var metaParts = [];
    if (group.git_branch) metaParts.push(group.git_branch);
    if (group.git_dirty) metaParts.push('<span class="dirty">dirty</span>');
    if (group.unpushed_commits > 0) metaParts.push('<span class="unpushed">' + group.unpushed_commits + " unpushed</span>");
    if (!group.git_dirty && group.unpushed_commits === 0) metaParts.push("clean");
    metaParts.push(filtered.length + " sessions");

    var header = document.createElement("div");
    header.className = "repo-header";
    header.innerHTML =
      '<div class="repo-header-left">' +
      '<span class="repo-chevron ' + (isExpanded ? "expanded" : "") + '">\u25B6</span>' +
      '<span class="repo-name">' + escapeHtml(group.repo_name) + "</span>" +
      '<span class="repo-meta">(' + metaParts.join(", ") + ")</span>" +
      "</div>" +
      '<div class="repo-header-right">' + timeAgo(group.last_active) + "</div>";

    var sessionList = document.createElement("div");
    sessionList.className = "session-list" + (isExpanded ? "" : " collapsed");

    var visibleCount = INITIAL_VISIBLE;
    var showingAll = filtered.length <= INITIAL_VISIBLE;

    function renderVisibleSessions() {
      sessionList.innerHTML = "";
      var toShow = showingAll ? filtered : filtered.slice(0, visibleCount);
      toShow.forEach(function (session) {
        sessionList.appendChild(renderSessionRow(session));
      });
      if (!showingAll) {
        var moreBtn = document.createElement("div");
        moreBtn.className = "show-more-btn";
        moreBtn.textContent = "Show all " + filtered.length + " sessions";
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
    var filtered = filterSessions(group.sessions);
    if (filtered.length === 0) return null;

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
      '<span class="repo-meta">(' + filtered.length + " sessions)</span>" +
      "</div>" +
      '<div class="repo-header-right">' + timeAgo(group.last_active) + "</div>";

    var sessionList = document.createElement("div");
    sessionList.className = "session-list" + (isExpanded ? "" : " collapsed");

    var showingAll = filtered.length <= INITIAL_VISIBLE;

    function renderVisibleSessions() {
      sessionList.innerHTML = "";
      var toShow = showingAll ? filtered : filtered.slice(0, INITIAL_VISIBLE);
      toShow.forEach(function (session) {
        sessionList.appendChild(renderSessionRow(session));
      });
      if (!showingAll) {
        var moreBtn = document.createElement("div");
        moreBtn.className = "show-more-btn";
        moreBtn.textContent = "Show all " + filtered.length + " sessions";
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

  function renderChronological(data) {
    $chronoView.innerHTML = "";
    // Collect all sessions from all groups, sort by last_active desc
    var allSessions = [];
    (data.repo_groups || []).forEach(function (g) {
      g.sessions.forEach(function (s) { allSessions.push(s); });
    });
    (data.non_repo_groups || []).forEach(function (g) {
      g.sessions.forEach(function (s) { allSessions.push(s); });
    });

    allSessions = filterSessions(allSessions);
    allSessions.sort(function (a, b) {
      return new Date(b.last_active) - new Date(a.last_active);
    });

    if (allSessions.length === 0) {
      $chronoView.innerHTML = '<div class="empty-state">No sessions match the current filter.</div>';
      return;
    }

    var chronoLimit = 50;
    var chronoShowAll = allSessions.length <= chronoLimit;
    var toShow = chronoShowAll ? allSessions : allSessions.slice(0, chronoLimit);
    toShow.forEach(function (session) {
      $chronoView.appendChild(renderSessionRow(session));
    });
    if (!chronoShowAll) {
      var moreBtn = document.createElement("div");
      moreBtn.className = "show-more-btn";
      moreBtn.textContent = "Show all " + allSessions.length + " sessions";
      moreBtn.addEventListener("click", function () {
        $chronoView.innerHTML = "";
        allSessions.forEach(function (session) {
          $chronoView.appendChild(renderSessionRow(session));
        });
      });
      $chronoView.appendChild(moreBtn);
    }
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

    if (currentView === "grouped") {
      $chronoView.classList.add("hidden");
      $repoGroups.classList.remove("hidden");
      $nonRepoGroups.classList.remove("hidden");

      // Auto-expand the first repo group on initial render
      if (expandedRepos.size === 0 && dashboardData.repo_groups.length > 0) {
        expandedRepos.add(dashboardData.repo_groups[0].repo_root);
      }

      $repoGroups.innerHTML = "";
      dashboardData.repo_groups.forEach(function (group) {
        var el = renderRepoGroup(group);
        if (el) $repoGroups.appendChild(el);
      });

      $nonRepoGroups.innerHTML = "";
      if (hasNonRepos) {
        var nonRepoEls = [];
        dashboardData.non_repo_groups.forEach(function (group) {
          var el = renderNonRepoGroup(group);
          if (el) nonRepoEls.push(el);
        });
        if (nonRepoEls.length > 0) {
          var sep = document.createElement("div");
          sep.className = "non-repo-separator";
          sep.textContent = "\u2014 Sessions outside git repos \u2014";
          $nonRepoGroups.appendChild(sep);
          nonRepoEls.forEach(function (el) { $nonRepoGroups.appendChild(el); });
        }
      }
    } else {
      $repoGroups.classList.add("hidden");
      $nonRepoGroups.classList.add("hidden");
      $chronoView.classList.remove("hidden");
      renderChronological(dashboardData);
    }
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
    $btnRefresh.classList.add("spinning");
    $btnRefresh.textContent = "Refreshing...";
    return fetch("/api/refresh")
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
    var params = new URLSearchParams({
      source: session.source,
      session_id: session.id,
      cwd: session.cwd,
    });

    rowEl.classList.add("launched");
    setTimeout(function () { rowEl.classList.remove("launched"); }, 500);

    fetch("/api/launch?" + params.toString())
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.error) {
          console.error("Launch failed:", data.error);
        }
      })
      .catch(function (err) {
        console.error("Launch request failed:", err);
      });
  }

  // --- Last scan timer ---

  function updateLastScan() {
    if (lastScannedTime) {
      $lastScan.textContent = "Last scan: " + timeAgo(lastScannedTime);
    }
  }

  // --- Event listeners ---

  $btnRefresh.addEventListener("click", refreshSessions);

  $filterSource.addEventListener("change", function () {
    sourceFilter = this.value;
    render();
  });

  document.querySelectorAll('input[name="view"]').forEach(function (radio) {
    radio.addEventListener("change", function () {
      currentView = this.value;
      render();
    });
  });

  // --- Init ---

  fetchSessions();

  // Auto-refresh every 30 seconds
  setInterval(function () {
    fetchSessions();
    updateLastScan();
  }, 30000);

  // Update "last scan" display every 5 seconds
  setInterval(updateLastScan, 5000);
})();
