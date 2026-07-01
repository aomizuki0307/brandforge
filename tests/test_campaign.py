"""Tests for the campaign driver (``app.campaign``).

The Genblaze run is faked at the ``build_image_pipeline`` seam (as in
``test_pipeline``) and B2 is faked with the same ``FakeBackend`` used by
``test_storage``, so these exercise the real driver ordering — kit save,
provider pick, generation, and one-manifest mapping — with no network. All
tests are ``unit``.
"""

import types

import pytest

from app import campaign as campaign_mod
from app import pipeline
from app.campaign import run_campaign
from app.config import Settings
from app.index import ASSET_INDEX_KEY, query_assets
from app.models import BrandKit, Campaign
from app.pipeline import PipelineError, ProviderChoice


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


def _kit() -> BrandKit:
    return BrandKit(id="acme", name="Acme", version=2, tone_words=["bold"], style_prompt="flat")


def _camp(n: int = 3) -> Campaign:
    return Campaign(id="c1", brand_kit_id="acme", theme="spring launch", num_variants=n)


def _choice() -> ProviderChoice:
    return ProviderChoice(provider=object(), model="gpt-image-1", name="openai:gpt-image-1")


class FakeBackend:
    """Minimal stand-in for S3StorageBackend (matches tests/test_storage.py)."""

    def __init__(self):
        self.put_calls = []
        self.store = {}

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


def _fake_pipe_factory(n: int):
    """Build a ``build_image_pipeline`` replacement whose run yields ``n`` media
    across ``n`` steps under a single manifest (one manifest per campaign)."""
    steps = [
        types.SimpleNamespace(
            model="gpt-image-1",
            prompt=f"variant {i}",
            assets=[
                types.SimpleNamespace(
                    asset_id=f"a{i}",
                    url=f"https://b/brandforge/run1/a{i}.png",
                    sha256=f"sha{i}",
                )
            ],
        )
        for i in range(n)
    ]
    fake_result = types.SimpleNamespace(
        run=types.SimpleNamespace(steps=steps, run_id="run1"),
        manifest=types.SimpleNamespace(manifest_uri="brandforge/run1/manifest.json"),
        failed_steps=lambda: [],
        error_summary=lambda: None,
    )

    class FakePipe:
        def run(self, *, sink=None, timeout=None, raise_on_failure=None):
            return fake_result

    return lambda *a, **k: FakePipe()


@pytest.mark.unit
def test_run_campaign_saves_kit_and_returns_variant_set(monkeypatch):
    monkeypatch.setattr(pipeline, "build_image_pipeline", _fake_pipe_factory(3))
    backend = FakeBackend()

    result = run_campaign(
        _settings(), _kit(), _camp(3), backend=backend, choice=_choice(), created_at="t"
    )

    # One manifest for the whole set: all three variants reference it.
    assert len(result.assets) == 3
    manifest_keys = {a.manifest_b2_key for a in result.assets}
    assert manifest_keys == {"brandforge/run1/manifest.json"}
    assert result.manifest.manifest_uri == "brandforge/run1/manifest.json"

    # Every asset traces back to the exact saved Brand Kit revision.
    assert {a.brand_kit_version for a in result.assets} == {2}

    # The Brand Kit was persisted versioned, and its URL returned.
    saved_keys = [key for key, _data, _ct in backend.put_calls]
    assert "brandkits/acme/v2.json" in saved_keys
    assert result.brand_kit_url.startswith("https://cdn.example/brandkits/acme/v2.json")


@pytest.mark.unit
def test_run_campaign_rejects_brand_id_mismatch():
    mismatched = Campaign(id="c9", brand_kit_id="other", theme="x", num_variants=1)
    backend = FakeBackend()
    with pytest.raises(PipelineError):
        run_campaign(_settings(), _kit(), mismatched, backend=backend)
    # Fail-fast before any I/O: a mismatched campaign never persists a Brand Kit.
    assert backend.put_calls == []


@pytest.mark.unit
def test_run_campaign_defaults_created_at_when_omitted(monkeypatch):
    monkeypatch.setattr(pipeline, "build_image_pipeline", _fake_pipe_factory(1))

    result = run_campaign(_settings(), _kit(), _camp(1), backend=FakeBackend(), choice=_choice())

    # created_at was defaulted to an ISO-8601 UTC timestamp and stamped onto assets.
    stamped = result.assets[0].created_at
    assert stamped and stamped.endswith("+00:00")


@pytest.mark.unit
def test_run_campaign_propagates_generation_failure(monkeypatch):
    fail_result = types.SimpleNamespace(
        run=types.SimpleNamespace(steps=[], run_id="run-x"),
        manifest=types.SimpleNamespace(manifest_uri="brandforge/run-x/manifest.json"),
        failed_steps=lambda: ["boom"],
        error_summary=lambda: "step 0 failed",
    )

    class FailPipe:
        def run(self, *, sink=None, timeout=None, raise_on_failure=None):
            return fail_result

    monkeypatch.setattr(pipeline, "build_image_pipeline", lambda *a, **k: FailPipe())
    backend = FakeBackend()

    with pytest.raises(PipelineError) as excinfo:
        run_campaign(_settings(), _kit(), _camp(2), backend=backend, choice=_choice())
    # A failed generation surfaces as PipelineError with the orphaned-upload locators.
    assert "run-x" in str(excinfo.value)
    # The Brand Kit was already saved (it precedes generation), so cleanup can find it.
    assert any(key == "brandkits/acme/v2.json" for key, _d, _c in backend.put_calls)


@pytest.mark.unit
def test_run_campaign_uses_injected_choice_without_picking(monkeypatch):
    monkeypatch.setattr(pipeline, "build_image_pipeline", _fake_pipe_factory(1))

    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("pick_image_provider must not be called when choice is given")

    monkeypatch.setattr(campaign_mod, "pick_image_provider", _boom)

    result = run_campaign(
        _settings(), _kit(), _camp(1), backend=FakeBackend(), choice=_choice(), created_at="t"
    )

    assert len(result.assets) == 1
    assert result.assets[0].provider == "openai:gpt-image-1"


@pytest.mark.unit
def test_run_campaign_updates_index_by_default(monkeypatch):
    monkeypatch.setattr(pipeline, "build_image_pipeline", _fake_pipe_factory(3))
    backend = FakeBackend()

    run_campaign(_settings(), _kit(), _camp(3), backend=backend, choice=_choice(), created_at="t")

    # The campaign's assets are now queryable from the Parquet catalog.
    indexed = query_assets(_settings(), campaign_id="c1", backend=backend)
    assert len(indexed) == 3
    assert ASSET_INDEX_KEY in backend.store


@pytest.mark.unit
def test_run_campaign_skips_index_when_disabled(monkeypatch):
    monkeypatch.setattr(pipeline, "build_image_pipeline", _fake_pipe_factory(3))
    backend = FakeBackend()

    run_campaign(
        _settings(),
        _kit(),
        _camp(3),
        backend=backend,
        choice=_choice(),
        created_at="t",
        update_index=False,
    )

    assert ASSET_INDEX_KEY not in backend.store


@pytest.mark.unit
def test_run_campaign_wraps_index_failure_as_pipeline_error(monkeypatch):
    monkeypatch.setattr(pipeline, "build_image_pipeline", _fake_pipe_factory(2))

    def _boom(*_a, **_k):
        raise RuntimeError("b2 down")

    monkeypatch.setattr(campaign_mod, "index_assets", _boom)

    with pytest.raises(PipelineError) as excinfo:
        run_campaign(
            _settings(), _kit(), _camp(2), backend=FakeBackend(), choice=_choice(), created_at="t"
        )
    # The generated set is already in B2; the error carries its locators for re-indexing.
    assert "run1" in str(excinfo.value)
    assert "brandforge/run1/manifest.json" in str(excinfo.value)
