"""Backblaze B2 storage layer for BrandForge.

All durable state lives in one B2 bucket: generated media (written by the
Genblaze ``ObjectStorageSink`` with co-located provenance manifests), and
Brand Kit revisions (versioned JSON written directly through the backend).

Keys are hierarchical so a bucket listing reads like a catalog:

    brandforge/<run>/...            generated assets + manifests (Genblaze sink)
    brandkits/<id>/v<version>.json  Brand Kit revisions (this module)

Public delivery URLs are produced via ``URLPolicy.PUBLIC`` so a public bucket
serves the gallery directly; flip ``public=False`` for presigned URLs instead.
"""

from __future__ import annotations

from genblaze_core.storage import KeyStrategy, ObjectStorageSink, URLPolicy
from genblaze_s3 import S3StorageBackend

from app.config import Settings
from app.models import BrandKit

DEFAULT_PREFIX = "brandforge"
BRANDKIT_PREFIX = "brandkits"


def _public_base(settings: Settings) -> str | None:
    """Resolve the public delivery base URL for the bucket, or ``None``.

    This is intentionally explicit: only set ``BRANDFORGE_PUBLIC_BASE_URL``
    (a CDN, or the bucket's virtual-hosted S3 URL
    ``https://<bucket>.s3.<region>.backblazeb2.com`` for a *public* bucket).
    When unset, the bucket is treated as private and delivery falls back to
    presigned URLs — so a private bucket never advertises unreachable public
    links.
    """
    return settings.public_base_url


def _resolve_policy(backend: object, public: bool) -> URLPolicy:
    """Use PUBLIC only when the backend actually exposes a public base URL;
    otherwise AUTO, so a private/unconfigured bucket never raises."""
    if public and getattr(backend, "public_url_base", None):
        return URLPolicy.PUBLIC
    return URLPolicy.AUTO


def make_backend(settings: Settings) -> S3StorageBackend:
    """Build a Backblaze-backed S3 storage backend from settings.

    Credentials are passed explicitly (rather than relying on ambient env) so
    the same process can, in principle, target more than one bucket.
    """
    return S3StorageBackend.for_backblaze(
        settings.b2_bucket,
        region=settings.b2_region,
        key_id=settings.b2_key_id,
        app_key=settings.b2_app_key,
        public_url_base=_public_base(settings),
    )


def make_sink(
    settings: Settings,
    *,
    prefix: str = DEFAULT_PREFIX,
    public: bool = True,
    backend: S3StorageBackend | None = None,
) -> ObjectStorageSink:
    """Build the media sink used by generation pipelines.

    Assets are stored with a hierarchical key layout and each run's manifest is
    co-located, so provenance travels with every generated file.
    """
    backend = backend or make_backend(settings)
    return ObjectStorageSink(
        backend,
        prefix=prefix,
        key_strategy=KeyStrategy.HIERARCHICAL,
        asset_url_policy=_resolve_policy(backend, public),
    )


def brand_kit_key(brand: BrandKit) -> str:
    """Deterministic, versioned key for a Brand Kit revision."""
    return f"{BRANDKIT_PREFIX}/{brand.id}/v{brand.version}.json"


def save_brand_kit(
    settings: Settings,
    brand: BrandKit,
    *,
    backend: S3StorageBackend | None = None,
    public: bool = True,
) -> str:
    """Persist a Brand Kit revision to B2 and return its delivery URL.

    Versioning is explicit in the key so a campaign's assets can always be
    traced back to the exact brand revision that produced them.
    """
    backend = backend or make_backend(settings)
    key = brand_kit_key(brand)
    payload = brand.model_dump_json(indent=2).encode("utf-8")
    backend.put(key, payload, content_type="application/json")
    return backend.get_url(key, policy=_resolve_policy(backend, public))


def load_brand_kit(
    settings: Settings,
    brand_id: str,
    version: int,
    *,
    backend: S3StorageBackend | None = None,
) -> BrandKit:
    """Load a specific Brand Kit revision from B2."""
    backend = backend or make_backend(settings)
    key = f"{BRANDKIT_PREFIX}/{brand_id}/v{version}.json"
    raw = backend.get(key)
    return BrandKit.model_validate_json(raw)


def public_url(
    settings: Settings,
    key: str,
    *,
    backend: S3StorageBackend | None = None,
) -> str:
    """Return a delivery URL for a stored object (public when available)."""
    backend = backend or make_backend(settings)
    return backend.get_url(key, policy=_resolve_policy(backend, public=True))
