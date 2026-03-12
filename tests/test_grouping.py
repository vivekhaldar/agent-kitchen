# ABOUTME: Tests for session grouping logic.
# ABOUTME: Covers grouping by repo, non-repo grouping, sorting, and git status attachment.

from datetime import datetime, timezone
from unittest.mock import patch

from agent_kitchen.git_status import GitStatus
from agent_kitchen.grouping import group_sessions
from agent_kitchen.models import Session


def _make_session(
    id: str = "test-id",
    source: str = "claude",
    cwd: str = "/home/user/project",
    repo_root: str | None = None,
    repo_name: str | None = None,
    git_branch: str | None = "main",
    started_at: datetime | None = None,
    last_active: datetime | None = None,
    slug: str | None = None,
    summary: str = "Test session",
    status: str = "done",
    turn_count: int = 5,
    file_path: str = "/tmp/test.jsonl",
    file_mtime: float = 1000.0,
) -> Session:
    return Session(
        id=id,
        source=source,
        cwd=cwd,
        repo_root=repo_root,
        repo_name=repo_name,
        git_branch=git_branch,
        started_at=started_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_active=last_active or datetime(2026, 1, 1, tzinfo=timezone.utc),
        slug=slug,
        summary=summary,
        status=status,
        turn_count=turn_count,
        file_path=file_path,
        file_mtime=file_mtime,
    )


class TestGroupSessions:
    """Tests for group_sessions() function."""

    def test_empty_input(self):
        """No sessions produces empty groups."""
        repo_groups, non_repo_groups = group_sessions([])
        assert repo_groups == []
        assert non_repo_groups == []

    def test_single_session_in_repo(self):
        """One session with a repo_root produces one RepoGroup."""
        s = _make_session(repo_root="/home/user/project", repo_name="project")

        with patch("agent_kitchen.grouping.get_git_status") as mock_git:
            mock_git.return_value = GitStatus(branch="main", dirty=False, unpushed=0, untracked=0)
            repo_groups, non_repo_groups = group_sessions([s])

        assert len(repo_groups) == 1
        assert repo_groups[0].repo_root == "/home/user/project"
        assert repo_groups[0].repo_name == "project"
        assert len(repo_groups[0].sessions) == 1
        assert non_repo_groups == []

    def test_single_session_without_repo(self):
        """One session without repo_root goes to NonRepoGroup."""
        s = _make_session(repo_root=None, cwd="/home/user")

        with patch("agent_kitchen.grouping.get_git_status"):
            repo_groups, non_repo_groups = group_sessions([s])

        assert repo_groups == []
        assert len(non_repo_groups) == 1
        assert non_repo_groups[0].cwd == "/home/user"
        assert len(non_repo_groups[0].sessions) == 1

    def test_multiple_sessions_same_repo(self):
        """Sessions in the same repo are grouped together."""
        s1 = _make_session(
            id="s1",
            repo_root="/home/user/project",
            repo_name="project",
            last_active=datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        s2 = _make_session(
            id="s2",
            repo_root="/home/user/project",
            repo_name="project",
            last_active=datetime(2026, 3, 12, tzinfo=timezone.utc),
        )

        with patch("agent_kitchen.grouping.get_git_status") as mock_git:
            mock_git.return_value = GitStatus(branch="main", dirty=False, unpushed=0, untracked=0)
            repo_groups, _ = group_sessions([s1, s2])

        assert len(repo_groups) == 1
        assert len(repo_groups[0].sessions) == 2

    def test_sessions_sorted_by_last_active_within_repo(self):
        """Sessions within a repo are sorted most-recent-first."""
        s_old = _make_session(
            id="old",
            repo_root="/home/user/project",
            repo_name="project",
            last_active=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        s_new = _make_session(
            id="new",
            repo_root="/home/user/project",
            repo_name="project",
            last_active=datetime(2026, 3, 12, tzinfo=timezone.utc),
        )

        with patch("agent_kitchen.grouping.get_git_status") as mock_git:
            mock_git.return_value = GitStatus(branch="main", dirty=False, unpushed=0, untracked=0)
            repo_groups, _ = group_sessions([s_old, s_new])

        assert repo_groups[0].sessions[0].id == "new"
        assert repo_groups[0].sessions[1].id == "old"

    def test_repos_sorted_by_last_active(self):
        """Repo groups are sorted by most recent session activity."""
        s_proj_a = _make_session(
            id="a",
            repo_root="/home/user/project-a",
            repo_name="project-a",
            last_active=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        s_proj_b = _make_session(
            id="b",
            repo_root="/home/user/project-b",
            repo_name="project-b",
            last_active=datetime(2026, 3, 12, tzinfo=timezone.utc),
        )

        with patch("agent_kitchen.grouping.get_git_status") as mock_git:
            mock_git.return_value = GitStatus(branch="main", dirty=False, unpushed=0, untracked=0)
            repo_groups, _ = group_sessions([s_proj_a, s_proj_b])

        assert len(repo_groups) == 2
        assert repo_groups[0].repo_name == "project-b"
        assert repo_groups[1].repo_name == "project-a"

    def test_repo_group_last_active_is_most_recent_session(self):
        """RepoGroup.last_active reflects the most recent session in the group."""
        recent = datetime(2026, 3, 12, tzinfo=timezone.utc)
        s1 = _make_session(
            id="s1",
            repo_root="/r",
            repo_name="r",
            last_active=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        s2 = _make_session(
            id="s2",
            repo_root="/r",
            repo_name="r",
            last_active=recent,
        )

        with patch("agent_kitchen.grouping.get_git_status") as mock_git:
            mock_git.return_value = GitStatus(branch="main", dirty=False, unpushed=0, untracked=0)
            repo_groups, _ = group_sessions([s1, s2])

        assert repo_groups[0].last_active == recent

    def test_git_status_attached_to_repo_group(self):
        """Live git status is attached to each RepoGroup."""
        s = _make_session(repo_root="/home/user/project", repo_name="project")

        with patch("agent_kitchen.grouping.get_git_status") as mock_git:
            mock_git.return_value = GitStatus(branch="feat", dirty=True, unpushed=3, untracked=1)
            repo_groups, _ = group_sessions([s])

        rg = repo_groups[0]
        assert rg.git_branch == "feat"
        assert rg.git_dirty is True
        assert rg.unpushed_commits == 3

    def test_git_status_none_uses_defaults(self):
        """If get_git_status returns None, RepoGroup uses safe defaults."""
        s = _make_session(repo_root="/home/user/project", repo_name="project")

        with patch("agent_kitchen.grouping.get_git_status") as mock_git:
            mock_git.return_value = None
            repo_groups, _ = group_sessions([s])

        rg = repo_groups[0]
        assert rg.git_branch is None
        assert rg.git_dirty is False
        assert rg.unpushed_commits == 0

    def test_non_repo_groups_sorted_by_last_active(self):
        """Non-repo groups are sorted by most recent activity."""
        s_home = _make_session(
            id="home",
            cwd="/home/user",
            repo_root=None,
            last_active=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        s_tmp = _make_session(
            id="tmp",
            cwd="/tmp",
            repo_root=None,
            last_active=datetime(2026, 3, 12, tzinfo=timezone.utc),
        )

        with patch("agent_kitchen.grouping.get_git_status"):
            _, non_repo_groups = group_sessions([s_home, s_tmp])

        assert len(non_repo_groups) == 2
        assert non_repo_groups[0].cwd == "/tmp"
        assert non_repo_groups[1].cwd == "/home/user"

    def test_non_repo_sessions_grouped_by_cwd(self):
        """Sessions with same cwd but no repo are grouped together."""
        s1 = _make_session(
            id="s1",
            cwd="/home/user",
            repo_root=None,
            last_active=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        s2 = _make_session(
            id="s2",
            cwd="/home/user",
            repo_root=None,
            last_active=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )

        with patch("agent_kitchen.grouping.get_git_status"):
            _, non_repo_groups = group_sessions([s1, s2])

        assert len(non_repo_groups) == 1
        assert len(non_repo_groups[0].sessions) == 2

    def test_mixed_repo_and_non_repo_sessions(self):
        """Sessions split correctly between repo and non-repo groups."""
        s_repo = _make_session(id="repo", repo_root="/r", repo_name="r")
        s_norep = _make_session(id="norep", cwd="/tmp", repo_root=None)

        with patch("agent_kitchen.grouping.get_git_status") as mock_git:
            mock_git.return_value = GitStatus(branch="main", dirty=False, unpushed=0, untracked=0)
            repo_groups, non_repo_groups = group_sessions([s_repo, s_norep])

        assert len(repo_groups) == 1
        assert len(non_repo_groups) == 1

    def test_repo_name_from_session(self):
        """RepoGroup.repo_name comes from session's repo_name."""
        s = _make_session(repo_root="/home/user/my-project", repo_name="my-project")

        with patch("agent_kitchen.grouping.get_git_status") as mock_git:
            mock_git.return_value = GitStatus(branch="main", dirty=False, unpushed=0, untracked=0)
            repo_groups, _ = group_sessions([s])

        assert repo_groups[0].repo_name == "my-project"

    def test_repo_name_fallback_to_path_basename(self):
        """If session has no repo_name, derive it from repo_root basename."""
        s = _make_session(repo_root="/home/user/my-project", repo_name=None)

        with patch("agent_kitchen.grouping.get_git_status") as mock_git:
            mock_git.return_value = GitStatus(branch="main", dirty=False, unpushed=0, untracked=0)
            repo_groups, _ = group_sessions([s])

        assert repo_groups[0].repo_name == "my-project"

    def test_git_status_called_once_per_repo(self):
        """get_git_status is called once per unique repo_root, not per session."""
        s1 = _make_session(id="s1", repo_root="/r", repo_name="r")
        s2 = _make_session(id="s2", repo_root="/r", repo_name="r")

        with patch("agent_kitchen.grouping.get_git_status") as mock_git:
            mock_git.return_value = GitStatus(branch="main", dirty=False, unpushed=0, untracked=0)
            group_sessions([s1, s2])

        mock_git.assert_called_once_with("/r")

    def test_non_repo_group_last_active(self):
        """NonRepoGroup.last_active reflects the most recent session."""
        recent = datetime(2026, 3, 12, tzinfo=timezone.utc)
        s1 = _make_session(
            id="s1",
            cwd="/tmp",
            repo_root=None,
            last_active=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        s2 = _make_session(id="s2", cwd="/tmp", repo_root=None, last_active=recent)

        with patch("agent_kitchen.grouping.get_git_status"):
            _, non_repo_groups = group_sessions([s1, s2])

        assert non_repo_groups[0].last_active == recent

    def test_sessions_from_different_sources_same_repo(self):
        """Claude and Codex sessions in the same repo are grouped together."""
        s_claude = _make_session(
            id="c1",
            source="claude",
            repo_root="/r",
            repo_name="r",
            last_active=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )
        s_codex = _make_session(
            id="x1",
            source="codex",
            repo_root="/r",
            repo_name="r",
            last_active=datetime(2026, 3, 2, tzinfo=timezone.utc),
        )

        with patch("agent_kitchen.grouping.get_git_status") as mock_git:
            mock_git.return_value = GitStatus(branch="main", dirty=False, unpushed=0, untracked=0)
            repo_groups, _ = group_sessions([s_claude, s_codex])

        assert len(repo_groups) == 1
        assert len(repo_groups[0].sessions) == 2
        # Most recent first
        assert repo_groups[0].sessions[0].source == "codex"
