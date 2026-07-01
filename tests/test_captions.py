"""Unit tests for caption generation (OpenAI call is mocked)."""

import pytest

import app.captions as captions
from app.captions import CaptionError, generate_caption
from app.models import BrandKit


def _kit() -> BrandKit:
    return BrandKit(id="k1", name="Acme", tone_words=["warm"], audience="creators")


@pytest.mark.unit
def test_extracts_from_code_block(monkeypatch):
    monkeypatch.setattr(
        captions,
        "_call_openai",
        lambda system, user, model: "```caption\n夏の新作、軽やかに。\n```",
    )
    cap = generate_caption(_kit(), theme="summer", platform="x", hashtags=["#夏"])
    assert cap.text == "夏の新作、軽やかに。"
    assert cap.platform == "x"
    assert cap.hashtags == ["#夏"]


@pytest.mark.unit
def test_trims_when_too_long_for_platform(monkeypatch):
    long_text = "あ" * 400
    monkeypatch.setattr(captions, "_call_openai", lambda s, u, m: long_text)
    cap = generate_caption(_kit(), theme="t", platform="x")
    assert len(cap.text) <= 280


@pytest.mark.unit
def test_rejects_unsafe_content(monkeypatch):
    monkeypatch.setattr(
        captions,
        "_call_openai",
        lambda s, u, m: "follow for follow! dm me now",
    )
    with pytest.raises(CaptionError):
        generate_caption(_kit(), theme="t", platform="x")
