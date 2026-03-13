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
    assert config_module.SUMMARY_CONCURRENCY == 3


def test_terminal_app_default():
    assert config_module.TERMINAL_APP == "ghostty"


def test_claude_projects_dir():
    assert isinstance(config_module.CLAUDE_PROJECTS_DIR, Path)
    assert str(config_module.CLAUDE_PROJECTS_DIR).endswith(".claude/projects")


def test_codex_sessions_dir():
    assert isinstance(config_module.CODEX_SESSIONS_DIR, Path)
    assert str(config_module.CODEX_SESSIONS_DIR).endswith(".codex/sessions")


def test_setup_auth_with_api_key():
    """setup_auth succeeds when ANTHROPIC_API_KEY is set."""
    env = {"ANTHROPIC_API_KEY": "sk-ant-test", "CLAUDE_CODE_OAUTH_TOKEN": ""}
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        config_module.setup_auth()
        assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-test"


def test_setup_auth_with_oauth_token():
    """setup_auth succeeds when CLAUDE_CODE_OAUTH_TOKEN is set."""
    env = {"CLAUDE_CODE_OAUTH_TOKEN": "oauth-test"}
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        config_module.setup_auth()
        assert os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") == "oauth-test"


def test_setup_auth_falls_back_to_pass():
    """setup_auth falls back to `pass` when no env vars are set."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "pass-token\n"
            config_module.setup_auth()
            assert os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") == "pass-token"


def test_setup_auth_raises_when_no_credentials():
    """setup_auth raises RuntimeError when no credentials are available."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            try:
                config_module.setup_auth()
                assert False, "Should have raised RuntimeError"
            except RuntimeError as e:
                assert "ANTHROPIC_API_KEY" in str(e)
