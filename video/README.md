# BrandForge demo-video pipeline

AI-assisted, divided-labor pipeline that turns [`docs/DEMO_SCRIPT.md`](../docs/DEMO_SCRIPT.md)
into a captioned, narrated `final.mp4` — safe half-automation, so a human keeps control of
the screen capture (presigned URLs / passwords must never appear on camera).

`scenes.yaml` is the single source of truth. Everything else is regenerated from it.

## Prerequisites (already installed on this machine)
- **FFmpeg + ffprobe** on PATH (or `C:\ffmpeg\bin`)
- **Node** (for the Mermaid render + card render via Playwright) — `npx`, `playwright`
- **Python** venv with `edge-tts` — `C:\Users\wandt\AI_coding\.venv`
- Optional: **OBS Studio** for screen capture

Run all Python scripts with the root venv:
`C:\Users\wandt\AI_coding\.venv\Scripts\python.exe`

## Roles → tools
| Step | Script / tool | Output |
|------|---------------|--------|
| Validate manifest | `scripts/build_scenes.py` | timeline + budget report |
| Architecture diagram | `npx @mermaid-js/mermaid-cli` on `assets/architecture.mmd` | `assets/architecture.png` |
| Title / prod / outro / placeholder cards | `scripts/render_cards.mjs` (Playwright) | `cards/*.png` |
| Narration | `scripts/tts_narrate.py` (edge-tts) | `narration/*.mp3` |
| Subtitles | `scripts/make_subtitles.py` | `subtitles/en.srt`, `subtitles/ja.srt` |
| Screen capture | **human OBS** (or Playwright, phase 2) | `raw_shots/<scene-id>.mp4` |
| Assemble | `scripts/assemble.py` (FFmpeg) | `build/*.mp4` → `final.mp4` |
| QA (deterministic) | `scripts/qa_report.py` | pass/fail report |
| QA (on-screen) | `ai-vision-mcp analyze_video` on `final.mp4` | secret / caption / beat check |

## Build (end to end)
```powershell
$py = "C:\Users\wandt\AI_coding\.venv\Scripts\python.exe"
cd C:\Users\wandt\AI_coding\workspace\projects\backblaze-genblaze\video\scripts

& $py build_scenes.py                 # sanity-check the manifest
npx --yes "@mermaid-js/mermaid-cli" -i ..\assets\architecture.mmd -o ..\assets\architecture.png -c ..\assets\mermaid-config.json -b "#0d1117" -w 1600 --scale 2
node render_cards.mjs                  # run from repo root if bare 'playwright' import fails
& $py tts_narrate.py                   # narration/*.mp3
& $py make_subtitles.py                # en.srt + ja.srt
# --- record shots 03–06 into ..\raw_shots\ (see DEMO_SCRIPT.md) ---
& $py assemble.py --burn en            # -> final.mp4  (missing shots use a placeholder)
& $py qa_report.py                     # deterministic gate
```
`assemble.py` renders an end-to-end **draft immediately** — missing `screen` clips fall
back to `cards/placeholder.png` with real narration/timing, so you can review pacing before
recording. Drop the OBS clips into `raw_shots/<scene-id>.mp4` and re-run `assemble.py`.

## Options
- `assemble.py --burn {en|ja|none}` — which caption track to burn (default `en`; sidecar
  SRTs are always written for YouTube CC upload).
- `assemble.py --only <scene-id>` — rebuild one scene clip.
- `tts_narrate.py <scene-id>` — regenerate one narration clip.
- Change the voice / durations / safety flags in `scenes.yaml`.

## Safety (enforced)
- Scenes flagged `blur_top_bar` get the top address-bar strip boxblurred (presigned-URL safety).
- `qa_report.py` scans generated text for secret patterns and cross-checks `docs/pass.txt`
  tokens without printing them.
- Always run the `ai-vision-mcp analyze_video` pass before upload (prompt printed by `qa_report.py`).

## Swappable choices
- **Narration**: edge-tts (default, free). OpenAI TTS is a drop-in in `tts_narrate.py:synth()`.
- **Editor**: FFmpeg (this pipeline). Remotion could replace `assemble.py` for animated cards.
- **Capture**: human OBS (MVP) → Playwright `recordVideo` (phase 2).
