"""Unit tests for domain schemas."""

import pytest
from pydantic import ValidationError

from app.models import Asset, BrandKit, Campaign


@pytest.mark.unit
def test_brandkit_defaults():
    kit = BrandKit(id="k1", name="Acme")
    assert kit.version == 1
    assert kit.platforms == ["x", "instagram"]
    assert kit.locale == "ja"


@pytest.mark.unit
def test_campaign_variant_bounds():
    with pytest.raises(ValidationError):
        Campaign(id="c1", brand_kit_id="k1", theme="summer", num_variants=99)


@pytest.mark.unit
def test_campaign_requires_theme():
    with pytest.raises(ValidationError):
        Campaign(id="c1", brand_kit_id="k1", theme="")


@pytest.mark.unit
def test_asset_roundtrip():
    asset = Asset(
        id="a1",
        campaign_id="c1",
        brand_kit_id="k1",
        brand_kit_version=1,
        modality="image",
        provider="openai",
        model="gpt-image",
        prompt="a cat",
        b2_key="campaigns/c1/a1.png",
        url="https://cdn/x.png",
        created_at="2026-07-01T00:00:00Z",
    )
    assert asset.modality == "image"
    assert asset.sha256 is None
