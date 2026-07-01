r"""Phase 2 smoke test: one Brand Kit -> N on-brand image variants -> B2.

Exercises the ``run_campaign`` driver end-to-end: a Brand Kit revision is
saved, ``num_variants`` images are generated as a single campaign, and the
whole set is stored under **one manifest**. Like the Phase 1 smoke, it needs
live credentials in ``.env`` (B2_KEY_ID / B2_APP_KEY / B2_BUCKET / B2_REGION and
OPENAI_API_KEY) and makes real gpt-image-1 calls (3 images = billable), so it
is NOT part of the pytest suite.

The printed asset/Brand-Kit URLs are short-lived (~1h) SigV4 *presigned* GET
URLs that embed a signature granting read access to that object — fine for a
local run, but do NOT record this output in the demo video or paste it into a
public issue/PR, and do not redirect it into a file that later gets committed.

Run from the repo root:

    .\.venv\Scripts\python examples\smoke_variant_set.py

Success criteria:
  * a Brand Kit revision is written to B2 and returns a URL
  * exactly ``num_variants`` images are generated and stored (hierarchical keys)
  * all variants share ONE manifest key (one manifest per campaign)
  * every asset carries a sha256
  * ``result.manifest.verify().ok`` is True (provenance intact)
  * the assets are queryable from the Parquet catalog (``index/assets.parquet``)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the repo root importable so `app...` resolves when run as a script.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.campaign import run_campaign
from app.config import load_settings
from app.index import query_assets
from app.models import BrandKit, Campaign

NUM_VARIANTS = 3


def main() -> int:
    settings = load_settings()

    brand = BrandKit(
        id="smoke-set",
        name="BrandForge Smoke Set",
        palette=["#0f172a", "#38bdf8", "#f8fafc"],
        tone_words=["clean", "modern", "confident"],
        style_prompt="flat vector illustration, soft gradients, generous whitespace",
        audience="indie software makers",
    )
    campaign = Campaign(
        id="smoke-set-001",
        brand_kit_id=brand.id,
        theme="launch announcement for a focus-timer productivity app",
        num_variants=NUM_VARIANTS,
    )

    print(f"[1/4] Running campaign {campaign.id} ({NUM_VARIANTS} variants) ...")
    result = run_campaign(settings, brand, campaign)
    print(f"       Brand Kit v{brand.version} stored: {result.brand_kit_url}")

    print(f"[2/4] Generated {len(result.assets)} assets:")
    for asset in result.assets:
        print(f"       asset {asset.id}")
        print(f"         url    : {asset.url}")
        print(f"         sha256 : {asset.sha256}")
        print(f"         b2_key : {asset.b2_key}")

    verification = result.manifest.verify()
    # verify() may return a bool or a ManifestVerification with an `.ok` flag.
    verified = verification.ok if hasattr(verification, "ok") else bool(verification)
    manifest_keys = {a.manifest_b2_key for a in result.assets}
    print(f"[3/4] manifest verified = {verified}")
    print(f"       manifest_uri = {result.manifest.manifest_uri}")
    print(f"       shared manifest keys = {manifest_keys}")

    # run_campaign auto-updates the Parquet catalog; read it back as the gallery would.
    # (Re-running accumulates new variants under this campaign — de-dup is by asset
    # id, so we check this run's ids are present rather than an exact total.)
    indexed = query_assets(settings, brand_kit_id=brand.id, campaign_id=campaign.id)
    indexed_ids = {a.id for a in indexed}
    this_run_ids = {a.id for a in result.assets}
    print(f"[4/4] index/assets.parquet -> {len(indexed)} asset(s) for this campaign")
    if indexed:
        top = indexed[0]
        print(f"       newest: {top.id} ({top.modality}) fresh url ok = {bool(top.url)}")

    if len(result.assets) != NUM_VARIANTS:
        print(
            f"SMOKE FAILED: expected {NUM_VARIANTS} assets, got {len(result.assets)}",
            file=sys.stderr,
        )
        return 1
    if len(manifest_keys) != 1:
        print(f"SMOKE FAILED: assets span {len(manifest_keys)} manifests, expected 1", file=sys.stderr)
        return 1
    if any(a.sha256 is None for a in result.assets):
        print("SMOKE FAILED: an asset is missing its sha256", file=sys.stderr)
        return 1
    if not verified:
        print("SMOKE FAILED: manifest did not verify", file=sys.stderr)
        return 1
    if not this_run_ids <= indexed_ids:
        missing = this_run_ids - indexed_ids
        print(f"SMOKE FAILED: this run's assets missing from index: {missing}", file=sys.stderr)
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
