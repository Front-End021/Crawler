"""Unit tests for configuration loading, safety keywords, and resource filtering."""

import pytest
from src.config import Settings, get_settings


def test_default_settings():
    """Verify default settings initialization."""
    settings = Settings()
    assert settings.gemini_model == "gemini-3.8-flash"
    assert settings.default_max_steps == 40
    assert settings.default_output_dir == "./output"
    assert settings.headless is False
    assert "delete" in settings.destructive_keywords
    assert "logout" in settings.destructive_keywords


def test_destructive_action_detection():
    """Verify detection of destructive actions across cases and substrings."""
    settings = Settings()

    # Destructive actions
    assert settings.is_destructive_action("Click Delete Account Button") is True
    assert settings.is_destructive_action("Proceed to checkout") is True
    assert settings.is_destructive_action("Log out") is True
    assert settings.is_destructive_action("Purge old logs") is True
    assert settings.is_destructive_action("Remove user from team") is True

    # Safe actions
    assert settings.is_destructive_action("Click navigation tab 'Billing'") is False
    assert settings.is_destructive_action("Search for employee 'John'") is False
    assert settings.is_destructive_action("Filter by status 'Active'") is False
    assert settings.is_destructive_action("Next page") is False
    assert settings.is_destructive_action("") is False


def test_additional_destructive_keywords():
    """Verify additional keywords extension."""
    settings = Settings(additional_destructive_keywords=["custom_drop", "ban_user"])
    keywords = settings.get_all_destructive_keywords()
    assert "custom_drop" in keywords
    assert "ban_user" in keywords
    assert settings.is_destructive_action("Trigger custom_drop handler") is True


def test_ignored_resources_and_static_extensions():
    """Verify static asset and telemetry URL detection."""
    settings = Settings()

    # Ignored resource types
    assert settings.is_ignored_resource("image", "https://example.com/api/data") is True
    assert settings.is_ignored_resource("stylesheet", "https://example.com/style.css") is True
    assert settings.is_ignored_resource("font", "https://example.com/font.woff2") is True

    # Ignored file extensions
    assert settings.is_ignored_resource("fetch", "https://example.com/bundle.js") is True
    assert settings.is_ignored_resource("fetch", "https://example.com/theme.css") is True
    assert settings.is_ignored_resource("fetch", "https://example.com/logo.png") is True

    # Ignored analytics domains
    assert settings.is_ignored_resource("fetch", "https://www.google-analytics.com/g/collect") is True
    assert settings.is_ignored_resource("fetch", "https://in.hotjar.com/api/v2") is True

    # Valid API calls
    assert settings.is_ignored_resource("fetch", "https://example.com/api/v1/users") is False
    assert settings.is_ignored_resource("xhr", "https://example.com/graphql") is False


def test_header_sanitization():
    """Verify sensitive token redaction in headers."""
    settings = Settings()
    headers = {
        "Authorization": "Bearer super-secret-jwt-token-12345",
        "Cookie": "session_id=xyz789; secure=true",
        "X-Api-Key": "live_key_9999",
        "Content-Type": "application/json",
        "Accept": "*/*",
    }
    sanitized = settings.sanitize_headers(headers)
    assert sanitized["Authorization"] == "{{auth_token}}"
    assert sanitized["Cookie"] == "{{auth_cookie}}"
    assert sanitized["X-Api-Key"] == "{{auth_token}}"
    assert sanitized["Content-Type"] == "application/json"
    assert sanitized["Accept"] == "*/*"


def test_env_additional_destructive_keywords_empty_and_csv(monkeypatch):
    """Regression: .env.example empty and comma-separated values must not raise SettingsError."""
    monkeypatch.setenv("ADDITIONAL_DESTRUCTIVE_KEYWORDS", "")
    s_empty = Settings()
    assert s_empty.additional_destructive_keywords == []

    monkeypatch.setenv("ADDITIONAL_DESTRUCTIVE_KEYWORDS", "nuke_all, wipe_cache")
    s_csv = Settings()
    assert s_csv.additional_destructive_keywords == ["nuke_all", "wipe_cache"]
    assert s_csv.is_destructive_action("Click nuke_all") is True


def test_destructive_action_word_boundaries_and_reset():
    """Regression: reset/restore blocked, while substring false positives like Dropdown/Payroll are allowed."""
    settings = Settings()
    assert settings.is_destructive_action("Reset to Default") is True
    assert settings.is_destructive_action("Restore defaults") is True
    assert settings.is_destructive_action("btn_delete_user") is True

    # False positives previously matched by substring check
    assert settings.is_destructive_action("Dropdown Menu") is False
    assert settings.is_destructive_action("Payroll") is False
    assert settings.is_destructive_action("Payment History") is False


def test_ignored_consent_and_ad_telemetry_domains():
    """Regression: common third-party consent and ad-telemetry domains are excluded."""
    settings = Settings()
    assert settings.is_ignored_resource("fetch", "https://fundingchoicesmessages.google.com/el/abc") is True
    assert settings.is_ignored_resource("fetch", "https://cookiehub.net/client/123/en.json") is True
