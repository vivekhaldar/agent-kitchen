# ABOUTME: Tests for the CLI entry point argument parsing and startup behavior.
# ABOUTME: Covers web/index subcommand flags and browser auto-open logic.

from unittest.mock import MagicMock, patch

import pytest

from agent_kitchen.cli import build_arg_parser, run_cli


class TestArgParser:
    def test_web_defaults(self):
        parser = build_arg_parser()
        args = parser.parse_args(["web"])
        assert args.port == 8099
        assert args.scan_days == 60
        assert args.no_open is False

    def test_web_custom_port(self):
        parser = build_arg_parser()
        args = parser.parse_args(["web", "--port", "9000"])
        assert args.port == 9000

    def test_web_custom_scan_days(self):
        parser = build_arg_parser()
        args = parser.parse_args(["web", "--scan-days", "30"])
        assert args.scan_days == 30

    def test_web_no_open_flag(self):
        parser = build_arg_parser()
        args = parser.parse_args(["web", "--no-open"])
        assert args.no_open is True

    def test_web_all_flags_combined(self):
        parser = build_arg_parser()
        args = parser.parse_args(["web", "--port", "3000", "--scan-days", "7", "--no-open"])
        assert args.port == 3000
        assert args.scan_days == 7
        assert args.no_open is True

    def test_index_defaults(self):
        parser = build_arg_parser()
        args = parser.parse_args(["index"])
        assert args.scan_days == 60
        assert args.dry_run is False
        assert args.force is False

    def test_index_flags(self):
        parser = build_arg_parser()
        args = parser.parse_args(["index", "--scan-days", "14", "--dry-run", "--force"])
        assert args.scan_days == 14
        assert args.dry_run is True
        assert args.force is True


class TestRunCli:
    @patch("agent_kitchen.cli.uvicorn")
    @patch("agent_kitchen.cli.setup_auth")
    @patch("agent_kitchen.cli.create_app")
    def test_passes_browser_url_by_default(self, mock_create_app, mock_setup_auth, mock_uvicorn):
        mock_create_app.return_value = MagicMock()
        run_cli(["web", "--port", "8099"])
        mock_create_app.assert_called_once_with(
            summarize=False, open_browser="http://localhost:8099"
        )

    @patch("agent_kitchen.cli.uvicorn")
    @patch("agent_kitchen.cli.setup_auth")
    @patch("agent_kitchen.cli.create_app")
    def test_no_open_skips_browser(self, mock_create_app, mock_setup_auth, mock_uvicorn):
        mock_create_app.return_value = MagicMock()
        run_cli(["web", "--no-open"])
        mock_create_app.assert_called_once_with(summarize=False, open_browser=None)

    @patch("agent_kitchen.cli.uvicorn")
    @patch("agent_kitchen.cli.setup_auth")
    @patch("agent_kitchen.cli.create_app")
    def test_port_passed_to_uvicorn(self, mock_create_app, mock_setup_auth, mock_uvicorn):
        mock_create_app.return_value = MagicMock()
        run_cli(["web", "--port", "9999"])
        mock_uvicorn.run.assert_called_once()
        call_kwargs = mock_uvicorn.run.call_args
        assert call_kwargs.kwargs["port"] == 9999

    @patch("agent_kitchen.cli.uvicorn")
    @patch("agent_kitchen.cli.setup_auth")
    @patch("agent_kitchen.cli.create_app")
    def test_scan_days_applied_to_config(self, mock_create_app, mock_setup_auth, mock_uvicorn):
        mock_create_app.return_value = MagicMock()
        with patch("agent_kitchen.cli.config") as mock_config:
            run_cli(["web", "--scan-days", "14"])
            assert mock_config.SCAN_WINDOW_DAYS == 14

    @patch("agent_kitchen.cli.uvicorn")
    @patch("agent_kitchen.cli.setup_auth")
    @patch("agent_kitchen.cli.create_app")
    def test_auth_failure_continues(self, mock_create_app, mock_setup_auth, mock_uvicorn):
        mock_setup_auth.side_effect = RuntimeError("No token")
        mock_create_app.return_value = MagicMock()
        # Should not raise — auth failure is non-fatal (only called with --summarize)
        run_cli(["web", "--summarize"])
        mock_uvicorn.run.assert_called_once()

    @patch("agent_kitchen.cli.uvicorn")
    @patch("agent_kitchen.cli.setup_auth")
    @patch("agent_kitchen.cli.create_app")
    def test_server_starts_without_blocking_scan(
        self, mock_create_app, mock_setup_auth, mock_uvicorn
    ):
        mock_create_app.return_value = MagicMock()
        run_cli(["web"])
        mock_uvicorn.run.assert_called_once()
        mock_create_app.assert_called_once()

    @patch("agent_kitchen.cli.uvicorn")
    @patch("agent_kitchen.cli.setup_auth")
    @patch("agent_kitchen.cli.create_app")
    def test_port_also_applied_to_config(self, mock_create_app, mock_setup_auth, mock_uvicorn):
        mock_create_app.return_value = MagicMock()
        with patch("agent_kitchen.cli.config") as mock_config:
            run_cli(["web", "--port", "7777"])
            assert mock_config.SERVER_PORT == 7777


class TestArgParserEdgeCases:
    """Tests for argument parsing edge cases."""

    def test_missing_subcommand_raises(self):
        parser = build_arg_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args([])
        assert exc_info.value.code == 2

    def test_invalid_subcommand_raises(self):
        parser = build_arg_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["invalid"])
        assert exc_info.value.code == 2

    def test_non_integer_port_raises(self):
        parser = build_arg_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["web", "--port", "abc"])
        assert exc_info.value.code == 2

    def test_negative_scan_days_is_accepted(self):
        """Argparse doesn't validate negative ints, so this should parse fine."""
        parser = build_arg_parser()
        args = parser.parse_args(["web", "--scan-days", "-1"])
        assert args.scan_days == -1

    def test_web_summarize_default_false(self):
        parser = build_arg_parser()
        args = parser.parse_args(["web"])
        assert args.summarize is False

    def test_web_summarize_flag(self):
        parser = build_arg_parser()
        args = parser.parse_args(["web", "--summarize"])
        assert args.summarize is True

    def test_index_concurrency_default(self):
        from agent_kitchen import config

        parser = build_arg_parser()
        args = parser.parse_args(["index"])
        assert args.concurrency == config.SUMMARY_CONCURRENCY

    def test_index_concurrency_custom(self):
        parser = build_arg_parser()
        args = parser.parse_args(["index", "--concurrency", "10"])
        assert args.concurrency == 10

    def test_index_all_flags_combined(self):
        parser = build_arg_parser()
        args = parser.parse_args(
            ["index", "--scan-days", "7", "--concurrency", "5", "--dry-run", "--force"]
        )
        assert args.scan_days == 7
        assert args.concurrency == 5
        assert args.dry_run is True
        assert args.force is True
