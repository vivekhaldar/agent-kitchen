# ABOUTME: Shared test fixtures and configuration for the test suite.
# ABOUTME: Provides environment cleanup to isolate git operations in tests.

import pytest


@pytest.fixture(autouse=True)
def clean_git_env(monkeypatch):
    """Remove git environment variables that leak from pre-commit hooks.

    When tests run inside a pre-commit hook, GIT_DIR and related vars are set,
    causing subprocess git commands (git init, git status, etc.) to operate on
    the parent repo instead of the test's tmp_path.
    """
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY"):
        monkeypatch.delenv(var, raising=False)
