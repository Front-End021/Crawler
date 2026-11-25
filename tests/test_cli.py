"""Unit tests for the CLI interface and parameter validation."""

from unittest.mock import AsyncMock, patch
from typer.testing import CliRunner
from main import app

runner = CliRunner()


def test_cli_help():
    """Verify that --help documents all expected arguments."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--url" in result.output
    assert "--username" in result.output
    assert "--password" in result.output
    assert "--max-steps" in result.output
    assert "--headless" in result.output
    assert "--output-dir" in result.output


def test_cli_missing_url():
    """Verify that omitting --url raises a validation error."""
    result = runner.invoke(app, [])
    assert result.exit_code != 0
    assert "Missing option" in result.output or "required" in result.output.lower() or "--url" in result.output


def test_cli_invalid_url():
    """Verify that an invalid URL schema triggers rejection."""
    result = runner.invoke(app, ["--url", "not-a-valid-url"])
    assert result.exit_code == 1
    assert "Invalid target URL" in result.output


def test_cli_valid_run_public():
    """Verify successful execution for public target."""
    with patch("main.run_pipeline", new_callable=AsyncMock) as mock_run:
        result = runner.invoke(app, ["--url", "https://example.com", "--max-steps", "10", "--headless"])
        assert result.exit_code == 0
        assert "Execution Parameters" in result.output
        assert "https://example.com" in result.output
        assert mock_run.call_count == 1


def test_cli_password_masking():
    """Verify that raw passwords are never printed to console."""
    sensitive_pass = "SuperSecretP@ssw0rd999!"
    with patch("main.run_pipeline", new_callable=AsyncMock) as mock_run:
        result = runner.invoke(
            app,
            [
                "--url",
                "https://example.com",
                "--username",
                "admin@example.com",
                "--password",
                sensitive_pass,
                "--headless",
            ],
        )
        assert result.exit_code == 0
        assert sensitive_pass not in result.output
        assert "admin@example.com" in result.output
        assert "********" in result.output
        assert mock_run.call_count == 1


def test_cli_inherits_env_settings_defaults(monkeypatch, tmp_path):
    """Regression: omitted CLI options fall back to Settings loaded from environment."""
    from src.config import get_settings

    monkeypatch.setenv("HEADLESS", "true")
    monkeypatch.setenv("DEFAULT_MAX_STEPS", "12")
    monkeypatch.setenv("DEFAULT_OUTPUT_DIR", str(tmp_path))
    get_settings.cache_clear()

    try:
        with patch("main.run_pipeline", new_callable=AsyncMock) as mock_run:
            result = runner.invoke(app, ["--url", "https://example.com"])
            assert result.exit_code == 0
            kwargs = mock_run.call_args.kwargs
            assert kwargs["headless"] is True
            assert kwargs["max_steps"] == 12
            assert kwargs["output_dir"] == tmp_path
    finally:
        get_settings.cache_clear()


def test_cli_username_without_password_warning():
    """Regression: providing --username without --password clearly reports skipped auth."""
    with patch("main.run_pipeline", new_callable=AsyncMock):
        result = runner.invoke(app, ["--url", "https://example.com", "--username", "onlyuser", "--headless"])
        assert result.exit_code == 0
        assert "Incomplete" in result.output
