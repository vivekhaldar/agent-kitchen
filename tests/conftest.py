# ABOUTME: Shared pytest fixtures for the test suite.
# ABOUTME: Strips git environment variables that interfere with tests under pre-commit.


import pytest


@pytest.fixture(autouse=True)
def clean_git_env(monkeypatch):
    """Remove GIT_DIR and GIT_WORK_TREE from the environment.

    Pre-commit hooks set these variables, which cause git subprocess calls
    in tests to resolve against the wrong repository.
    """
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    monkeypatch.delenv("GIT_INDEX_FILE", raising=False)
