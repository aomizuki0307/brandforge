"""Validate and summarize the scene manifest.

scenes.yaml is authored by hand (the script->manifest step); this script is the
guardrail: it validates structure, sums the timeline against the 3:00 hard cap,
estimates spoken length vs. the shot budget, and reports which source assets exist.

Usage:
    python video/scripts/build_scenes.py
Exit code 0 = manifest is sane; 1 = a blocking problem (over cap / bad schema).
"""

from __future__ import annotations

import sys

from _common import HARD_CAP_SEC, load_manifest

# ~2.7 words/sec is a comfortable VO pace; used only for a soft over/under warning.
WORDS_PER_SEC = 2.7


def main() -> int:
    try:
        spec, scenes = load_manifest()
    except (ValueError, FileNotFoundError) as exc:
        print(f"MANIFEST ERROR: {exc}", file=sys.stderr)
        return 1

    total = sum(s.duration for s in scenes)
    print(f"BrandForge demo — {len(scenes)} scenes, {spec.width}x{spec.height}@{spec.fps}")
    print(f"voice={spec.voice}  subtitles={','.join(spec.subtitles)}\n")

    header = f"{'#':<20} {'dur':>5} {'words':>6} {'est':>6}  {'visual':<8} src?  safety"
    print(header)
    print("-" * len(header))
    warnings: list[str] = []
    for s in scenes:
        words = len(s.narration.split())
        est = words / WORDS_PER_SEC
        exists = "OK " if s.source_path.exists() else "MISS"
        if s.visual == "screen" and not s.source_path.exists():
            warnings.append(f"{s.id}: screen clip missing ({s.source}) — placeholder will be used")
        # narration materially longer than the shot -> it will be rushed/clipped
        if est > s.duration * 1.15:
            warnings.append(f"{s.id}: narration ~{est:.0f}s > shot {s.duration:.0f}s (tighten copy)")
        flag = "blur" if s.blur_top_bar else ""
        print(f"{s.id:<20} {s.duration:>5.0f} {words:>6} {est:>6.1f}  {s.visual:<8} {exists}  {flag}")

    print("-" * len(header))
    print(f"{'TOTAL':<20} {total:>5.0f}s  (target {spec.target_duration_sec:.0f}s, cap {HARD_CAP_SEC}s)")

    if total > HARD_CAP_SEC:
        print(f"\nBLOCKING: timeline {total:.0f}s exceeds the {HARD_CAP_SEC}s hard cap.", file=sys.stderr)
        return 1

    if warnings:
        print("\nwarnings:")
        for w in warnings:
            print(f"  - {w}")

    print("\nmanifest OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
