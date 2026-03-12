# ABOUTME: Git status checker for repositories associated with agent sessions.
# ABOUTME: Detects repo roots from working directories and queries live git status.

import subprocess
from dataclasses import dataclass

# Module-level cache for repo root lookups (cwd -> repo_root or None)
_repo_root_cache: dict[str, str | None] = {}


@dataclass
class GitStatus:
    """Live git status for a repository."""

    branch: str | None
    dirty: bool
    unpushed: int
    untracked: int


def get_repo_root(cwd: str, _cache: dict[str, str | None] | None = None) -> str | None:
    """Find the git repo root for a working directory. Results are cached by cwd.

    Returns the absolute path to the repo root, or None if cwd is not inside a git repo.
    """
    cache = _cache if _cache is not None else _repo_root_cache
    if cwd in cache:
        return cache[cwd]

    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            root = result.stdout.strip()
            cache[cwd] = root
            return root
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    cache[cwd] = None
    return None


def get_git_status(repo_root: str) -> GitStatus | None:
    """Get live git status for a repository. Returns None if the path is not a valid git repo."""
    try:
        # Verify it's a git repo
        check = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if check.returncode != 0:
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    # Branch name
    branch_result = subprocess.run(
        ["git", "-C", repo_root, "branch", "--show-current"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    branch = branch_result.stdout.strip() or None

    # Porcelain status for dirty + untracked
    porcelain_result = subprocess.run(
        ["git", "-C", repo_root, "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    porcelain_lines = [line for line in porcelain_result.stdout.splitlines() if line.strip()]
    untracked = sum(1 for line in porcelain_lines if line.startswith("??"))
    dirty = any(not line.startswith("??") for line in porcelain_lines)

    # Unpushed commits (ahead of upstream)
    unpushed = 0
    rev_list_result = subprocess.run(
        ["git", "-C", repo_root, "rev-list", "--count", "@{upstream}..HEAD"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if rev_list_result.returncode == 0:
        try:
            unpushed = int(rev_list_result.stdout.strip())
        except ValueError:
            pass

    return GitStatus(
        branch=branch,
        dirty=dirty,
        unpushed=unpushed,
        untracked=untracked,
    )
