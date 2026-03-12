# ABOUTME: Tests for the Codex CLI session scanner.
# ABOUTME: Validates JSONL parsing, session metadata extraction, filtering, and index lookup.

from datetime import datetime, timezone
from pathlib import Path

from agent_kitchen.scanner import (
    load_codex_session_index,
    parse_codex_filename,
    scan_codex_sessions,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "codex_sessions"


class TestParseCodexFilename:
    """Tests for extracting session ID and start timestamp from filenames."""

    def test_parses_standard_filename(self):
        name = "rollout-2026-03-10T14-30-00-019c0001-aaaa-7777-bbbb-ccccddddeeee.jsonl"
        session_id, started_at = parse_codex_filename(name)
        assert session_id == "019c0001-aaaa-7777-bbbb-ccccddddeeee"
        assert started_at == datetime(2026, 3, 10, 14, 30, 0, tzinfo=timezone.utc)

    def test_parses_filename_with_seconds(self):
        name = "rollout-2026-01-05T09-15-45-019c9999-1111-2222-3333-444455556666.jsonl"
        session_id, started_at = parse_codex_filename(name)
        assert session_id == "019c9999-1111-2222-3333-444455556666"
        assert started_at == datetime(2026, 1, 5, 9, 15, 45, tzinfo=timezone.utc)

    def test_returns_none_for_invalid_filename(self):
        assert parse_codex_filename("not-a-session-file.jsonl") is None
        assert parse_codex_filename("random.txt") is None

    def test_returns_none_for_malformed_date(self):
        name = "rollout-bad-date-019c0001-aaaa-7777-bbbb-ccccddddeeee.jsonl"
        assert parse_codex_filename(name) is None


class TestLoadCodexSessionIndex:
    """Tests for loading the session index file."""

    def test_loads_index_from_fixture(self):
        index = load_codex_session_index(FIXTURES_DIR / "session_index.jsonl")
        assert len(index) == 3
        assert index["019c0001-aaaa-7777-bbbb-ccccddddeeee"] == "Fix broken test runner tests"
        assert index["019c0002-bbbb-7777-cccc-ddddeeeeffff"] == "Add OAuth2 auth support"
        assert index["019c0003-cccc-7777-dddd-eeeeffff0000"] == "Refactor database layer"

    def test_returns_empty_dict_for_missing_file(self):
        index = load_codex_session_index(FIXTURES_DIR / "nonexistent.jsonl")
        assert index == {}

    def test_returns_empty_dict_for_empty_file(self, tmp_path):
        empty = tmp_path / "empty.jsonl"
        empty.write_text("")
        index = load_codex_session_index(empty)
        assert index == {}


class TestScanCodexSessions:
    """Tests for the main Codex session scanning function."""

    def test_finds_all_sessions_within_window(self):
        since = datetime(2025, 1, 1, tzinfo=timezone.utc)
        sessions = scan_codex_sessions(
            since=since,
            sessions_dir=FIXTURES_DIR,
            index_path=FIXTURES_DIR / "session_index.jsonl",
        )
        assert len(sessions) == 4

    def test_filters_by_since_date(self):
        # Fixture file mtimes are all recent (created at test setup time), so
        # mtime filtering won't exclude any. Set since to far future to exclude all.
        since = datetime(2099, 1, 1, tzinfo=timezone.utc)
        sessions = scan_codex_sessions(
            since=since,
            sessions_dir=FIXTURES_DIR,
            index_path=FIXTURES_DIR / "session_index.jsonl",
        )
        assert len(sessions) == 0

    def test_since_filter_includes_recent_files(self):
        # With a very old since date, all fixture files (recent mtime) should be included
        since = datetime(2020, 1, 1, tzinfo=timezone.utc)
        sessions = scan_codex_sessions(
            since=since,
            sessions_dir=FIXTURES_DIR,
            index_path=FIXTURES_DIR / "session_index.jsonl",
        )
        assert len(sessions) == 4

    def test_parses_session_metadata(self):
        since = datetime(2026, 3, 1, tzinfo=timezone.utc)
        sessions = scan_codex_sessions(
            since=since,
            sessions_dir=FIXTURES_DIR,
            index_path=FIXTURES_DIR / "session_index.jsonl",
        )
        session_a = next(s for s in sessions if s.id == "019c0001-aaaa-7777-bbbb-ccccddddeeee")
        assert session_a.source == "codex"
        assert session_a.cwd == "/Users/test/repos/myproject"
        assert session_a.git_branch == "main"
        assert session_a.started_at == datetime(2026, 3, 10, 14, 30, 0, tzinfo=timezone.utc)
        assert session_a.last_active == datetime(2026, 3, 10, 14, 30, 32, tzinfo=timezone.utc)

    def test_counts_turns_from_event_msg(self):
        since = datetime(2026, 3, 1, tzinfo=timezone.utc)
        sessions = scan_codex_sessions(
            since=since,
            sessions_dir=FIXTURES_DIR,
            index_path=FIXTURES_DIR / "session_index.jsonl",
        )
        # Session A: 1 user_message + 2 agent_message = 3
        session_a = next(s for s in sessions if s.id == "019c0001-aaaa-7777-bbbb-ccccddddeeee")
        assert session_a.turn_count == 3

        # Session B: 1 user_message + 3 agent_message = 4
        session_b = next(s for s in sessions if s.id == "019c0002-bbbb-7777-cccc-ddddeeeeffff")
        assert session_b.turn_count == 4

    def test_uses_thread_name_as_summary(self):
        since = datetime(2026, 3, 1, tzinfo=timezone.utc)
        sessions = scan_codex_sessions(
            since=since,
            sessions_dir=FIXTURES_DIR,
            index_path=FIXTURES_DIR / "session_index.jsonl",
        )
        session_a = next(s for s in sessions if s.id == "019c0001-aaaa-7777-bbbb-ccccddddeeee")
        assert session_a.summary == "Fix broken test runner tests"

    def test_empty_summary_without_index(self):
        since = datetime(2025, 1, 1, tzinfo=timezone.utc)
        sessions = scan_codex_sessions(
            since=since,
            sessions_dir=FIXTURES_DIR,
            index_path=FIXTURES_DIR / "nonexistent_index.jsonl",
        )
        for s in sessions:
            assert s.summary == ""

    def test_extracts_git_branch(self):
        since = datetime(2026, 3, 1, tzinfo=timezone.utc)
        sessions = scan_codex_sessions(
            since=since,
            sessions_dir=FIXTURES_DIR,
            index_path=FIXTURES_DIR / "session_index.jsonl",
        )
        session_b = next(s for s in sessions if s.id == "019c0002-bbbb-7777-cccc-ddddeeeeffff")
        assert session_b.git_branch == "feature/auth"

    def test_handles_session_without_git(self):
        since = datetime(2025, 1, 1, tzinfo=timezone.utc)
        sessions = scan_codex_sessions(
            since=since,
            sessions_dir=FIXTURES_DIR,
            index_path=FIXTURES_DIR / "session_index.jsonl",
        )
        session_d = next(s for s in sessions if s.id == "019c0004-dddd-7777-eeee-ffff00001111")
        assert session_d.git_branch is None
        assert session_d.cwd == "/Users/test/old-project"

    def test_sets_file_path(self):
        since = datetime(2026, 3, 1, tzinfo=timezone.utc)
        sessions = scan_codex_sessions(
            since=since,
            sessions_dir=FIXTURES_DIR,
            index_path=FIXTURES_DIR / "session_index.jsonl",
        )
        session_a = next(s for s in sessions if s.id == "019c0001-aaaa-7777-bbbb-ccccddddeeee")
        assert session_a.file_path.endswith(".jsonl")
        assert "019c0001" in session_a.file_path

    def test_sets_file_mtime(self):
        since = datetime(2026, 3, 1, tzinfo=timezone.utc)
        sessions = scan_codex_sessions(
            since=since,
            sessions_dir=FIXTURES_DIR,
            index_path=FIXTURES_DIR / "session_index.jsonl",
        )
        for s in sessions:
            assert s.file_mtime > 0

    def test_returns_empty_for_missing_directory(self):
        since = datetime(2025, 1, 1, tzinfo=timezone.utc)
        sessions = scan_codex_sessions(
            since=since,
            sessions_dir=Path("/nonexistent/path"),
            index_path=FIXTURES_DIR / "session_index.jsonl",
        )
        assert sessions == []

    def test_returns_empty_for_empty_directory(self, tmp_path):
        since = datetime(2025, 1, 1, tzinfo=timezone.utc)
        sessions = scan_codex_sessions(
            since=since,
            sessions_dir=tmp_path,
            index_path=FIXTURES_DIR / "session_index.jsonl",
        )
        assert sessions == []

    def test_skips_malformed_jsonl(self, tmp_path):
        """Sessions with malformed JSONL should be skipped gracefully."""
        day_dir = tmp_path / "2026" / "03" / "10"
        day_dir.mkdir(parents=True)
        bad_file = (
            day_dir / "rollout-2026-03-10T10-00-00-019cffff-aaaa-7777-bbbb-ccccddddeeee.jsonl"
        )
        bad_file.write_text("this is not json\n{also bad\n")
        since = datetime(2025, 1, 1, tzinfo=timezone.utc)
        sessions = scan_codex_sessions(since=since, sessions_dir=tmp_path)
        assert sessions == []

    def test_skips_non_jsonl_files(self, tmp_path):
        """Non-JSONL files should be ignored."""
        day_dir = tmp_path / "2026" / "03" / "10"
        day_dir.mkdir(parents=True)
        (day_dir / "notes.txt").write_text("not a session")
        since = datetime(2025, 1, 1, tzinfo=timezone.utc)
        sessions = scan_codex_sessions(since=since, sessions_dir=tmp_path)
        assert sessions == []

    def test_slug_is_none_for_codex(self):
        """Codex sessions don't have slug (Claude concept); should be None."""
        since = datetime(2026, 3, 1, tzinfo=timezone.utc)
        sessions = scan_codex_sessions(
            since=since,
            sessions_dir=FIXTURES_DIR,
            index_path=FIXTURES_DIR / "session_index.jsonl",
        )
        for s in sessions:
            assert s.slug is None
