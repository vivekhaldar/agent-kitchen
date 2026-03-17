# ABOUTME: Git status checker for repositories associated with agent sessions.
# ABOUTME: Detects repo roots from working directories and queries live git status.

import logging
import os
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Module-level cache for repo root lookups (cwd -> repo_root or None)
_repo_root_cache: dict[str, str | None] = {}

# Env vars that override git's -C flag and must be stripped for subprocess calls
_GIT_ENV_OVERRIDES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


def _clean_git_env() -> dict[str, str]:
    """Return a copy of os.environ without git override variables.

    Pre-commit hooks and other tools set GIT_DIR/GIT_WORK_TREE which override
    the -C flag, causing git to operate on the wrong repository.
    """
    return {k: v for k, v in os.environ.items() if k not in _GIT_ENV_OVERRIDES}


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
            env=_clean_git_env(),
        )
        if result.returncode == 0:
            root = result.stdout.strip()
            cache[cwd] = root
            return root
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    cache[cwd] = None
    return None


def _parse_porcelain_branch_header(header: str) -> tuple[str | None, int]:
    """Parse the branch header from `git status --porcelain -b`.

    The header line looks like:
      ## main                         (no upstream)
      ## main...origin/main           (tracking, in sync)
      ## main...origin/main [ahead 2] (tracking, ahead)

    Returns (branch_name, ahead_count).
    """
    if not header.startswith("## "):
        return None, 0

    rest = header[3:]

    # Detached HEAD: "## HEAD (no branch)"
    if rest.startswith("HEAD (no branch)"):
        return None, 0

    # "No commits yet" initial branch: "## No commits yet on main"
    if rest.startswith("No commits yet on "):
        return rest[len("No commits yet on ") :].strip(), 0

    # "Initial commit on main" (older git versions)
    if rest.startswith("Initial commit on "):
        return rest[len("Initial commit on ") :].strip(), 0

    # Extract ahead count from brackets if present
    ahead = 0
    bracket_idx = rest.find("[")
    if bracket_idx != -1:
        bracket_content = rest[bracket_idx + 1 : rest.find("]")]
        for part in bracket_content.split(","):
            part = part.strip()
            if part.startswith("ahead "):
                try:
                    ahead = int(part[6:])
                except ValueError:
                    pass
        rest = rest[:bracket_idx].strip()

    # Split "main...origin/main" or just "main"
    if "..." in rest:
        branch = rest.split("...")[0]
    else:
        branch = rest.strip()

    return branch or None, ahead


def get_git_status(repo_root: str) -> GitStatus | None:
    """Get live git status for a repository. Returns None if the path is not a valid git repo.

    Uses a single `git status --porcelain -b` call to get branch name, dirty/untracked
    status, and ahead count in one subprocess invocation.
    """
    # Verify this is a git repo root (has .git dir or file for worktrees)
    if not os.path.exists(os.path.join(repo_root, ".git")):
        return None

    branch = None
    dirty = False
    unpushed = 0
    untracked = 0

    try:
        result = subprocess.run(
            ["git", "-C", repo_root, "status", "--porcelain", "-b"],
            capture_output=True,
            text=True,
            timeout=5,
            env=_clean_git_env(),
        )
        if result.returncode != 0:
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    lines = result.stdout.splitlines()

    # First line is the branch header
    if lines:
        branch, unpushed = _parse_porcelain_branch_header(lines[0])

    # Remaining lines are file status entries
    file_lines = [line for line in lines[1:] if line.strip()]
    untracked = sum(1 for line in file_lines if line.startswith("??"))
    dirty = any(not line.startswith("??") for line in file_lines)

    return GitStatus(
        branch=branch,
        dirty=dirty,
        unpushed=unpushed,
        untracked=untracked,
    )
