"""Environment configuration with fail-fast validation.

Secrets are loaded from a gitignored `.env` (pattern reused from the X-growth
pipeline). Required keys raise immediately so misconfiguration fails at startup
rather than mid-generation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(
            f"Required environment variable {name!r} is not set. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _optional(name: str) -> str | None:
    value = os.getenv(name)
    return value or None


@dataclass(frozen=True)
class Settings:
    """Immutable application settings resolved from the environment."""

    b2_key_id: str
    b2_app_key: str
    b2_bucket: str
    b2_region: str | None
    gmicloud_api_key: str | None
    openai_api_key: str | None
    anthropic_api_key: str | None
    public_base_url: str | None

    @property
    def has_gmicloud(self) -> bool:
        return bool(self.gmicloud_api_key)

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def has_captions(self) -> bool:
        return bool(self.anthropic_api_key)


def load_settings() -> Settings:
    """Resolve settings, failing fast if required B2 credentials are absent.

    Generative-provider keys are optional at load time so the app can boot and
    report which capabilities are available, rather than crashing when only one
    provider is configured.
    """
    settings = Settings(
        b2_key_id=_require("B2_KEY_ID"),
        b2_app_key=_require("B2_APP_KEY"),
        b2_bucket=_require("B2_BUCKET"),
        b2_region=_optional("B2_REGION"),
        gmicloud_api_key=_optional("GMICLOUD_API_KEY"),
        openai_api_key=_optional("OPENAI_API_KEY"),
        anthropic_api_key=_optional("ANTHROPIC_API_KEY"),
        public_base_url=_optional("BRANDFORGE_PUBLIC_BASE_URL"),
    )
    if not (settings.has_gmicloud or settings.has_openai):
        raise ConfigError(
            "No generative provider configured: set GMICLOUD_API_KEY (primary) "
            "or OPENAI_API_KEY (fallback)."
        )
    return settings
