"""Configuration management and safety settings for the autonomous crawler."""

from functools import lru_cache
import json
import re
from typing import Annotated, Any, Optional, Set
from urllib.parse import urlparse
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    # Gemini API Configuration
    gemini_api_key: Optional[str] = Field(
        default=None,
        description="Google Gemini API Key",
        alias="GEMINI_API_KEY",
    )
    gemini_model: str = Field(
        default="gemini-3.8-flash",
        description="Google Gemini model identifier",
        alias="GEMINI_MODEL",
    )

    # Exploration & Safety Defaults
    default_max_steps: int = Field(
        default=40,
        description="Default safety cap for browser exploration actions",
        alias="DEFAULT_MAX_STEPS",
    )
    default_output_dir: str = Field(
        default="./output",
        description="Default directory for exported Postman collection",
        alias="DEFAULT_OUTPUT_DIR",
    )
    headless: bool = Field(
        default=False,
        description="Run browser in headless mode",
        alias="HEADLESS",
    )

    # Destructive action blocklist keywords (case-insensitive substring match)
    destructive_keywords: list[str] = Field(
        default=[
            "delete",
            "remove",
            "destroy",
            "checkout",
            "pay",
            "purchase",
            "cancel subscription",
            "unsubscribe",
            "logout",
            "log out",
            "signout",
            "sign out",
            "deactivate",
            "drop",
            "purge",
            "terminate",
            "erase",
            "reset",
            "reset to default",
            "reset database",
            "restore",
            "wipe",
        ],
        description="Keywords representing destructive or state-terminating UI actions",
    )
    additional_destructive_keywords: Annotated[list[str], NoDecode] = Field(
        default=[],
        description="Additional custom destructive keywords loaded from environment",
        alias="ADDITIONAL_DESTRUCTIVE_KEYWORDS",
    )

    @field_validator("additional_destructive_keywords", mode="before")
    @classmethod
    def parse_additional_keywords(cls, value: Any) -> list[str]:
        """Support empty, comma-separated, or JSON-formatted environment values."""
        if value is None:
            return []
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except Exception:
                    pass
            return [part.strip() for part in raw.split(",") if part.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []

    # Static asset filtering
    ignored_resource_types: list[str] = Field(
        default=[
            "stylesheet",
            "image",
            "media",
            "font",
            "other",
            "eventsource",
            "websocket",
            "manifest",
            "ping",
        ],
        description="Playwright resource types strictly excluded from API sniffing",
    )
    ignored_extensions: list[str] = Field(
        default=[
            ".js",
            ".css",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".svg",
            ".ico",
            ".woff",
            ".woff2",
            ".ttf",
            ".eot",
            ".otf",
            ".mp4",
            ".webm",
            ".map",
            ".webp",
            ".avif",
        ],
        description="Static asset file extensions excluded from API capture",
    )
    ignored_domains: list[str] = Field(
        default=[
            "google-analytics.com",
            "analytics.google.com",
            "googletagmanager.com",
            "doubleclick.net",
            "facebook.com",
            "hotjar.com",
            "sentry.io",
            "datadoghq.com",
            "clarity.ms",
            "segment.io",
            "mixpanel.com",
            "intercom.io",
            "fundingchoicesmessages.google.com",
            "adtrafficquality.google",
            "cookiehub.net",
            "googlesyndication.com",
        ],
        description="Common telemetry and analytics domains excluded from API capture",
    )

    response_preview_max_chars: int = Field(
        default=1000,
        description="Maximum characters of response body retained for AI mapping context",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    def get_all_destructive_keywords(self) -> Set[str]:
        """Combine default and additional destructive keywords as lowercase set."""
        combined = set(k.strip().lower() for k in self.destructive_keywords)
        if self.additional_destructive_keywords:
            combined.update(
                k.strip().lower()
                for k in self.additional_destructive_keywords
                if k.strip()
            )
        return combined

    def is_destructive_action(self, action_text: str) -> bool:
        """Check whether an action description contains destructive keywords."""
        if not action_text:
            return False
        normalized = action_text.strip().lower()
        keywords = self.get_all_destructive_keywords()
        return any(
            re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", normalized)
            for keyword in keywords
        )

    def is_ignored_resource(self, resource_type: str, url: str) -> bool:
        """Determine if a network request should be ignored (static assets/telemetry)."""
        if resource_type.lower() in self.ignored_resource_types:
            return True

        parsed = urlparse(url)
        path = parsed.path.lower()
        if any(path.endswith(ext) for ext in self.ignored_extensions):
            return True

        domain = (parsed.hostname or "").lower()
        if any(domain == d or domain.endswith("." + d) for d in self.ignored_domains):
            return True

        return False

    def sanitize_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Mask sensitive headers like Authorization and Cookie with placeholder."""
        sanitized = {}
        for key, value in headers.items():
            lower_key = key.lower()
            if lower_key in ("authorization", "x-api-key", "token", "x-auth-token"):
                sanitized[key] = "{{auth_token}}"
            elif lower_key in ("cookie", "set-cookie"):
                sanitized[key] = "{{auth_cookie}}"
            else:
                sanitized[key] = value
        return sanitized


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton getter for application settings."""
    return Settings()
