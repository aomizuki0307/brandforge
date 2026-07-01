# BrandForge — Demo Video Script & Storyboard

**Target: under 3:00, publicly accessible (YouTube).** This is the recording plan for the
hackathon demo. The demo is **replay-centric and non-billable** — it browses assets that already
exist in B2. Generating a fresh set live is optional (one short take, see §Optional live generation).

> ⚠️ **On-screen secret safety.** Never show: the Basic-auth password, `.env` / API keys, or full
> **presigned URLs** (the signed `?X-Amz-…` query string grants read access). Blur/crop the address
> bar when a presigned image URL could appear. Log in before recording so the password prompt isn't
> on camera, or type it off-frame.

---

## Before you record (checklist)

1. **Wake the app** (Render free tier cold-starts ~30–60s):
   `curl -s https://brandforge-ohce.onrender.com/healthz` → expect `{"status":"ok",...}`.
2. Log into https://brandforge-ohce.onrender.com in the browser (user `curator`, password from
   `docs/pass.txt`) **before** hitting record, so the credential dialog isn't captured.
3. Confirm the gallery shows the existing image set(s). Note a `campaign_id` to use for the replay
   shot (visible on each card / usable as `?campaign_id=<id>`).
4. Have a second tab on the **Backblaze B2 browser** (bucket `brandforge-media`) at the
   `runs/<date>/<run>/` prefix, and one on `docs/architecture.md` (or its rendered diagram).
5. Recorder ready (see §Recording). Do a 10s test clip to check mic + resolution (1080p, 30fps).

---

## Shot-by-shot storyboard (≈2:45)

| # | Time | Screen / action | On-camera focus | Narration (EN) |
|---|------|-----------------|-----------------|----------------|
| 1 | 0:00–0:18 | Title card → gallery hero | Product name + one-line value | "Small brands need a *consistent* look across every post. BrandForge turns one Brand Kit into a coordinated, provenance-tracked image set — built on Genblaze and Backblaze B2." |
| 2 | 0:18–0:40 | `docs/architecture.md` diagram | Trace: Brand Kit → Pipeline → B2 → Parquet → gallery | "Define a Brand Kit once. One Genblaze Pipeline generates every variant under a single SHA-256 manifest, and B2 stores the assets, the manifest, versioned Brand Kits, and a Parquet catalog." |
| 3 | 0:40–0:55 | Browser already on the live gallery | Point out it's the live Render URL; auth note | "This is live on Render. Every route but the health check is behind HTTP Basic auth that fails *closed* — anonymous callers never see a presigned URL." |
| 4 | 0:55–1:25 | Scroll the gallery — one campaign's set | Same look across cards; SHA-256 badge; modality/date | "Here's one campaign: a set of on-brand images that share the palette and style from the Brand Kit. Each card carries its content hash — provenance, not just pixels." |
| 5 | 1:25–1:55 | Change URL to `?campaign_id=<id>` (replay) | Reload → same set returns with fresh URLs | "Replay: I ask for a past campaign by id, and BrandForge re-queries the Parquet catalog and re-signs fresh delivery URLs from B2. The set is reproducible on demand." |
| 6 | 1:55–2:20 | B2 tab: `runs/<date>/<run>/` | `assets/`, `manifest.json`, and `index/assets.parquet` | "On the B2 side: assets under a hierarchical key strategy, a manifest beside them, versioned Brand Kits, and one Parquet index. `manifest.verify()` re-hashes every asset — tamper-evident." |
| 7 | 2:20–2:38 | Back to gallery / a slide | Model disclosure + prod-readiness bullets | "It runs on OpenAI's gpt-image-1 today; the GMI Cloud path and short-video chain are wired for when credits land. Rate-limited, security-headered, 83 tests, ~97% coverage." |
| 8 | 2:38–2:50 | Title/outro card | Repo + live URL | "Brand-consistent media, with provenance, on Genblaze and B2. Repo and live demo are in the description. Thanks for watching." |

Keep it ≤ 2:50 to stay safely under the 3:00 cap after any intro/outro padding.

---

## Full narration (read-through, ~150 words ≈ 2:30 spoken)

> Small brands need a consistent look across every post — but generating a *coordinated* set is
> tedious. BrandForge turns one Brand Kit into a provenance-tracked image set, built on Genblaze
> and Backblaze B2.
>
> Define the Brand Kit once. A single Genblaze Pipeline generates every variant under one SHA-256
> manifest, and B2 stores the assets, that manifest, versioned Brand Kits, and a Parquet catalog.
>
> This is live on Render, behind auth that fails closed. Here's one campaign — a set that shares
> the same palette and style, each image carrying its content hash.
>
> Replay: I request a past campaign by id; BrandForge re-queries the catalog and re-signs fresh B2
> URLs. Reproducible on demand.
>
> On B2: hierarchical keys, a manifest beside the assets, and a Parquet index — `verify()`
> re-hashes everything. It runs on OpenAI's gpt-image-1 today, with the GMI Cloud and short-video
> paths wired for later. Rate-limited, tested, production-shaped. Thanks for watching.

**Japanese subtitle cues** (optional, one line per shot): 1 一貫したブランド画像を一括生成 /
2 Brand Kit→Pipeline→B2→索引→ギャラリー / 3 本番稼働・認証はfail-closed / 4 同一トーンの画像セット＋SHA-256 /
5 replayで過去セットを再署名URLで再現 / 6 B2に資産・manifest・Parquet索引 / 7 使用モデル開示＋本番品質 / 8 リポジトリ・URLは概要欄.

---

## Optional live generation (only if you want to show it — billable)

Showing a fresh generation is stronger but calls `gpt-image-1` (**real cost**) and waits on a cold
start. If you do it, keep it to one short take between shots 4 and 5:

- Use the gallery's generate form (or `POST /campaigns`) with a small `num_variants` (e.g. 2).
- **Confirm the spend is acceptable first**, keep API keys off-screen, and don't show presigned URLs.
- Expect several seconds per image; you can cut the wait in editing.

---

## Recording (Windows)

- **Quick**: Xbox Game Bar — `Win + G` → Capture → record the browser window. Output in
  `Videos\Captures`.
- **Better**: [OBS Studio] — Display/Window Capture, 1920×1080 @ 30fps, mic on a separate track so
  you can re-do narration. Do a 10s mic/level test.
- Watch the clock: keep the raw take ≤ 3:00; trim dead air (cold-start waits) in editing.

## Upload & submit

1. Upload to YouTube. **Public** or **Unlisted** both satisfy "publicly accessible" — Unlisted is
   fine and keeps it low-profile. Do **not** use Private.
2. Title e.g. `BrandForge — Genblaze + Backblaze B2 (Hackathon Demo)`. Put the **live URL** and
   **repo URL** in the description.
3. Paste the YouTube link into:
   - `README.md` → Submission checklist → Demo video line.
   - The Devpost submission form.
4. Share the **test-account password** only in Devpost's private/judges-only field — never in the
   video, README, or repo.
