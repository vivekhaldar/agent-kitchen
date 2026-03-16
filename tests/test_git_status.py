# ABOUTME: Tests for git status checker module.
# ABOUTME: Covers repo root detection, git status parsing, caching, and error handling.

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from agent_kitchen.git_status import GitStatus, get_git_status, get_repo_root


@pytest.fixture(autouse=True)
def _clean_git_env(monkeypatch):
    """Strip GIT_DIR and GIT_WORK_TREE so test git commands operate on temp repos.

    Pre-commit and git worktrees set these env vars, which cause git init/status
    in temp directories to operate on the parent worktree instead.
    """
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    monkeypatch.delenv("GIT_INDEX_FILE", raising=False)


class TestGetRepoRoot:
    """Tests for get_repo_root() function."""

    def test_returns_repo_root_for_git_directory(self, tmp_path):
        """A directory inside a git repo returns the repo root."""
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
        subdir = tmp_path / "src" / "pkg"
        subdir.mkdir(parents=True)

        result = get_repo_root(str(subdir))
        assert result == str(tmp_path)

    def test_returns_none_for_non_git_directory(self, tmp_path):
        """A directory that's not in a git repo returns None."""
        result = get_repo_root(str(tmp_path))
        assert result is None

    def test_returns_none_for_nonexistent_directory(self):
        """A nonexistent directory returns None."""
        result = get_repo_root("/tmp/nonexistent-dir-abc123")
        assert result is None

    def test_caches_results_for_same_cwd(self, tmp_path):
        """Repeated calls with the same cwd use the cache."""
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)

        with patch("agent_kitchen.git_status.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=str(tmp_path) + "\n")
            cache = {}
            get_repo_root(str(tmp_path), _cache=cache)
            get_repo_root(str(tmp_path), _cache=cache)
            assert mock_run.call_count == 1

    def test_different_subdirs_same_repo_both_cached(self, tmp_path):
        """Different subdirectories in the same repo are cached independently by cwd."""
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
        sub_a = tmp_path / "a"
        sub_b = tmp_path / "b"
        sub_a.mkdir()
        sub_b.mkdir()

        cache = {}
        root_a = get_repo_root(str(sub_a), _cache=cache)
        root_b = get_repo_root(str(sub_b), _cache=cache)

        assert root_a == str(tmp_path)
        assert root_b == str(tmp_path)
        assert len(cache) == 2


class TestGetGitStatus:
    """Tests for get_git_status() function."""

    def test_clean_repo(self, tmp_path):
        """A freshly initialized repo with a commit is clean."""
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
            capture_output=True,
        )

        status = get_git_status(str(tmp_path))

        assert isinstance(status, GitStatus)
        assert status.dirty is False
        assert status.untracked == 0

    def test_dirty_repo_with_modified_file(self, tmp_path):
        """A repo with a modified tracked file reports dirty."""
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
        f = tmp_path / "file.txt"
        f.write_text("hello")
        subprocess.run(["git", "-C", str(tmp_path), "add", "file.txt"], capture_output=True)
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-m", "add file"],
            capture_output=True,
        )
        f.write_text("modified")

        status = get_git_status(str(tmp_path))
        assert status.dirty is True

    def test_untracked_files_count(self, tmp_path):
        """Untracked files are counted separately."""
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
            capture_output=True,
        )
        (tmp_path / "new1.txt").write_text("a")
        (tmp_path / "new2.txt").write_text("b")

        status = get_git_status(str(tmp_path))
        assert status.untracked == 2

    def test_branch_name(self, tmp_path):
        """Reports the current branch name."""
        subprocess.run(["git", "init", "-b", "main", str(tmp_path)], capture_output=True)
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
            capture_output=True,
        )

        status = get_git_status(str(tmp_path))
        assert status.branch == "main"

    def test_unpushed_commits_zero_without_remote(self, tmp_path):
        """A repo with no remote reports 0 unpushed commits."""
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
            capture_output=True,
        )

        status = get_git_status(str(tmp_path))
        assert status.unpushed == 0

    def test_unpushed_commits_with_remote(self, tmp_path):
        """A repo with commits ahead of remote reports the correct count."""
        # Create a bare "remote" repo
        remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], capture_output=True)

        # Create local repo and push
        local = tmp_path / "local"
        subprocess.run(["git", "clone", str(remote), str(local)], capture_output=True)
        subprocess.run(
            ["git", "-C", str(local), "commit", "--allow-empty", "-m", "first"],
            capture_output=True,
        )
        # Push to whatever branch was created by clone
        branch_result = subprocess.run(
            ["git", "-C", str(local), "branch", "--show-current"],
            capture_output=True,
            text=True,
        )
        branch = branch_result.stdout.strip()
        subprocess.run(
            ["git", "-C", str(local), "push", "-u", "origin", branch],
            capture_output=True,
        )

        # Make two more commits without pushing
        subprocess.run(
            ["git", "-C", str(local), "commit", "--allow-empty", "-m", "second"],
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(local), "commit", "--allow-empty", "-m", "third"],
            capture_output=True,
        )

        status = get_git_status(str(local))
        assert status.unpushed == 2

    def test_staged_files_make_repo_dirty(self, tmp_path):
        """Staged but uncommitted changes report dirty."""
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
            capture_output=True,
        )
        (tmp_path / "staged.txt").write_text("content")
        subprocess.run(["git", "-C", str(tmp_path), "add", "staged.txt"], capture_output=True)

        status = get_git_status(str(tmp_path))
        assert status.dirty is True

    def test_nonexistent_repo_returns_none(self):
        """A nonexistent path returns None."""
        result = get_git_status("/tmp/nonexistent-repo-xyz999")
        assert result is None

    def test_non_git_directory_returns_none(self, tmp_path):
        """A non-git directory returns None."""
        result = get_git_status(str(tmp_path))
        assert result is None

    def test_empty_repo_no_commits(self, tmp_path):
        """A repo with no commits handles gracefully."""
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)

        status = get_git_status(str(tmp_path))
        # Should still return something reasonable
        assert isinstance(status, GitStatus)
        assert status.unpushed == 0

    def test_detached_head(self, tmp_path):
        """A repo in detached HEAD state returns None for branch."""
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
            capture_output=True,
        )
        # Get the commit hash and detach HEAD
        result = subprocess.run(
            ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
        commit = result.stdout.strip()
        subprocess.run(
            ["git", "-C", str(tmp_path), "checkout", commit],
            capture_output=True,
        )

        status = get_git_status(str(tmp_path))
        assert status.branch is None or status.branch == ""
