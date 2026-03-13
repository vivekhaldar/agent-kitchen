# ABOUTME: Groups sessions by git repository or working directory.
# ABOUTME: Attaches live git status to each repo group and sorts by recent activity.

import logging
import os
from collections import defaultdict

from agent_kitchen.git_status import get_git_status
from agent_kitchen.models import NonRepoGroup, RepoGroup, Session

logger = logging.getLogger(__name__)


def group_sessions(
    sessions: list[Session],
) -> tuple[list[RepoGroup], list[NonRepoGroup]]:
    """Group sessions into RepoGroups (by repo_root) and NonRepoGroups (by cwd).

    Sessions with a repo_root are grouped by that root. Sessions without one are
    grouped by their cwd. Both group types and their contained sessions are sorted
    by last_active descending (most recent first).

    Live git status is fetched once per unique repo_root and attached to each RepoGroup.
    """
    repo_sessions: dict[str, list[Session]] = defaultdict(list)
    non_repo_sessions: dict[str, list[Session]] = defaultdict(list)

    for session in sessions:
        if session.repo_root:
            repo_sessions[session.repo_root].append(session)
        else:
            non_repo_sessions[session.cwd].append(session)

    # Build RepoGroups with live git status
    repo_groups: list[RepoGroup] = []
    for repo_root, repo_sess in repo_sessions.items():
        repo_sess.sort(key=lambda s: s.last_active, reverse=True)
        last_active = repo_sess[0].last_active

        # Derive repo_name: use the first session's repo_name, or fall back to basename
        repo_name = next(
            (s.repo_name for s in repo_sess if s.repo_name), None
        ) or os.path.basename(repo_root)

        git_status = get_git_status(repo_root)

        repo_groups.append(
            RepoGroup(
                repo_root=repo_root,
                repo_name=repo_name,
                git_branch=git_status.branch if git_status else None,
                git_dirty=git_status.dirty if git_status else False,
                unpushed_commits=git_status.unpushed if git_status else 0,
                sessions=repo_sess,
                last_active=last_active,
            )
        )

    repo_groups.sort(key=lambda g: g.last_active, reverse=True)
    logger.info("Grouped %d sessions into %d repo groups", len(sessions), len(repo_groups))

    # Build NonRepoGroups
    non_repo_groups: list[NonRepoGroup] = []
    for cwd, cwd_sess in non_repo_sessions.items():
        cwd_sess.sort(key=lambda s: s.last_active, reverse=True)
        last_active = cwd_sess[0].last_active

        non_repo_groups.append(
            NonRepoGroup(
                cwd=cwd,
                sessions=cwd_sess,
                last_active=last_active,
            )
        )

    non_repo_groups.sort(key=lambda g: g.last_active, reverse=True)

    return repo_groups, non_repo_groups
