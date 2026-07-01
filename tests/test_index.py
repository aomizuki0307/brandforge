"""Tests for the Parquet asset index (``app.index``).

Real pyarrow round-trips the bytes; only B2 is faked (``FakeBackend`` stores
the Parquet payload verbatim), so serialization, de-dup, filtering, and URL
refresh are all exercised for real. All tests are ``unit`` (no network).
"""

import pytest

from app.config import Settings
from app.index import ASSET_INDEX_KEY, index_assets, query_assets, read_index
from app.models import Asset


def _settings() -> Settings:
    return Settings(
        b2_key_id="k",
        b2_app_key="a",
        b2_bucket="brandforge-media",
        b2_region="us-east-005",
        gmicloud_api_key=None,
        openai_api_key="sk-test",
        anthropic_api_key=None,
        public_base_url=None,
    )


class FakeBackend:
    """Stores Parquet bytes verbatim; mirrors S3StorageBackend's surface."""

    def __init__(self):
        self.store = {}
        self.put_calls = []

    def put(self, key, data, *, content_type=None, **_):
        self.put_calls.append((key, data, content_type))
        self.store[key] = data
        return key

    def get(self, key, **_):
        return self.store[key]

    def exists(self, key):
        return key in self.store

    def get_url(self, key, *, policy=None, **_):
        return f"https://cdn.example/{key}?policy={policy}"


def _asset(asset_id: str, *, brand="acme", campaign="c1", modality="image", when="2026-07-01T00:00:00+00:00", url="stale") -> Asset:
    return Asset(
        id=asset_id,
        campaign_id=campaign,
        brand_kit_id=brand,
        brand_kit_version=1,
        modality=modality,
        provider="openai:gpt-image-1",
        model="gpt-image-1",
        prompt="p",
        b2_key=f"brandforge/runs/x/{asset_id}.png",
        url=url,
        sha256="sha",
        manifest_b2_key="brandforge/runs/x/manifest.json",
        created_at=when,
    )


@pytest.mark.unit
def test_read_index_returns_empty_when_absent():
    assert read_index(_settings(), backend=FakeBackend()) == []


@pytest.mark.unit
def test_index_assets_roundtrips_through_parquet():
    backend = FakeBackend()
    assets = [_asset("a1"), _asset("a2"), _asset("a3")]

    total = index_assets(_settings(), assets, backend=backend)

    assert total == 3
    # Written under the single catalog key as Parquet.
    key, _data, content_type = backend.put_calls[0]
    assert key == ASSET_INDEX_KEY
    assert content_type == "application/vnd.apache.parquet"
    # Round-trip preserves the rows (order-independent).
    loaded = read_index(_settings(), backend=backend)
    assert {a.id for a in loaded} == {"a1", "a2", "a3"}
    assert loaded[0].manifest_b2_key == "brandforge/runs/x/manifest.json"


@pytest.mark.unit
def test_index_assets_roundtrips_nullable_fields():
    # sha256 / manifest_b2_key are Optional — a None row must survive the
    # Parquet round trip (guards against pyarrow null-column schema inference).
    backend = FakeBackend()
    bare = _asset("a1").model_copy(update={"sha256": None, "manifest_b2_key": None})

    index_assets(_settings(), [bare, _asset("a2")], backend=backend)

    loaded = {a.id: a for a in read_index(_settings(), backend=backend)}
    assert loaded["a1"].sha256 is None
    assert loaded["a1"].manifest_b2_key is None
    assert loaded["a2"].sha256 == "sha"  # non-None still preserved


@pytest.mark.unit
def test_index_assets_merges_and_dedupes_by_id():
    backend = FakeBackend()
    index_assets(_settings(), [_asset("a1"), _asset("a2")], backend=backend)

    # Re-index a1 (updated prompt) plus a new a3: a1 must not duplicate.
    updated = _asset("a1")
    updated = updated.model_copy(update={"prompt": "new"})
    total = index_assets(_settings(), [updated, _asset("a3")], backend=backend)

    assert total == 3
    loaded = {a.id: a for a in read_index(_settings(), backend=backend)}
    assert set(loaded) == {"a1", "a2", "a3"}
    assert loaded["a1"].prompt == "new"  # later revision wins


@pytest.mark.unit
def test_index_assets_empty_is_noop_returning_count():
    backend = FakeBackend()
    index_assets(_settings(), [_asset("a1")], backend=backend)
    put_count_before = len(backend.put_calls)

    total = index_assets(_settings(), [], backend=backend)

    assert total == 1
    assert len(backend.put_calls) == put_count_before  # no rewrite


@pytest.mark.unit
def test_query_assets_filters_by_fields():
    backend = FakeBackend()
    index_assets(
        _settings(),
        [
            _asset("a1", brand="acme", campaign="c1", modality="image"),
            _asset("a2", brand="acme", campaign="c2", modality="video"),
            _asset("b1", brand="other", campaign="c9", modality="image"),
        ],
        backend=backend,
    )

    by_brand = query_assets(_settings(), brand_kit_id="acme", backend=backend)
    assert {a.id for a in by_brand} == {"a1", "a2"}

    by_campaign = query_assets(_settings(), campaign_id="c1", backend=backend)
    assert {a.id for a in by_campaign} == {"a1"}

    by_modality = query_assets(_settings(), modality="video", backend=backend)
    assert {a.id for a in by_modality} == {"a2"}


@pytest.mark.unit
def test_query_assets_refreshes_stale_urls():
    backend = FakeBackend()
    index_assets(_settings(), [_asset("a1", url="https://old/expired")], backend=backend)

    [asset] = query_assets(_settings(), brand_kit_id="acme", backend=backend)

    # URL was re-resolved from the durable b2_key, not the stored snapshot.
    assert asset.url != "https://old/expired"
    assert asset.b2_key in asset.url


@pytest.mark.unit
def test_query_assets_sorts_newest_first():
    backend = FakeBackend()
    index_assets(
        _settings(),
        [
            _asset("old", when="2026-07-01T00:00:00+00:00"),
            _asset("new", when="2026-07-02T00:00:00+00:00"),
        ],
        backend=backend,
    )

    result = query_assets(_settings(), backend=backend, refresh_urls=False)

    assert [a.id for a in result] == ["new", "old"]
