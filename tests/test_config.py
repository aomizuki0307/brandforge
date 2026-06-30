"""Unit tests for fail-fast environment configuration."""

import importlib

import pytest


def _reload_config(monkeypatch, env: dict[str, str]):
    """Reload app.config with a controlled environment (dotenv disabled)."""
    for key in [
        "B2_KEY_ID",
        "B2_APP_KEY",
        "B2_BUCKET",
        "B2_REGION",
        "GMICLOUD_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "BRANDFORGE_PUBLIC_BASE_URL",
    ]:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    # Prevent .env on disk from leaking into the test environment.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    import app.config as config

    return importlib.reload(config)


@pytest.mark.unit
def test_missing_b2_raises(monkeypatch):
    config = _reload_config(monkeypatch, {"GMICLOUD_API_KEY": "x"})
    with pytest.raises(config.ConfigError, match="B2_KEY_ID"):
        config.load_settings()


@pytest.mark.unit
def test_no_provider_raises(monkeypatch):
    config = _reload_config(
        monkeypatch,
        {"B2_KEY_ID": "id", "B2_APP_KEY": "key", "B2_BUCKET": "bucket"},
    )
    with pytest.raises(config.ConfigError, match="generative provider"):
        config.load_settings()


@pytest.mark.unit
def test_loads_with_gmicloud(monkeypatch):
    config = _reload_config(
        monkeypatch,
        {
            "B2_KEY_ID": "id",
            "B2_APP_KEY": "key",
            "B2_BUCKET": "bucket",
            "GMICLOUD_API_KEY": "gmi",
        },
    )
    settings = config.load_settings()
    assert settings.b2_bucket == "bucket"
    assert settings.has_gmicloud is True
    assert settings.has_openai is False
    assert settings.has_captions is False
