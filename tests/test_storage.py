"""Tests for the B2 storage layer. A fake backend stands in for live B2."""

import pytest

from app import storage
from app.config import Settings
from app.models import BrandKit
from app.storage import _resolve_policy
from genblaze_core.storage import ObjectStorageSink, URLPolicy


def _settings(public_base: str | None = None) -> Settings:
    return Settings(
        b2_key_id="k",
        b2_app_key="a",
        b2_bucket="brandforge-media",
        b2_region="us-west-004",
        gmicloud_api_key=None,
        openai_api_key="sk-test",
        anthropic_api_key=None,
        public_base_url=public_base,
    )


class FakeBackend:
    """Minimal stand-in for S3StorageBackend used by the storage layer."""

    def __init__(self):
        self.put_calls = []
        self.store = {}

    def put(self, key, data, *, content_type=None, **_):
        self.put_calls.append((key, data, content_type))
        self.store[key] = data
        return key

    def get(self, key, **_):
        return self.store[key]

    def get_url(self, key, *, policy=None, **_):
        return f"https://cdn.example/{key}?policy={policy}"


@pytest.mark.unit
def test_brand_kit_key_is_versioned():
    kit = BrandKit(id="acme", name="Acme", version=3)
    assert storage.brand_kit_key(kit) == "brandkits/acme/v3.json"


@pytest.mark.unit
def test_resolve_policy_auto_when_no_public_base():
    assert _resolve_policy(_settings(public_base=None), public=True) is URLPolicy.AUTO


@pytest.mark.unit
def test_resolve_policy_public_when_base_configured():
    assert _resolve_policy(_settings(public_base="https://cdn"), public=True) is URLPolicy.PUBLIC


@pytest.mark.unit
def test_resolve_policy_auto_when_public_false():
    assert _resolve_policy(_settings(public_base="https://cdn"), public=False) is URLPolicy.AUTO


@pytest.mark.unit
def test_save_brand_kit_puts_versioned_json_and_returns_url():
    backend = FakeBackend()
    kit = BrandKit(id="acme", name="Acme", version=2, tone_words=["bold"])

    url = storage.save_brand_kit(_settings(), kit, backend=backend)

    key, data, content_type = backend.put_calls[0]
    assert key == "brandkits/acme/v2.json"
    assert content_type == "application/json"
    assert b"Acme" in data
    assert url.startswith("https://cdn.example/brandkits/acme/v2.json")


@pytest.mark.unit
def test_load_brand_kit_roundtrips():
    backend = FakeBackend()
    kit = BrandKit(id="acme", name="Acme", version=2, tone_words=["bold"], style_prompt="flat")
    storage.save_brand_kit(_settings(), kit, backend=backend)

    loaded = storage.load_brand_kit(_settings(), "acme", 2, backend=backend)

    assert loaded == kit


@pytest.mark.unit
def test_make_sink_returns_object_storage_sink():
    backend = FakeBackend()
    sink = storage.make_sink(_settings(), backend=backend)
    assert isinstance(sink, ObjectStorageSink)
