# ABOUTME: Tests for the data model definitions.
# ABOUTME: Validates Session, RepoGroup, and NonRepoGroup dataclass construction and defaults.

from datetime import datetime

from agent_kitchen.models import NonRepoGroup, RepoGroup, Session


def test_session_creation():
    session = Session(
        id="abc-123",
        source="claude",
        cwd="/Users/test/repos/myproject",
        repo_root="/Users/test/repos/myproject",
        repo_name="myproject",
        git_branch="main",
        started_at=datetime(2026, 3, 1, 10, 0, 0),
        last_active=datetime(2026, 3, 1, 11, 0, 0),
        slug="lively-herding-sonnet",
        summary="Implement retry logic",
        status="done",
        turn_count=15,
        file_path="/Users/test/.claude/projects/-Users-test/abc-123.jsonl",
        file_mtime=1710288000.0,
    )
    assert session.id == "abc-123"
    assert session.source == "claude"
    assert session.repo_name == "myproject"
    assert session.status == "done"
    assert session.turn_count == 15


def test_session_without_repo():
    session = Session(
        id="def-456",
        source="codex",
        cwd="/Users/test",
        repo_root=None,
        repo_name=None,
        git_branch=None,
        started_at=datetime(2026, 3, 1, 10, 0, 0),
        last_active=datetime(2026, 3, 1, 11, 0, 0),
        slug=None,
        summary="General help session",
        status="likely done",
        turn_count=3,
        file_path="/Users/test/.codex/sessions/2026/03/01/rollout-xxx.jsonl",
        file_mtime=1710288000.0,
    )
    assert session.repo_root is None
    assert session.repo_name is None
    assert session.git_branch is None


def test_repo_group_defaults():
    group = RepoGroup(
        repo_root="/Users/test/repos/myproject",
        repo_name="myproject",
        git_branch="main",
        git_dirty=False,
        unpushed_commits=0,
    )
    assert group.sessions == []
    assert group.last_active == datetime.min


def test_repo_group_with_sessions():
    session = Session(
        id="abc-123",
        source="claude",
        cwd="/Users/test/repos/myproject",
        repo_root="/Users/test/repos/myproject",
        repo_name="myproject",
        git_branch="main",
        started_at=datetime(2026, 3, 1, 10, 0, 0),
        last_active=datetime(2026, 3, 1, 11, 0, 0),
        slug="test-session",
        summary="Test",
        status="done",
        turn_count=5,
        file_path="/test/path.jsonl",
        file_mtime=1710288000.0,
    )
    group = RepoGroup(
        repo_root="/Users/test/repos/myproject",
        repo_name="myproject",
        git_branch="main",
        git_dirty=True,
        unpushed_commits=2,
        sessions=[session],
        last_active=session.last_active,
    )
    assert len(group.sessions) == 1
    assert group.git_dirty is True
    assert group.unpushed_commits == 2


def test_non_repo_group_defaults():
    group = NonRepoGroup(cwd="/Users/test")
    assert group.sessions == []
    assert group.last_active == datetime.min
