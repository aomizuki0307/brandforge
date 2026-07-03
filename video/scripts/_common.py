"""Shared helpers for the BrandForge demo-video pipeline.

Kept dependency-light on purpose: only PyYAML plus the stdlib. FFmpeg/ffprobe are
resolved at runtime (never hard-coded) so the same scripts run on any box that has
ffmpeg on PATH or at C:\\ffmpeg\\bin.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

# Windows consoles default to cp932/cp1252 and choke on em-dashes and Japanese.
# Force UTF-8 for anything these scripts print. File writes always pass encoding="utf-8".
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

# video/scripts/_common.py -> video/
VIDEO_DIR = Path(__file__).resolve().parent.parent
SCENES_PATH = VIDEO_DIR / "scenes.yaml"
NARRATION_DIR = VIDEO_DIR / "narration"
AUDIO_EXT = ".mp3"  # edge-tts native container; single source of truth for the pipeline

HARD_CAP_SEC = 180  # 3:00 submission hard cap

VISUAL_KINDS = ("card", "diagram", "screen")


class PipelineError(RuntimeError):
    """Recoverable pipeline failure — caught by each script's main() and reported."""


@dataclass(frozen=True)
class Scene:
    """One shot in the storyboard (immutable)."""

    id: str
    duration: float
    visual: Literal["card", "diagram", "screen"]
    source: str  # path relative to VIDEO_DIR
    narration: str
    subtitle_ja: str
    action: str
    safety: tuple[str, ...]
    trim_start: float = 0.0  # seconds to seek into a `screen` clip before the shot window
    speed: float = 1.0  # video playback multiplier (>1 compresses idle/navigation; audio stays 1x)
    zoom: float = 1.0   # gentle Ken-Burns zoom-in factor (1.0 = off, e.g. 1.15 = zoom to 115%)

    @property
    def source_path(self) -> Path:
        return VIDEO_DIR / self.source

    @property
    def narration_path(self) -> Path:
        return NARRATION_DIR / f"{self.id}{AUDIO_EXT}"

    @property
    def blur_top_bar(self) -> bool:
        # exact membership, not substring — a free-text note must not trip this
        return "blur_top_bar" in self.safety


@dataclass(frozen=True)
class VideoSpec:
    target_duration_sec: float
    width: int
    height: int
    fps: int
    voice: str
    xfade_sec: float
    subtitles: tuple[str, ...]


def _clean(text: str) -> str:
    return " ".join(text.split())


def _require_positive(value: float, label: str) -> None:
    if value <= 0:
        raise ValueError(f"{label} must be > 0 (got {value})")


def load_manifest(path: Path = SCENES_PATH) -> tuple[VideoSpec, list[Scene]]:
    """Load and validate scenes.yaml. Raises ValueError/FileNotFoundError on bad input.

    This is the pipeline's guardrail for the hand-authored manifest, so it defends
    against non-mapping nodes and out-of-range numbers rather than trusting them.
    """
    if not path.exists():
        raise FileNotFoundError(f"scenes manifest not found: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "video" not in data or "scenes" not in data:
        raise ValueError("scenes.yaml must have top-level 'video' and 'scenes' keys")

    v = data["video"]
    if not isinstance(v, dict):
        raise ValueError("'video' must be a mapping")
    res = str(v.get("resolution", "1920x1080")).lower().split("x")
    if len(res) != 2 or not all(p.strip().isdigit() for p in res):
        raise ValueError(f"bad resolution: {v.get('resolution')!r}")
    spec = VideoSpec(
        target_duration_sec=float(v.get("target_duration_sec", 170)),
        width=int(res[0]),
        height=int(res[1]),
        fps=int(v.get("fps", 30)),
        voice=str(v.get("voice", "en-US-AndrewNeural")),
        xfade_sec=float(v.get("xfade_sec", 0.0)),
        subtitles=tuple(v.get("subtitles", ["en"])),
    )
    _require_positive(spec.width, "video.width")
    _require_positive(spec.height, "video.height")
    _require_positive(spec.fps, "video.fps")
    if spec.xfade_sec < 0:
        raise ValueError(f"video.xfade_sec must be >= 0 (got {spec.xfade_sec})")

    scenes_raw = data["scenes"]
    if not isinstance(scenes_raw, list) or not scenes_raw:
        raise ValueError("'scenes' must be a non-empty list")

    scenes: list[Scene] = []
    seen: set[str] = set()
    for i, raw in enumerate(scenes_raw):
        if not isinstance(raw, dict):
            raise ValueError(f"scene #{i} must be a mapping")
        for req in ("id", "duration", "visual", "source", "narration"):
            if req not in raw:
                raise ValueError(f"scene #{i} missing required field {req!r}")
        sid = str(raw["id"])
        if sid in seen:
            raise ValueError(f"duplicate scene id: {sid!r}")
        seen.add(sid)
        if raw["visual"] not in VISUAL_KINDS:
            raise ValueError(f"scene {sid}: visual must be one of {VISUAL_KINDS}")
        duration = float(raw["duration"])
        _require_positive(duration, f"scene {sid}: duration")
        trim_start = float(raw.get("trim_start", 0.0) or 0.0)
        if trim_start < 0:
            raise ValueError(f"scene {sid}: trim_start must be >= 0 (got {trim_start})")
        # None-sentinel (not `or`): an explicit `speed: 0` / `zoom: 0` must reach validation
        # and raise, rather than being silently coerced back to the 1.0 default.
        raw_speed = raw.get("speed", 1.0)
        speed = 1.0 if raw_speed is None else float(raw_speed)
        _require_positive(speed, f"scene {sid}: speed")
        raw_zoom = raw.get("zoom", 1.0)
        zoom = 1.0 if raw_zoom is None else float(raw_zoom)
        if zoom < 1.0:
            raise ValueError(f"scene {sid}: zoom must be >= 1.0 (1.0 = off, got {zoom})")
        scenes.append(
            Scene(
                id=sid,
                duration=duration,
                visual=raw["visual"],
                source=str(raw["source"]),
                narration=_clean(str(raw["narration"])),
                subtitle_ja=_clean(str(raw.get("subtitle_ja", ""))),
                action=_clean(str(raw.get("action", ""))),
                safety=tuple(raw.get("safety", []) or []),
                trim_start=trim_start,
                speed=speed,
                zoom=zoom,
            )
        )
    return spec, scenes


def timeline(scenes: list[Scene]) -> tuple[list[tuple[float, float]], float]:
    """Absolute (start, end) of each scene on the assembled timeline, plus total.

    assemble.py concatenates independently-rendered clips back-to-back (no crossfade
    overlap), so scene i starts at the cumulative sum of prior durations and the total
    is sum(durations). This mirrors assemble.py exactly — keep the two in lockstep.
    """
    spans: list[tuple[float, float]] = []
    cursor = 0.0
    for s in scenes:
        spans.append((cursor, cursor + s.duration))
        cursor += s.duration
    return spans, cursor


def srt_timestamp(seconds: float) -> str:
    """Seconds -> 'HH:MM:SS,mmm' (SRT format)."""
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """subprocess.run wrapper that always decodes as UTF-8 (ffmpeg/ffprobe/edge-tts
    emit UTF-8) so output doesn't depend on the ambient Windows console codepage."""
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs
    )


def _resolve_exe(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    fallback = Path(rf"C:\ffmpeg\bin\{name}.exe")
    if fallback.exists():
        return str(fallback)
    raise PipelineError(f"{name} not found on PATH or at C:\\ffmpeg\\bin")


def resolve_ffmpeg() -> str:
    return _resolve_exe("ffmpeg")


def resolve_ffprobe() -> str:
    return _resolve_exe("ffprobe")


def probe_duration(path: Path) -> float:
    """Media duration in seconds via ffprobe. Raises PipelineError on failure."""
    result = run([
        resolve_ffprobe(), "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(path),
    ])
    if result.returncode != 0:
        raise PipelineError(f"ffprobe failed for {path.name}: {result.stderr.strip()}")
    return float(json.loads(result.stdout)["format"]["duration"])
