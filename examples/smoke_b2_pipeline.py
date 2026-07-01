r"""Phase 1 smoke test: OpenAI image -> Backblaze B2 -> manifest verify.

This is the Task A completion gate. It requires live credentials in ``.env``
(B2_KEY_ID / B2_APP_KEY / B2_BUCKET / B2_REGION and OPENAI_API_KEY) and a
public B2 bucket, so it is NOT part of the pytest suite.

Run from the repo root:

    .\.venv\Scripts\python examples\smoke_b2_pipeline.py

Success criteria:
  * a Brand Kit revision is written to B2 and returns a URL
  * one on-brand image is generated and stored in B2 (hierarchical key)
  * ``result.manifest.verify().ok`` is True (provenance intact)
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the repo root importable so `app...` resolves when run as a script.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config import load_settings
from app.models import BrandKit, Campaign
from app.pipeline import generate_images, pick_image_provider
from app.storage import make_backend, make_sink, save_brand_kit


def main() -> int:
    settings = load_settings()
    backend = make_backend(settings)
    sink = make_sink(settings, backend=backend)

    brand = BrandKit(
        id="smoke",
        name="BrandForge Smoke",
        palette=["#0f172a", "#38bdf8", "#f8fafc"],
        tone_words=["clean", "modern", "confident"],
        style_prompt="flat vector illustration, soft gradients, generous whitespace",
        audience="indie software makers",
    )
    kit_url = save_brand_kit(settings, brand, backend=backend)
    print(f"[1/3] Brand Kit v{brand.version} stored: {kit_url}")

    campaign = Campaign(
        id="smoke-001",
        brand_kit_id=brand.id,
        theme="launch announcement for a focus-timer productivity app",
        num_variants=1,
    )
    choice = pick_image_provider(settings)
    print(f"[2/3] Generating 1 image via {choice.name} ...")

    created = datetime.now(timezone.utc).isoformat()
    result = generate_images(
        settings, brand, campaign, created_at=created, sink=sink, backend=backend, choice=choice
    )
    for asset in result.assets:
        print(f"       asset {asset.id}")
        print(f"         url    : {asset.url}")
        print(f"         sha256 : {asset.sha256}")
        print(f"         b2_key : {asset.b2_key}")

    verification = result.manifest.verify()
    # verify() may return a bool or a ManifestVerification with an `.ok` flag.
    verified = verification.ok if hasattr(verification, "ok") else bool(verification)
    print(f"[3/3] manifest verified = {verified}")
    print(f"       manifest_uri = {result.manifest.manifest_uri}")

    if not verified:
        print("SMOKE FAILED: manifest did not verify", file=sys.stderr)
        return 1
    if not result.assets:
        print("SMOKE FAILED: no assets produced", file=sys.stderr)
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
