"""Unit tests for brand style injection."""

import pytest

from app.brandkit import (
    brand_context_for_caption,
    compose_image_prompt,
    compose_video_prompt,
)
from app.models import BrandKit


def _kit() -> BrandKit:
    return BrandKit(
        id="k1",
        name="Acme",
        palette=["#1a1a1a", "#f5f5f5"],
        tone_words=["minimal", "warm"],
        style_prompt="soft natural light, editorial photography",
        audience="20s creators",
    )


@pytest.mark.unit
def test_image_prompt_includes_brand_signals():
    prompt = compose_image_prompt(_kit(), theme="summer sneakers", variant_idx=0)
    assert "summer sneakers" in prompt
    assert "editorial photography" in prompt
    assert "#1a1a1a" in prompt
    assert "minimal" in prompt


@pytest.mark.unit
def test_variants_differ_but_stay_on_brand():
    kit = _kit()
    p0 = compose_image_prompt(kit, "summer sneakers", 0)
    p1 = compose_image_prompt(kit, "summer sneakers", 1)
    assert p0 != p1  # different framing nudge
    assert "editorial photography" in p0 and "editorial photography" in p1


@pytest.mark.unit
def test_variant_index_wraps():
    kit = _kit()
    # index beyond the nudge list must not raise
    assert compose_image_prompt(kit, "x", 99)


@pytest.mark.unit
def test_video_prompt_has_motion():
    prompt = compose_video_prompt(_kit(), "summer sneakers")
    assert "camera motion" in prompt
    assert "summer sneakers" in prompt


@pytest.mark.unit
def test_caption_context_lists_tone():
    ctx = brand_context_for_caption(_kit())
    assert "Acme" in ctx
    assert "minimal" in ctx
