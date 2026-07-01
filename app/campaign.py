"""Campaign driver: one call that runs a whole Brand Kit -> image set.

This is the thin layer that ties the two halves of Phase 2 together — Brand
Kit persistence (``app.storage``) and multi-variant generation
(``app.pipeline``) — so a caller (smoke script, and later the FastAPI
``POST /campaigns`` route) has a single entry point instead of re-assembling
the flow each time.

The heavy lifting already lives below us: ``build_image_pipeline`` builds one
Pipeline with ``campaign.num_variants`` steps and ``generate_images`` runs it as
a single unit, so a campaign yields **one manifest for the whole set** with
every variant tracing back to it. This driver just orders the steps, saves the
brand revision the assets were produced from, and returns them together.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from genblaze_core.models import Manifest, Run
from genblaze_s3 import S3StorageBackend

from app.config import Settings
from app.index import index_assets
from app.models import Asset, BrandKit, Campaign
from app.pipeline import (
    DEFAULT_IMAGE_SIZE,
    DEFAULT_TIMEOUT,
    PipelineError,
    Provider,
    ProviderChoice,
    generate_images,
    pick_image_provider,
)
from app.storage import make_backend, make_sink, save_brand_kit


@dataclass(frozen=True)
class CampaignResult:
    """A campaign's saved Brand Kit plus its generated image set.

    ``assets`` are the campaign's variants, all sharing the single
    ``manifest`` (see ``Asset.manifest_b2_key``); ``run`` carries the raw
    Genblaze provenance for the whole set.
    """

    brand_kit_url: str
    assets: list[Asset]
    manifest: Manifest
    run: Run


def run_campaign(
    settings: Settings,
    brand: BrandKit,
    campaign: Campaign,
    *,
    created_at: str | None = None,
    backend: S3StorageBackend | None = None,
    choice: ProviderChoice | None = None,
    prefer: Provider = "openai",
    image_size: str = DEFAULT_IMAGE_SIZE,
    timeout: float = DEFAULT_TIMEOUT,
    update_index: bool = True,
) -> CampaignResult:
    """Save the Brand Kit, generate the campaign's variant set, return both.

    Fails fast if ``campaign`` does not belong to ``brand`` so we never persist
    a Brand Kit revision and then attach assets produced from a different brand.
    A single backend is threaded through kit-save, sink, and generation so the
    whole campaign targets one bucket in one pass.

    With ``update_index`` (default), the new assets are folded into the Parquet
    catalog (``app.index``) after generation so the gallery is always current.
    Indexing runs only once the assets and their manifest are durably in B2, so
    a later index failure surfaces as ``PipelineError`` without losing the
    generated set (re-running ``index_assets`` recovers the catalog).
    """
    if campaign.brand_kit_id != brand.id:
        raise PipelineError(
            f"campaign {campaign.id!r} targets brand_kit_id "
            f"{campaign.brand_kit_id!r}, not the given brand {brand.id!r}"
        )

    backend = backend or make_backend(settings)
    sink = make_sink(settings, backend=backend)
    created_at = created_at or datetime.now(timezone.utc).isoformat()

    brand_kit_url = save_brand_kit(settings, brand, backend=backend)
    choice = choice or pick_image_provider(settings, prefer=prefer)
    gen = generate_images(
        settings,
        brand,
        campaign,
        created_at=created_at,
        sink=sink,
        choice=choice,
        backend=backend,
        image_size=image_size,
        timeout=timeout,
    )
    if update_index:
        try:
            index_assets(settings, gen.assets, backend=backend)
        except Exception as exc:  # noqa: BLE001 - re-raised with locators below
            # Assets + manifest are already durably in B2; surface a locatable
            # error (matching generate_images' style) so the set can be re-indexed.
            raise PipelineError(
                f"asset indexing failed after generation "
                f"[run_id={gen.run.run_id} manifest={gen.manifest.manifest_uri}]"
            ) from exc
    return CampaignResult(
        brand_kit_url=brand_kit_url,
        assets=gen.assets,
        manifest=gen.manifest,
        run=gen.run,
    )
