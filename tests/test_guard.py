"""Unit tests for the content safety guard."""

import pytest

from app.guard import check_caption, check_prompt


@pytest.mark.unit
def test_allows_clean_caption():
    # Arrange
    text = "新作スニーカー、夏の足元を軽やかに。#summer #sneakers"

    # Act
    result = check_caption(text, platform="instagram")

    # Assert
    assert result.allowed is True
    assert result.reason == "ok"


@pytest.mark.unit
def test_blocks_too_short():
    result = check_caption("hi", platform="x")
    assert result.allowed is False
    assert "too_short" in result.reason


@pytest.mark.unit
def test_blocks_over_platform_limit():
    # X caps at 280 chars; this exceeds it but would pass on Instagram.
    long_text = "あ" * 300
    assert check_caption(long_text, platform="x").allowed is False
    assert check_caption(long_text, platform="instagram").allowed is True


@pytest.mark.unit
def test_blocks_leaked_secret():
    result = check_caption("my api_key=sk-abcdef0123456789abcdef0123 here we go", platform="x")
    assert result.allowed is False
    assert result.reason == "secret_pattern_detected"


@pytest.mark.unit
def test_blocks_salesy_spam():
    result = check_caption("相互フォローしましょう！今すぐ購入してね", platform="x")
    assert result.allowed is False
    assert result.reason == "salesy_spam"


@pytest.mark.unit
def test_prompt_guard_rejects_empty():
    assert check_prompt("   ").allowed is False


@pytest.mark.unit
def test_prompt_guard_blocks_secret():
    assert check_prompt("render this, token: ghp_" + "a" * 36).allowed is False
