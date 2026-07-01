# BrandForge

> Brand-consistent SNS media generation pipeline — built on **Genblaze** and **Backblaze B2** for the Backblaze Generative Media Hackathon.

Define a **Brand Kit** once (palette, tone, style prompt fragments, target platforms). For each campaign theme, BrandForge generates a coordinated set of on-brand images plus one short video, tracks every asset with a **SHA-256 provenance manifest**, versions them in **Backblaze B2**, indexes them for search, and serves public delivery URLs — with per-platform captions.

## Why Genblaze + B2

- **Genblaze**: one fluent `Pipeline` orchestrates multi-step generation (image → short video), swaps providers with a one-line change (GMI Cloud primary, OpenAI fallback), and emits a verifiable provenance manifest per run.
- **Backblaze B2**: durable, versioned home for every generated asset, its manifest, and a Parquet asset index — addressed with a hierarchical key strategy and exposed via public delivery URLs.

## Status

🚧 In progress. Working: Phase 1 (single image → B2 → verified manifest),
Phase 2 (multi-variant on-brand image **set**, one manifest per campaign), and
Phase 4 (single **Parquet asset catalog** in B2, auto-updated per campaign and
queryable for the gallery). Next up: the FastAPI + web gallery layer and the
short-video chain. See the implementation plan for phases and scope.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1   |   *nix: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in B2 + provider keys
```

### Required accounts / keys

| Service | Purpose | Notes |
|---|---|---|
| Backblaze B2 | Asset storage | Free tier 10GB. Create an Application Key. |
| GMI Cloud | Primary generative provider | Free credits for the first 270 participants (request form). |
| OpenAI | Fallback image provider | Optional; pay-as-you-go. |
| Anthropic | Caption generation | Optional. |

## Generate a campaign

`run_campaign` is the single entry point: it saves the Brand Kit revision, runs
one Genblaze `Pipeline` of `num_variants` on-brand steps, and returns the whole
image **set** under one provenance manifest.

```python
from app.campaign import run_campaign
from app.config import load_settings
from app.models import BrandKit, Campaign

settings = load_settings()
brand = BrandKit(id="acme", name="Acme", tone_words=["clean", "bold"],
                 style_prompt="flat vector, soft gradients")
campaign = Campaign(id="launch-1", brand_kit_id="acme",
                    theme="summer product launch", num_variants=3)

result = run_campaign(settings, brand, campaign)   # OpenAI-first; prefer="gmicloud" once credits land
# result.brand_kit_url, result.assets (all share result.manifest), result.run
```

Each run also folds its assets into a single **Parquet catalog** in B2
(`index/assets.parquet`, de-duped by asset id) — the data source for the gallery.
Query it back with fresh (re-signed) delivery URLs:

```python
from app.index import query_assets

assets = query_assets(settings, brand_kit_id="acme")          # newest first
assets = query_assets(settings, campaign_id="launch-1")        # one campaign
assets = query_assets(settings, modality="image")              # filter by kind
```

Pass `update_index=False` to `run_campaign` to skip the catalog write.

### Smoke tests (live B2 + OpenAI, billable — not in the pytest suite)

```bash
.\.venv\Scripts\python examples\smoke_b2_pipeline.py    # Phase 1: 1 image
.\.venv\Scripts\python examples\smoke_variant_set.py    # Phase 2: 3-variant set, one manifest
```

> The printed URLs are short-lived presigned links that grant read access to the
> object — don't record them in the demo video or paste them anywhere public.

## Test

```bash
pytest --cov=app --cov-report=term-missing
```

## AI providers and models used

_(Disclosure required by the hackathon — kept current as providers are wired in.)_

- **GMI Cloud** — image and short-video models (primary).
- **OpenAI** — image model (fallback).
- **Anthropic** — caption generation.

## Submission checklist

- [ ] Working app URL (deployed; test account if auth)
- [ ] Public GitHub repo with setup instructions
- [ ] Description of B2 + Genblaze usage
- [ ] List of AI providers/models used
- [ ] Demo video (< 3 min, public)
