# ABOUTME: Test configuration and fixtures.
# ABOUTME: Strips git override env vars that interfere with subprocess calls in tests.


import pytest

# Pre-commit hooks set GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE which override
# the -C flag in git subprocess calls, causing tests to operate on the wrong
# repository. Strip these before any test runs.
_GIT_OVERRIDES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


@pytest.fixture(autouse=True)
def _clean_git_env_for_tests(monkeypatch):
    """Remove git override env vars so tests use their own git repos."""
    for var in _GIT_OVERRIDES:
        monkeypatch.delenv(var, raising=False)
