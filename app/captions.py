"""Platform-targeted caption generation.

Ported from the X-growth `tweet_generator.py` pattern (system-prompt-as-markdown
+ code-block extraction + trim), but routed through OpenAI so it runs with the
already-available OPENAI_API_KEY. The brand voice lives in prompts/brand_voice.md
so captions can be retuned without code changes.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import cast

from app.brandkit import brand_context_for_caption
from app.guard import check_caption
from app.models import BrandKit, Caption, Platform

logger = logging.getLogger(__name__)

_VOICE_PATH = Path(__file__).resolve().parent.parent / "prompts" / "brand_voice.md"
_DEFAULT_MODEL = os.getenv("OPENAI_CAPTION_MODEL", "gpt-5-mini")


class CaptionError(RuntimeError):
    """Raised when a caption cannot be generated safely."""


def _load_voice() -> str:
    if not _VOICE_PATH.exists():
        raise FileNotFoundError(f"Brand voice prompt not found: {_VOICE_PATH}")
    return _VOICE_PATH.read_text(encoding="utf-8")


def _call_openai(system: str, user: str, model: str) -> str:
    """Single chat completion. Imported lazily so unit tests can monkeypatch this
    without the openai package or an API key present."""
    import openai

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise CaptionError("OPENAI_API_KEY not set")

    client = openai.OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _extract(raw: str) -> str:
    match = re.search(r"```(?:caption)?\s*(.*?)\s*```", raw, re.DOTALL)
    return match.group(1).strip() if match else raw.strip()


def generate_caption(
    brand: BrandKit,
    theme: str,
    platform: str,
    hashtags: list[str] | None = None,
    model: str | None = None,
) -> Caption:
    """Generate one on-brand caption for a platform, guarded for safety."""
    hashtags = hashtags or []
    system = _load_voice()
    user = (
        f"{brand_context_for_caption(brand)}\n\n"
        f"Platform: {platform}\n"
        f"Campaign theme: {theme}\n"
    )
    if hashtags:
        user += f"End with these hashtags: {' '.join(hashtags)}\n"

    raw = _call_openai(system, user, model or _DEFAULT_MODEL)
    text = _extract(raw)

    result = check_caption(text, platform=platform)
    if not result.allowed:
        # Length is recoverable (trim); unsafe content is not.
        if result.reason.startswith("too_long"):
            logger.warning("Caption too long for %s, trimming.", platform)
            text = text[: _platform_limit(platform)]
        else:
            raise CaptionError(f"Caption rejected by guard: {result.reason}")

    # pydantic validates the platform against the Platform literal at runtime.
    return Caption(platform=cast(Platform, platform), text=text, hashtags=hashtags)


def _platform_limit(platform: str) -> int:
    from app.guard import _PLATFORM_MAX_LENGTH

    return _PLATFORM_MAX_LENGTH.get(platform.lower(), _PLATFORM_MAX_LENGTH["default"])
