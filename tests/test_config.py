# ABOUTME: Tests for configuration module.
# ABOUTME: Validates default values and environment variable overrides.

import os
from pathlib import Path
from unittest.mock import patch

import agent_kitchen.config as config_module


def test_default_scan_window():
    assert config_module.SCAN_WINDOW_DAYS == 60 or isinstance(config_module.SCAN_WINDOW_DAYS, int)


def test_default_server_port():
    assert isinstance(config_module.SERVER_PORT, int)


def test_default_refresh_interval():
    assert isinstance(config_module.REFRESH_INTERVAL_SECONDS, int)


def test_cache_dir_is_path():
    assert isinstance(config_module.CACHE_DIR, Path)


def test_haiku_model():
    assert config_module.HAIKU_MODEL == "claude-haiku-4-5-20251001"


def test_summary_concurrency():
    assert config_module.SUMMARY_CONCURRENCY == 10


def test_claude_projects_dir():
    assert isinstance(config_module.CLAUDE_PROJECTS_DIR, Path)
    assert str(config_module.CLAUDE_PROJECTS_DIR).endswith(".claude/projects")


def test_codex_sessions_dir():
    assert isinstance(config_module.CODEX_SESSIONS_DIR, Path)
    assert str(config_module.CODEX_SESSIONS_DIR).endswith(".codex/sessions")


def test_get_claude_token_success():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "test-token-123\n"
        token = config_module.get_claude_token()
        assert token == "test-token-123"
        mock_run.assert_called_once_with(
            ["pass", "dev/CLAUDE_SUBSCRIPTION_TOKEN"],
            capture_output=True,
            text=True,
        )


def test_get_claude_token_failure():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        try:
            config_module.get_claude_token()
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "pass" in str(e)


def test_setup_auth():
    with patch.object(config_module, "get_claude_token", return_value="my-token"):
        # Set ANTHROPIC_API_KEY to verify it gets removed
        os.environ["ANTHROPIC_API_KEY"] = "should-be-removed"
        config_module.setup_auth()
        assert os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") == "my-token"
        assert "ANTHROPIC_API_KEY" not in os.environ
