"""Brand style injection.

The whole point of BrandForge is consistency: every generated asset in a
campaign must share one look. We achieve that by composing every generation
prompt from the same Brand Kit fields, adding only a small deterministic nudge
per variant so the set has variety without drifting off-brand.
"""

from __future__ import annotations

from app.models import BrandKit

# Deterministic per-variant framing so a set of images differs in composition
# but not in brand identity. Indexed by variant number (wraps around).
_VARIANT_NUDGES = [
    "hero composition, centered subject",
    "close-up detail shot",
    "wide environmental shot with negative space",
    "flat-lay top-down arrangement",
    "dynamic diagonal composition",
    "minimal product-on-surface shot",
    "lifestyle in-context scene",
    "bold graphic poster layout",
]


def _brand_style_clause(brand: BrandKit) -> str:
    parts: list[str] = []
    if brand.style_prompt:
        parts.append(brand.style_prompt.strip())
    if brand.tone_words:
        parts.append("tone: " + ", ".join(brand.tone_words))
    if brand.palette:
        parts.append("color palette: " + ", ".join(brand.palette))
    return ". ".join(parts)


def compose_image_prompt(brand: BrandKit, theme: str, variant_idx: int = 0) -> str:
    """Build a single, on-brand image prompt for the given theme and variant."""
    nudge = _VARIANT_NUDGES[variant_idx % len(_VARIANT_NUDGES)]
    style = _brand_style_clause(brand)
    segments = [theme.strip(), nudge]
    if style:
        segments.append(style)
    segments.append("cohesive brand identity, high quality, no text overlay")
    return ". ".join(s for s in segments if s)


def compose_video_prompt(brand: BrandKit, theme: str) -> str:
    """Build a short-video motion prompt that keeps the brand look."""
    style = _brand_style_clause(brand)
    segments = [
        theme.strip(),
        "smooth subtle camera motion, short looping clip",
    ]
    if style:
        segments.append(style)
    return ". ".join(s for s in segments if s)


def brand_context_for_caption(brand: BrandKit) -> str:
    """Human-readable brand summary injected into the caption model prompt."""
    lines = [f"Brand: {brand.name}", f"Audience: {brand.audience or 'general'}"]
    if brand.tone_words:
        lines.append("Tone: " + ", ".join(brand.tone_words))
    lines.append(f"Locale: {brand.locale}")
    return "\n".join(lines)
