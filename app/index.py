"""Parquet asset index — the catalog behind the gallery / search layer.

Every generated asset is recorded as one row in a single Parquet object in B2
(``index/assets.parquet``), so the whole body of work is queryable as data
rather than by crawling object keys. This is the "data orchestration" half of
the B2 story: generate -> store (with provenance manifest) -> **index** ->
reuse / replay.

Design (hackathon scale — one user, sequential campaigns):

* One index object, rewritten in full on each update (read -> merge -> write).
  De-dup is by ``Asset.id`` so re-indexing the same assets is idempotent and a
  later revision of a row wins. Concurrency is out of scope; if this ever needs
  to be concurrent, shard per run under ``index/runs/<run>.parquet`` and
  aggregate on read.
* Rows are the ``Asset`` schema verbatim (``model_dump``). The stored ``url`` is
  a snapshot from generation time — presigned URLs expire — so ``query_assets``
  re-resolves a fresh delivery URL from the durable ``b2_key`` before returning.
"""

from __future__ import annotations

import io

import pyarrow as pa
import pyarrow.parquet as pq

from app.config import Settings
from app.models import Asset
from app.storage import make_backend, public_url
from genblaze_s3 import S3StorageBackend

# Single catalog object for every asset across all brands and campaigns.
ASSET_INDEX_KEY = "index/assets.parquet"
PARQUET_CONTENT_TYPE = "application/vnd.apache.parquet"


def _assets_to_parquet(assets: list[Asset]) -> bytes:
    """Serialize assets to Parquet bytes (columns = the ``Asset`` fields)."""
    rows = [a.model_dump() for a in assets]
    table = pa.Table.from_pylist(rows)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _parquet_to_assets(raw: bytes) -> list[Asset]:
    """Parse Parquet bytes back into validated ``Asset`` rows."""
    table = pq.read_table(io.BytesIO(raw))
    return [Asset(**row) for row in table.to_pylist()]


def read_index(
    settings: Settings,
    *,
    backend: S3StorageBackend | None = None,
) -> list[Asset]:
    """Load every indexed asset, or ``[]`` if no index exists yet.

    ``exists`` is checked first so the very first campaign (no index object)
    reads as an empty catalog rather than an error.
    """
    backend = backend or make_backend(settings)
    if not backend.exists(ASSET_INDEX_KEY):
        return []
    return _parquet_to_assets(backend.get(ASSET_INDEX_KEY))


def index_assets(
    settings: Settings,
    assets: list[Asset],
    *,
    backend: S3StorageBackend | None = None,
) -> int:
    """Merge ``assets`` into the catalog (de-duped by id) and return the total.

    Read-modify-write of the single index object: existing rows are loaded,
    the new assets overwrite any row with the same ``Asset.id``, and the union
    is written back. A no-op call (empty ``assets``) still returns the current
    row count without rewriting.
    """
    backend = backend or make_backend(settings)
    if not assets:
        return len(read_index(settings, backend=backend))

    merged: dict[str, Asset] = {a.id: a for a in read_index(settings, backend=backend)}
    merged.update({a.id: a for a in assets})
    ordered = list(merged.values())
    backend.put(
        ASSET_INDEX_KEY,
        _assets_to_parquet(ordered),
        content_type=PARQUET_CONTENT_TYPE,
    )
    return len(ordered)


def query_assets(
    settings: Settings,
    *,
    brand_kit_id: str | None = None,
    campaign_id: str | None = None,
    modality: str | None = None,
    backend: S3StorageBackend | None = None,
    refresh_urls: bool = True,
) -> list[Asset]:
    """Return indexed assets matching the given filters, newest first.

    Filtering is exact-match on the provided fields (any ``None`` filter is
    ignored). With ``refresh_urls`` (default), each asset's ``url`` is
    re-resolved from its durable ``b2_key`` so callers always get a live
    delivery link even though the stored URL may have expired.
    """
    backend = backend or make_backend(settings)
    assets = read_index(settings, backend=backend)

    def _matches(a: Asset) -> bool:
        return (
            (brand_kit_id is None or a.brand_kit_id == brand_kit_id)
            and (campaign_id is None or a.campaign_id == campaign_id)
            and (modality is None or a.modality == modality)
        )

    selected = [a for a in assets if _matches(a)]
    if refresh_urls:
        selected = [
            a.model_copy(update={"url": public_url(settings, a.b2_key, backend=backend)})
            for a in selected
        ]
    return sorted(selected, key=lambda a: a.created_at, reverse=True)
