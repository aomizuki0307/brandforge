"""Genblaze generation orchestration.

Every asset in a campaign is produced from the same Brand Kit (see
``app.brandkit``), so a set of images shares one look. Prompts are guarded
before they reach a provider, all variants of a campaign run under a single
``Pipeline`` (one manifest per campaign), and the provider-agnostic Genblaze
result is mapped back to our own ``Asset`` schema for storage and display.

Provider selection is OpenAI-first for now (GMI Cloud free credits pending);
switch ``prefer`` to ``"gmicloud"`` once credits land to route through
Seedance/Kling for the video chain.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from genblaze_core.models import Manifest, Modality as GenModality, Run
from genblaze_core.pipeline import Pipeline, PipelineResult
from genblaze_core.providers.base import BaseProvider
from genblaze_core.sinks.base import BaseSink
from genblaze_core.storage import StorageBackend
from genblaze_openai import DalleProvider
from genblaze_gmicloud import GMICloudImageProvider

from app.brandkit import compose_image_prompt
from app.config import Settings
from app.genblaze_compat import apply as _apply_genblaze_compat
from app.guard import check_prompt
from app.models import Asset, BrandKit, Campaign, Modality as AssetModality

# Fix Genblaze's Windows file:// asset URLs before any pipeline runs.
_apply_genblaze_compat()

# Model identifiers. OpenAI is confirmed against this account's live catalog
# (DALL-E is retired; gpt-image is what's available). GMI strings are
# placeholders to confirm against the live catalog when GMI credits arrive.
IMAGE_MODEL_OPENAI = "gpt-image-1"
IMAGE_MODEL_GMI = "seedream-4-0"  # TODO: confirm exact GMI image model id
DEFAULT_IMAGE_SIZE = "1024x1024"
DEFAULT_TIMEOUT = 300.0


class PipelineError(RuntimeError):
    """Raised when generation cannot proceed (blocked prompt, provider missing,
    or a step failed)."""


Provider = Literal["openai", "gmicloud"]


@dataclass(frozen=True)
class ProviderChoice:
    """A resolved (provider instance, model id, display name) triple."""

    provider: BaseProvider
    model: str
    name: str


@dataclass(frozen=True)
class GenerationResult:
    """Mapped assets plus the raw Genblaze provenance objects."""

    assets: list[Asset]
    manifest: Manifest
    run: Run


def _order(prefer: Provider) -> tuple[Provider, ...]:
    return ("gmicloud", "openai") if prefer == "gmicloud" else ("openai", "gmicloud")


def _gen_output_dir() -> str:
    """Scratch directory for provider downloads.

    Genblaze sinks only transfer files that live under the system temp or the
    provider's ``output_dir``; the default (CWD) is rejected, so we pin an
    allowed temp dir.
    """
    path = Path(tempfile.gettempdir()) / "brandforge-gen"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def pick_image_provider(settings: Settings, *, prefer: Provider = "openai") -> ProviderChoice:
    """Resolve an image provider, honouring ``prefer`` then falling back.

    OpenAI is the default because GMI free credits are still pending; the GMI
    branch is wired but only selected when ``prefer="gmicloud"`` and a key is set.
    """
    if prefer not in ("openai", "gmicloud"):
        raise PipelineError(f"Unknown provider preference: {prefer!r}")
    output_dir = _gen_output_dir()
    for name in _order(prefer):
        if name == "openai" and settings.has_openai:
            return ProviderChoice(
                DalleProvider(api_key=settings.openai_api_key, output_dir=output_dir),
                IMAGE_MODEL_OPENAI,
                f"openai:{IMAGE_MODEL_OPENAI}",
            )
        if name == "gmicloud" and settings.has_gmicloud:
            # GMI returns cloud HTTPS URLs (no local file), so no output_dir.
            return ProviderChoice(
                GMICloudImageProvider(api_key=settings.gmicloud_api_key),
                IMAGE_MODEL_GMI,
                f"gmicloud:{IMAGE_MODEL_GMI}",
            )
    raise PipelineError(
        "No image provider configured: set OPENAI_API_KEY or GMICLOUD_API_KEY."
    )


def build_image_pipeline(
    brand: BrandKit,
    campaign: Campaign,
    choice: ProviderChoice,
    *,
    image_size: str = DEFAULT_IMAGE_SIZE,
) -> Pipeline:
    """Assemble one Pipeline whose N steps are the campaign's on-brand variants.

    Each prompt is guarded before being added; a blocked prompt aborts the whole
    campaign rather than silently dropping a variant.
    """
    pipe = Pipeline(f"brandforge-{campaign.id}-images")
    for i in range(campaign.num_variants):
        prompt = compose_image_prompt(brand, campaign.theme, i)
        guard = check_prompt(prompt)
        if not guard.allowed:
            raise PipelineError(f"prompt blocked for variant {i}: {guard.reason}")
        pipe.step(
            choice.provider,
            model=choice.model,
            prompt=prompt,
            modality=GenModality.IMAGE,
            size=image_size,
        )
    return pipe


def _key_from_url(url: str, backend: StorageBackend | None = None) -> str:
    """Best-effort storage key from an asset URL.

    Uses the backend's own parser when available, otherwise falls back to the
    URL path (sufficient for display/traceability). ``key_from_url`` returns
    ``None`` for URLs it doesn't own, so we must fall through in that case
    rather than stringify ``None`` into the key.
    """
    parser = getattr(backend, "key_from_url", None)
    if callable(parser):
        try:
            key = parser(url)
        except Exception:  # pragma: no cover - defensive, fall through to parse
            key = None
        if key is not None:
            return str(key)
    return urlparse(url).path.lstrip("/")


def map_result_to_assets(
    result: PipelineResult,
    brand: BrandKit,
    campaign: Campaign,
    choice: ProviderChoice,
    created_at: str,
    *,
    modality: AssetModality = "image",
    backend: StorageBackend | None = None,
) -> list[Asset]:
    """Flatten a Genblaze run into our ``Asset`` schema with full provenance."""
    manifest_uri = result.manifest.manifest_uri
    assets: list[Asset] = []
    for step in result.run.steps:
        for media in step.assets:
            assets.append(
                Asset(
                    id=media.asset_id,
                    campaign_id=campaign.id,
                    brand_kit_id=brand.id,
                    brand_kit_version=brand.version,
                    modality=modality,
                    provider=choice.name,
                    model=step.model,
                    prompt=step.prompt or "",
                    b2_key=_key_from_url(media.url, backend),
                    url=media.url,
                    sha256=media.sha256,
                    manifest_b2_key=manifest_uri,
                    created_at=created_at,
                )
            )
    return assets


def generate_images(
    settings: Settings,
    brand: BrandKit,
    campaign: Campaign,
    *,
    created_at: str,
    sink: BaseSink | None,
    choice: ProviderChoice | None = None,
    backend: StorageBackend | None = None,
    image_size: str = DEFAULT_IMAGE_SIZE,
    timeout: float = DEFAULT_TIMEOUT,
) -> GenerationResult:
    """Generate a campaign's image set, store it via ``sink``, and map results.

    Raises ``PipelineError`` if any step fails so callers never persist a
    partially-successful campaign as if it were complete. Note that a failed
    multi-variant run may already have uploaded its successful variants and a
    manifest to B2, so the error carries the ``run_id``/``manifest_uri`` needed
    to locate and reap them.
    """
    choice = choice or pick_image_provider(settings)
    pipe = build_image_pipeline(brand, campaign, choice, image_size=image_size)
    # raise_on_failure=False keeps our explicit failed_steps() check authoritative
    # and pins today's behaviour against the genblaze-core 0.4.0 default flip.
    result = pipe.run(sink=sink, timeout=timeout, raise_on_failure=False)
    if result.failed_steps():
        detail = result.error_summary() or "image generation failed"
        raise PipelineError(
            f"{detail} [run_id={result.run.run_id} manifest={result.manifest.manifest_uri}]"
        )
    assets = map_result_to_assets(
        result, brand, campaign, choice, created_at, modality="image", backend=backend
    )
    return GenerationResult(assets=assets, manifest=result.manifest, run=result.run)
