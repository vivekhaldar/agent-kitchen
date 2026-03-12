# ABOUTME: Tests for the CLI entry point argument parsing and startup behavior.
# ABOUTME: Covers --port, --scan-days, --no-open flags and browser auto-open logic.

from unittest.mock import MagicMock, patch

from agent_kitchen.cli import build_arg_parser, run_cli


class TestArgParser:
    def test_defaults(self):
        parser = build_arg_parser()
        args = parser.parse_args([])
        assert args.port == 8099
        assert args.scan_days == 60
        assert args.no_open is False

    def test_custom_port(self):
        parser = build_arg_parser()
        args = parser.parse_args(["--port", "9000"])
        assert args.port == 9000

    def test_custom_scan_days(self):
        parser = build_arg_parser()
        args = parser.parse_args(["--scan-days", "30"])
        assert args.scan_days == 30

    def test_no_open_flag(self):
        parser = build_arg_parser()
        args = parser.parse_args(["--no-open"])
        assert args.no_open is True

    def test_all_flags_combined(self):
        parser = build_arg_parser()
        args = parser.parse_args(["--port", "3000", "--scan-days", "7", "--no-open"])
        assert args.port == 3000
        assert args.scan_days == 7
        assert args.no_open is True


class TestRunCli:
    @patch("agent_kitchen.cli.uvicorn")
    @patch("agent_kitchen.cli.asyncio")
    @patch("agent_kitchen.cli.setup_auth")
    @patch("agent_kitchen.cli.create_app")
    @patch("agent_kitchen.cli.webbrowser")
    def test_opens_browser_by_default(
        self, mock_webbrowser, mock_create_app, mock_setup_auth, mock_asyncio, mock_uvicorn
    ):
        mock_create_app.return_value = MagicMock()
        run_cli(["--port", "8099"])
        mock_webbrowser.open.assert_called_once_with("http://localhost:8099")

    @patch("agent_kitchen.cli.uvicorn")
    @patch("agent_kitchen.cli.asyncio")
    @patch("agent_kitchen.cli.setup_auth")
    @patch("agent_kitchen.cli.create_app")
    @patch("agent_kitchen.cli.webbrowser")
    def test_no_open_skips_browser(
        self, mock_webbrowser, mock_create_app, mock_setup_auth, mock_asyncio, mock_uvicorn
    ):
        mock_create_app.return_value = MagicMock()
        run_cli(["--no-open"])
        mock_webbrowser.open.assert_not_called()

    @patch("agent_kitchen.cli.uvicorn")
    @patch("agent_kitchen.cli.asyncio")
    @patch("agent_kitchen.cli.setup_auth")
    @patch("agent_kitchen.cli.create_app")
    @patch("agent_kitchen.cli.webbrowser")
    def test_port_passed_to_uvicorn(
        self, mock_webbrowser, mock_create_app, mock_setup_auth, mock_asyncio, mock_uvicorn
    ):
        mock_create_app.return_value = MagicMock()
        run_cli(["--port", "9999"])
        mock_uvicorn.run.assert_called_once()
        call_kwargs = mock_uvicorn.run.call_args
        assert call_kwargs.kwargs["port"] == 9999

    @patch("agent_kitchen.cli.uvicorn")
    @patch("agent_kitchen.cli.asyncio")
    @patch("agent_kitchen.cli.setup_auth")
    @patch("agent_kitchen.cli.create_app")
    @patch("agent_kitchen.cli.webbrowser")
    def test_scan_days_applied_to_config(
        self, mock_webbrowser, mock_create_app, mock_setup_auth, mock_asyncio, mock_uvicorn
    ):
        mock_create_app.return_value = MagicMock()
        with patch("agent_kitchen.cli.config") as mock_config:
            run_cli(["--scan-days", "14"])
            assert mock_config.SCAN_WINDOW_DAYS == 14

    @patch("agent_kitchen.cli.uvicorn")
    @patch("agent_kitchen.cli.asyncio")
    @patch("agent_kitchen.cli.setup_auth")
    @patch("agent_kitchen.cli.create_app")
    @patch("agent_kitchen.cli.webbrowser")
    def test_auth_failure_continues(
        self, mock_webbrowser, mock_create_app, mock_setup_auth, mock_asyncio, mock_uvicorn
    ):
        mock_setup_auth.side_effect = RuntimeError("No token")
        mock_create_app.return_value = MagicMock()
        # Should not raise — auth failure is non-fatal
        run_cli([])
        mock_uvicorn.run.assert_called_once()

    @patch("agent_kitchen.cli.uvicorn")
    @patch("agent_kitchen.cli.asyncio")
    @patch("agent_kitchen.cli.setup_auth")
    @patch("agent_kitchen.cli.create_app")
    @patch("agent_kitchen.cli.webbrowser")
    def test_initial_scan_runs_before_server(
        self, mock_webbrowser, mock_create_app, mock_setup_auth, mock_asyncio, mock_uvicorn
    ):
        mock_create_app.return_value = MagicMock()
        run_cli([])
        # asyncio.run should be called (for initial scan) before uvicorn.run
        mock_asyncio.run.assert_called_once()

    @patch("agent_kitchen.cli.uvicorn")
    @patch("agent_kitchen.cli.asyncio")
    @patch("agent_kitchen.cli.setup_auth")
    @patch("agent_kitchen.cli.create_app")
    @patch("agent_kitchen.cli.webbrowser")
    def test_port_also_applied_to_config(
        self, mock_webbrowser, mock_create_app, mock_setup_auth, mock_asyncio, mock_uvicorn
    ):
        mock_create_app.return_value = MagicMock()
        with patch("agent_kitchen.cli.config") as mock_config:
            run_cli(["--port", "7777"])
            assert mock_config.SERVER_PORT == 7777
