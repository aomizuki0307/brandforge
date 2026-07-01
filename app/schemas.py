"""HTTP request/response schemas for the web layer.

Kept separate from the domain models (``app.models``) so the wire contract can
evolve independently and, crucially, so responses expose only what a client
needs. ``AssetOut`` deliberately omits ``prompt`` and the internal ``provider``
string: the gallery shows images and provenance, not the raw generation inputs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models import Asset, BrandKit, Campaign, Modality


class CampaignRequest(BaseModel):
    """Body for ``POST /campaigns``: a brand plus the campaign to generate."""

    brand: BrandKit
    campaign: Campaign


class BrandKitOut(BaseModel):
    """Result of persisting a Brand Kit revision."""

    brand_kit_key: str
    url: str


class AssetOut(BaseModel):
    """A gallery-safe view of a generated asset (no prompt/provider internals)."""

    id: str
    campaign_id: str
    brand_kit_id: str
    brand_kit_version: int
    modality: Modality
    model: str
    b2_key: str
    url: str
    sha256: str | None = None
    manifest_b2_key: str | None = None
    created_at: str

    @classmethod
    def from_asset(cls, asset: Asset) -> "AssetOut":
        return cls(
            id=asset.id,
            campaign_id=asset.campaign_id,
            brand_kit_id=asset.brand_kit_id,
            brand_kit_version=asset.brand_kit_version,
            modality=asset.modality,
            model=asset.model,
            b2_key=asset.b2_key,
            url=asset.url,
            sha256=asset.sha256,
            manifest_b2_key=asset.manifest_b2_key,
            created_at=asset.created_at,
        )


class CampaignOut(BaseModel):
    """Result of a generation run: the saved brand, its manifest, and assets."""

    brand_kit_url: str
    manifest_uri: str
    assets: list[AssetOut] = Field(default_factory=list)
