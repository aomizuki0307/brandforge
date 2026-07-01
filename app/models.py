"""Domain schemas for BrandForge.

Pydantic models validate all data at the system boundary (API input, provider
output) before it flows into generation or storage.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Modality = Literal["image", "video", "audio"]
Platform = Literal["x", "instagram", "threads", "linkedin"]

# Identifiers are interpolated raw into B2 object keys (brandkits/<id>/..., the
# pipeline name) and must stay path-safe, so restrict them to a conservative
# slug charset — no "/" or other separators that could collide key namespaces.
ID_PATTERN = r"^[A-Za-z0-9_-]+$"
ID_MAX_LENGTH = 64


class BrandKit(BaseModel):
    """A brand's visual + voice identity. Versioned so assets can record which
    revision produced them (stored in B2 under brandkits/<id>/v<version>)."""

    id: str = Field(..., min_length=1, max_length=ID_MAX_LENGTH, pattern=ID_PATTERN)
    name: str = Field(..., min_length=1)
    version: int = Field(default=1, ge=1)
    palette: list[str] = Field(default_factory=list, description="Hex colors, e.g. #1a1a1a")
    tone_words: list[str] = Field(default_factory=list, description="e.g. minimal, warm, bold")
    style_prompt: str = Field(default="", description="Reusable style fragment for generation")
    audience: str = Field(default="")
    platforms: list[Platform] = Field(default_factory=lambda: ["x", "instagram"])
    locale: str = Field(default="ja")
    logo_b2_key: str | None = None


class Campaign(BaseModel):
    """A single generation request for one brand + theme."""

    id: str = Field(..., min_length=1, max_length=ID_MAX_LENGTH, pattern=ID_PATTERN)
    brand_kit_id: str = Field(..., min_length=1, max_length=ID_MAX_LENGTH, pattern=ID_PATTERN)
    theme: str = Field(..., min_length=1, max_length=500, description="What the campaign is about")
    num_variants: int = Field(default=3, ge=1, le=8)
    want_video: bool = False


class Asset(BaseModel):
    """A generated media asset with provenance, addressed in B2."""

    id: str
    campaign_id: str
    brand_kit_id: str
    brand_kit_version: int
    modality: Modality
    provider: str
    model: str
    prompt: str
    b2_key: str
    url: str
    sha256: str | None = None
    manifest_b2_key: str | None = None
    created_at: str = Field(..., description="ISO-8601 timestamp set by the caller")


class Caption(BaseModel):
    """A platform-targeted caption for an asset."""

    platform: Platform
    text: str
    hashtags: list[str] = Field(default_factory=list)
