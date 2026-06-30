# BrandForge

> Brand-consistent SNS media generation pipeline — built on **Genblaze** and **Backblaze B2** for the Backblaze Generative Media Hackathon.

Define a **Brand Kit** once (palette, tone, style prompt fragments, target platforms). For each campaign theme, BrandForge generates a coordinated set of on-brand images plus one short video, tracks every asset with a **SHA-256 provenance manifest**, versions them in **Backblaze B2**, indexes them for search, and serves public delivery URLs — with per-platform captions.

## Why Genblaze + B2

- **Genblaze**: one fluent `Pipeline` orchestrates multi-step generation (image → short video), swaps providers with a one-line change (GMI Cloud primary, OpenAI fallback), and emits a verifiable provenance manifest per run.
- **Backblaze B2**: durable, versioned home for every generated asset, its manifest, and a Parquet asset index — addressed with a hierarchical key strategy and exposed via public delivery URLs.

## Status

🚧 Early scaffold (Phase 0). See the implementation plan for phases and scope.

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
