"""Generate SRT subtitle tracks from the scene manifest.

Produces two sidecar files timed to the assembled (xfade-chained) timeline:
  subtitles/en.srt  — the English narration, one cue per scene
  subtitles/ja.srt  — the Japanese one-line cues

These are both burned by assemble.py (libass) AND uploadable to YouTube as
separate caption tracks. Timing mirrors _common.timeline() so cues line up with
the video assemble.py builds.

Usage:
    python video/scripts/make_subtitles.py
"""

from __future__ import annotations

import sys
import textwrap

from _common import VIDEO_DIR, load_manifest, srt_timestamp, timeline

SUBS_DIR = VIDEO_DIR / "subtitles"


def _wrap(text: str, width: int = 42) -> str:
    """Soft-wrap a cue at `width` chars. Multi-line is fine for sidecar SRT
    (YouTube re-wraps); short JA cues stay on one line."""
    return "\n".join(textwrap.wrap(text, width=width)) or text


def build_srt(spans: list[tuple[float, float]], texts: list[str]) -> str:
    blocks: list[str] = []
    idx = 1
    for (start, end), text in zip(spans, texts, strict=True):
        if not text.strip():
            continue
        blocks.append(
            f"{idx}\n{srt_timestamp(start)} --> {srt_timestamp(end)}\n{_wrap(text)}\n"
        )
        idx += 1
    return "\n".join(blocks)


def main() -> int:
    try:
        spec, scenes = load_manifest()
    except (ValueError, FileNotFoundError) as exc:
        print(f"MANIFEST ERROR: {exc}", file=sys.stderr)
        return 1
    spans, total = timeline(scenes)
    SUBS_DIR.mkdir(parents=True, exist_ok=True)

    tracks = {
        "en": [s.narration for s in scenes],
        "ja": [s.subtitle_ja for s in scenes],
    }
    for lang in spec.subtitles:
        if lang not in tracks:
            print(f"skip unknown subtitle lang: {lang}", file=sys.stderr)
            continue
        out = SUBS_DIR / f"{lang}.srt"
        out.write_text(build_srt(spans, tracks[lang]), encoding="utf-8")
        print(f"wrote {out.relative_to(VIDEO_DIR)}  ({len([t for t in tracks[lang] if t.strip()])} cues)")

    print(f"timeline total: {total:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
