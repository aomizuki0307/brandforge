"""Tests for Genblaze generation orchestration.

The Genblaze Pipeline/provider calls are never executed against a network here:
provider-selection and prompt-guard logic run for real, while the run step is
replaced with a fake result so mapping and error handling are verified in
isolation. All tests are therefore ``unit`` (no external services).
"""

import types

import pytest

from app import pipeline
from app.config import Settings
from app.models import BrandKit, Campaign
from app.pipeline import (
    PipelineError,
    ProviderChoice,
    _key_from_url,
    build_image_pipeline,
    generate_images,
    map_result_to_assets,
    pick_image_provider,
)


def _settings(*, openai=True, gmi=False) -> Settings:
    return Settings(
        b2_key_id="k",
        b2_app_key="a",
        b2_bucket="b",
        b2_region="r",
        gmicloud_api_key="g" if gmi else None,
        openai_api_key="sk-test" if openai else None,
        anthropic_api_key=None,
        public_base_url=None,
    )


def _kit() -> BrandKit:
    return BrandKit(id="acme", name="Acme", version=2, tone_words=["bold"], style_prompt="flat")


def _camp(n: int = 2) -> Campaign:
    return Campaign(id="c1", brand_kit_id="acme", theme="spring launch", num_variants=n)


def _choice() -> ProviderChoice:
    return ProviderChoice(provider=object(), model="dall-e-3", name="openai:dall-e-3")


class _KeyBackend:
    """Stub backend exposing only key_from_url with a canned return value."""

    def __init__(self, ret):
        self._ret = ret

    def key_from_url(self, url):
        return self._ret


@pytest.mark.unit
def test_pick_image_provider_prefers_openai_by_default():
    choice = pick_image_provider(_settings(openai=True, gmi=True))
    assert choice.name.startswith("openai:")
    assert choice.model == pipeline.IMAGE_MODEL_OPENAI


@pytest.mark.unit
def test_pick_image_provider_uses_gmi_when_preferred():
    choice = pick_image_provider(_settings(openai=True, gmi=True), prefer="gmicloud")
    assert choice.name.startswith("gmicloud:")


@pytest.mark.unit
def test_pick_image_provider_falls_back_to_openai_when_gmi_absent():
    choice = pick_image_provider(_settings(openai=True, gmi=False), prefer="gmicloud")
    assert choice.name.startswith("openai:")


@pytest.mark.unit
def test_pick_image_provider_raises_when_none_configured():
    with pytest.raises(PipelineError):
        pick_image_provider(_settings(openai=False, gmi=False))


@pytest.mark.unit
def test_pick_image_provider_rejects_unknown_prefer():
    with pytest.raises(PipelineError):
        pick_image_provider(_settings(openai=True), prefer="gmi")  # type: ignore[arg-type]


@pytest.mark.unit
def test_key_from_url_falls_back_when_backend_returns_none():
    backend = _KeyBackend(None)
    assert _key_from_url("https://h/brandforge/x/a.png", backend) == "brandforge/x/a.png"


@pytest.mark.unit
def test_key_from_url_uses_backend_key_when_present():
    backend = _KeyBackend("brandforge/x/a.png")
    assert _key_from_url("https://h/whatever", backend) == "brandforge/x/a.png"


@pytest.mark.unit
def test_map_result_to_assets_carries_provenance():
    media = types.SimpleNamespace(
        asset_id="a1", url="https://b/brandforge/x/a1.png", sha256="deadbeef"
    )
    step = types.SimpleNamespace(model="dall-e-3", prompt="p", assets=[media])
    result = types.SimpleNamespace(
        run=types.SimpleNamespace(steps=[step]),
        manifest=types.SimpleNamespace(manifest_uri="brandforge/x/manifest.json"),
    )

    assets = map_result_to_assets(result, _kit(), _camp(), _choice(), "2026-07-01T00:00:00Z")

    assert len(assets) == 1
    asset = assets[0]
    assert asset.id == "a1"
    assert asset.sha256 == "deadbeef"
    assert asset.b2_key == "brandforge/x/a1.png"
    assert asset.brand_kit_version == 2
    assert asset.manifest_b2_key == "brandforge/x/manifest.json"
    assert asset.provider == "openai:dall-e-3"
    assert asset.modality == "image"


@pytest.mark.unit
def test_build_image_pipeline_blocks_unsafe_prompt():
    # A secret-looking token in the theme must trip the prompt guard.
    unsafe = Campaign(
        id="c1",
        brand_kit_id="acme",
        theme="leak sk-abcdefghijklmnopqrstuvwxyz012345",
        num_variants=1,
    )
    with pytest.raises(PipelineError):
        build_image_pipeline(_kit(), unsafe, _choice())


@pytest.mark.unit
def test_generate_images_maps_and_returns(monkeypatch):
    media = types.SimpleNamespace(
        asset_id="a1", url="https://b/brandforge/x/a1.png", sha256="beef"
    )
    step = types.SimpleNamespace(model="dall-e-3", prompt="p", assets=[media])
    fake_result = types.SimpleNamespace(
        run=types.SimpleNamespace(steps=[step], run_id="run-1"),
        manifest=types.SimpleNamespace(manifest_uri="brandforge/x/m.json"),
        failed_steps=lambda: [],
        error_summary=lambda: None,
    )

    class FakePipe:
        def run(self, *, sink=None, timeout=None, raise_on_failure=None):
            return fake_result

    monkeypatch.setattr(pipeline, "build_image_pipeline", lambda *a, **k: FakePipe())

    result = generate_images(
        _settings(), _kit(), _camp(1), created_at="t", sink=None, choice=_choice()
    )

    assert len(result.assets) == 1
    assert result.manifest.manifest_uri == "brandforge/x/m.json"


@pytest.mark.unit
def test_generate_images_raises_on_failed_step(monkeypatch):
    fake_result = types.SimpleNamespace(
        run=types.SimpleNamespace(steps=[], run_id="run-2"),
        manifest=types.SimpleNamespace(manifest_uri="brandforge/x/m.json"),
        failed_steps=lambda: ["boom"],
        error_summary=lambda: "step 0 failed",
    )

    class FakePipe:
        def run(self, *, sink=None, timeout=None, raise_on_failure=None):
            return fake_result

    monkeypatch.setattr(pipeline, "build_image_pipeline", lambda *a, **k: FakePipe())

    with pytest.raises(PipelineError) as excinfo:
        generate_images(
            _settings(), _kit(), _camp(1), created_at="t", sink=None, choice=_choice()
        )
    # The error carries locators for the orphaned upload.
    assert "run-2" in str(excinfo.value)
    assert "brandforge/x/m.json" in str(excinfo.value)
