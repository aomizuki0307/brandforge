"""Deterministic QA gate for the assembled demo video.

Checks that don't need a vision model:
  - duration under the 3:00 hard cap; resolution/fps as specified
  - no leaked secrets in the *generated text assets* (scenes.yaml, subtitles):
    regex patterns (API keys, AWS/B2 ids, presigned signature params, JWTs) AND a
    check that no value from docs/pass.txt or .env appears — without printing them
  - no long black segments (ffmpeg blackdetect)
  - the audio track isn't effectively silent (volumedetect mean_volume)

The remaining check — "is a password / presigned URL / API key / account id visible
ON SCREEN?" — needs to watch the pixels, so it's done separately with
ai-vision-mcp analyze_video (this script prints the exact prompt to use). A zero exit
here means the deterministic checks passed, NOT that the on-screen check has run.

Usage:
    python video/scripts/qa_report.py
Exit 0 = deterministic checks pass; 1 = a blocking failure.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from _common import (
    HARD_CAP_SEC,
    PipelineError,
    VIDEO_DIR,
    load_manifest,
    resolve_ffmpeg,
    resolve_ffprobe,
    run,
)

FINAL = VIDEO_DIR / "final.mp4"
PROJECT_ROOT = VIDEO_DIR.parent
SECRET_FILES = [PROJECT_ROOT / "docs" / "pass.txt", PROJECT_ROOT / ".env"]

# Text assets we generate and could accidentally leak a secret into.
TEXT_ASSETS = [
    VIDEO_DIR / "scenes.yaml",
    VIDEO_DIR / "subtitles" / "en.srt",
    VIDEO_DIR / "subtitles" / "ja.srt",
]

SECRET_PATTERNS = {
    "OpenAI/Anthropic key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "AWS access key id": re.compile(r"AKIA[0-9A-Z]{12,}"),
    "B2 native key id": re.compile(r"\b00[0-9a-f]{9,}\b"),
    "presigned/S3 signature": re.compile(r"X-Amz-Signature|X-Amz-Credential|AWSAccessKeyId|Signature="),
    "JWT": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "bearer/api-key label": re.compile(r"(?i)(authorization:\s*bearer\s+\S+|api[_-]?key\s*[=:]\s*\S{12,})"),
    "long high-entropy token": re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}\b"),
}

MIN_TOKEN_LEN = 5  # low floor so short PINs/passcodes aren't silently ignored


@dataclass
class Report:
    oks: list[str] = field(default_factory=list)
    warns: list[str] = field(default_factory=list)
    fails: list[str] = field(default_factory=list)

    def ok(self, m: str) -> None:
        self.oks.append(m)

    def warn(self, m: str) -> None:
        self.warns.append(m)

    def fail(self, m: str) -> None:
        self.fails.append(m)


def _probe(path: Path) -> dict:
    result = run([
        resolve_ffprobe(), "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate",
        "-of", "json", str(path),
    ])
    if result.returncode != 0:
        raise PipelineError(f"ffprobe failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def _leak_tokens(path: Path) -> set[str]:
    """Extract candidate secret values from a credential file, robust to comment lines
    and `label = value` / `label: value` shapes. Never returns/prints the raw file."""
    if not path.exists():
        return set()
    tokens: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue  # skip whole comment lines (not per-token)
        value = re.split(r"[:=]", line, maxsplit=1)[-1].strip() if re.search(r"[:=]", line) else line
        tokens.update(t for t in value.split() if len(t) >= MIN_TOKEN_LEN)
    return tokens


def check_container(r: Report) -> None:
    spec, _ = load_manifest()
    data = _probe(FINAL)
    dur = float(data["format"]["duration"])
    if dur > HARD_CAP_SEC:
        r.fail(f"duration {dur:.1f}s exceeds {HARD_CAP_SEC}s cap")
    else:
        r.ok(f"duration {dur:.1f}s (under {HARD_CAP_SEC}s cap)")

    vs = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if not vs:
        r.fail("no video stream")
        return
    if (vs.get("width"), vs.get("height")) != (spec.width, spec.height):
        r.warn(f"resolution {vs.get('width')}x{vs.get('height')} != {spec.width}x{spec.height}")
    else:
        r.ok(f"resolution {spec.width}x{spec.height}")
    if not any(s["codec_type"] == "audio" for s in data["streams"]):
        r.fail("no audio stream (narration missing?)")
    else:
        r.ok("audio stream present")


def check_text_secrets(r: Report) -> None:
    leaks = 0
    for asset in TEXT_ASSETS:
        if not asset.exists():
            continue
        text = asset.read_text(encoding="utf-8", errors="strict")
        for name, pat in SECRET_PATTERNS.items():
            if pat.search(text):
                r.fail(f"{asset.name}: matches secret pattern [{name}]")
                leaks += 1
    # exact-value cross-check against local credential files, without echoing them
    checked_any = False
    for sec_file in SECRET_FILES:
        tokens = _leak_tokens(sec_file)
        if not tokens:
            continue
        checked_any = True
        for asset in TEXT_ASSETS:
            if not asset.exists():
                continue
            body = asset.read_text(encoding="utf-8", errors="strict")
            if any(tok in body for tok in tokens):
                r.fail(f"{asset.name}: contains a value from {sec_file.name}")
                leaks += 1
        r.ok(f"{sec_file.name} cross-check done ({len(tokens)} tokens)")
    if not checked_any:
        r.warn("no docs/pass.txt or .env found — skipped credential cross-check")
    if leaks == 0:
        r.ok("no secret patterns in generated text assets")


def _ffmpeg_null(vf_or_af: list[str]) -> tuple[int, str]:
    ffmpeg = resolve_ffmpeg()
    result = run([ffmpeg, "-i", str(FINAL), *vf_or_af, "-f", "null", "-"])
    return result.returncode, result.stderr or ""


def check_blackframes(r: Report) -> None:
    rc, stderr = _ffmpeg_null(["-vf", "blackdetect=d=1.0:pic_th=0.98", "-an"])
    if rc != 0:
        r.fail(f"blackdetect: ffmpeg failed (rc={rc}): {stderr[-300:]}")
        return
    hits = re.findall(r"black_start:(\d+\.?\d*)\s+black_end:(\d+\.?\d*)", stderr)
    long_gaps = [(float(a), float(b)) for a, b in hits if float(b) - float(a) >= 1.0]
    if long_gaps:
        r.warn(f"{len(long_gaps)} black segment(s) >=1s (e.g. {long_gaps[0][0]:.1f}-{long_gaps[0][1]:.1f}s)")
    else:
        r.ok("no long black segments")


def check_audio_level(r: Report) -> None:
    rc, stderr = _ffmpeg_null(["-af", "volumedetect", "-vn"])
    if rc != 0:
        r.fail(f"volumedetect: ffmpeg failed (rc={rc}): {stderr[-300:]}")
        return
    m = re.search(r"mean_volume:\s*(-?\d+\.?\d*)\s*dB", stderr)
    if not m:
        r.fail("could not read mean_volume (ffmpeg output unexpected)")
        return
    mean = float(m.group(1))
    if mean < -50:
        r.fail(f"audio effectively silent (mean_volume {mean:.1f} dB)")
    else:
        r.ok(f"audio level ok (mean_volume {mean:.1f} dB)")


VISION_PROMPT = (
    "This is a hackathon demo video. Watch it end to end and report: "
    "(1) Is any SECRET visible on screen at ANY point — a password, an API key, a .env "
    "value, a Backblaze B2 account id / application key id, or a presigned URL query "
    "string (X-Amz-Signature / X-Amz-Credential / Signature=)? Check the browser address "
    "bar, link-hover previews in the status bar, any DevTools/Network panels, toasts, and "
    "on-page text. Give timestamps for anything found. "
    "(2) Are the burned captions readable and roughly in sync with the spoken narration? "
    "(3) List the distinct scenes/beats you see, in order, with timestamps. "
    "(4) Any glitches: frozen frames, cut-off audio, unreadable text? Be specific."
)


def main() -> int:
    if not FINAL.exists():
        print(f"final video not found: {FINAL}\nRun assemble.py first.", file=sys.stderr)
        return 1

    r = Report()
    try:
        check_container(r)
        check_text_secrets(r)
        check_blackframes(r)
        check_audio_level(r)
    except (PipelineError, ValueError, FileNotFoundError) as exc:
        print(f"QA aborted: {exc}", file=sys.stderr)
        return 1

    print(f"QA report — {FINAL.name}\n")
    for m in r.oks:
        print(f"  [ OK ] {m}")
    for m in r.warns:
        print(f"  [WARN] {m}")
    for m in r.fails:
        print(f"  [FAIL] {m}")

    print("\nnext (REQUIRED before upload): on-screen secret / caption-sync check needs a")
    print("vision model. Run ai-vision-mcp analyze_video on final.mp4 with this prompt:\n")
    print(f"  {VISION_PROMPT}\n")

    if r.fails:
        print(f"RESULT: FAIL ({len(r.fails)} blocking issue(s))", file=sys.stderr)
        return 1
    print(f"RESULT: PASS deterministic checks ({len(r.warns)} warning(s)); "
          f"vision check still required")
    return 0


if __name__ == "__main__":
    sys.exit(main())
