"""Generate per-scene narration audio with edge-tts (free, local, neural voices).

Reads scenes.yaml, synthesizes one audio file per scene into video/narration/,
then probes each clip and reports its length against the shot budget so you can
see at a glance whether any copy needs tightening.

Usage:
    python video/scripts/tts_narrate.py            # generate all
    python video/scripts/tts_narrate.py 05-replay  # regenerate one scene

Swap engine later by editing synth(): the OpenAI-TTS path is a drop-in (openai
SDK is already installed) — this file is the only thing that changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

from _common import (
    NARRATION_DIR,
    PipelineError,
    load_manifest,
    probe_duration,
    run,
)


def synth(text: str, voice: str, out_path: Path) -> None:
    """Synthesize `text` to `out_path` via edge-tts. Raises PipelineError on failure."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = run([
        sys.executable, "-m", "edge_tts",
        "--voice", voice, "--text", text, "--write-media", str(out_path),
    ])
    if result.returncode != 0 or not out_path.exists():
        raise PipelineError(f"edge-tts failed for {out_path.name}: {(result.stderr or '').strip()}")


def main(argv: list[str]) -> int:
    try:
        spec, scenes = load_manifest()
    except (ValueError, FileNotFoundError) as exc:
        print(f"MANIFEST ERROR: {exc}", file=sys.stderr)
        return 1

    only = set(argv) if argv else None
    if only is not None:
        unknown = only - {s.id for s in scenes}
        if unknown:
            print(f"unknown scene id(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 1

    NARRATION_DIR.mkdir(parents=True, exist_ok=True)
    print(f"voice={spec.voice}  -> {NARRATION_DIR}\n")
    print(f"{'#':<20} {'audio':>7} {'budget':>7}  fit")
    print("-" * 46)
    over = 0
    for s in scenes:
        if only and s.id not in only:
            continue
        try:
            synth(s.narration, spec.voice, s.narration_path)
            dur = probe_duration(s.narration_path)
        except PipelineError as exc:
            print(f"{s.id:<20} ERROR: {exc}", file=sys.stderr)
            return 1
        # audio longer than the shot means the visual would be cut short
        fit = "ok" if dur <= s.duration else "OVER"
        if dur > s.duration:
            over += 1
        print(f"{s.id:<20} {dur:>6.1f}s {s.duration:>6.0f}s  {fit}")

    print("-" * 46)
    if over:
        print(f"\n{over} scene(s) OVER budget — tighten narration in scenes.yaml or add --rate.",
              file=sys.stderr)
        return 1
    print("\nnarration OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
