"""Assemble the BrandForge demo from the scene manifest.

Two stages:
  1. Build one normalized, self-contained clip per scene into video/build/
     (WxH @ fps from scenes.yaml, narration muxed, optional caption burned,
     fade in/out, address-bar blur where scenes.yaml flags it).
  2. Concatenate the clips into video/final.mp4 (concat demuxer, stream copy).

Missing `screen` clips fall back to cards/placeholder.png so an end-to-end draft
renders before OBS/Playwright capture is done. Narration and timing are real, so
the draft already has the correct length and voiceover.

Usage:
    python video/scripts/assemble.py                 # full build, burn EN captions
    python video/scripts/assemble.py --burn ja        # burn Japanese cues instead
    python video/scripts/assemble.py --burn none       # no burned captions (SRT sidecars only)
    python video/scripts/assemble.py --only 01-title   # rebuild a single scene clip
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Literal

from _common import (
    PipelineError,
    Scene,
    VIDEO_DIR,
    VideoSpec,
    load_manifest,
    probe_duration,
    resolve_ffmpeg,
    run,
    srt_timestamp,
)

BUILD_DIR = VIDEO_DIR / "build"
PLACEHOLDER = VIDEO_DIR / "cards" / "placeholder.png"
FINAL = VIDEO_DIR / "final.mp4"

BG = "0x0d1117"          # matches the diagram + cards
FADE = 0.25             # per-clip fade in/out (s)
BLUR_H = 96             # address-bar strip height to blur (px) for presigned-URL safety
CAPTION_STYLE = (
    "FontName=Segoe UI,FontSize=15,PrimaryColour=&H00FFFFFF&,"
    "OutlineColour=&H00101418&,BackColour=&HB0101418&,BorderStyle=3,"
    "Outline=1,Shadow=0,MarginV=42,Alignment=2"
)

BurnTrack = Literal["en", "ja", "none"]


def _encode_args(spec: VideoSpec) -> list[str]:
    return [
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-r", str(spec.fps), "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
    ]


def _vscale(spec: VideoSpec) -> str:
    w, h = spec.width, spec.height
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={BG},setsar=1,fps={spec.fps},format=yuv420p"
    )


def _fade(duration: float) -> str:
    return f"fade=t=in:st=0:d={FADE},fade=t=out:st={max(0.0, duration - FADE):.2f}:d={FADE}"


def _diagram_pan(spec: VideoSpec, duration: float) -> str:
    """Wide diagram readability: scale to fill the frame height (cover), then slowly
    pan left->right across the horizontal overflow. Fitting a 2.5:1 diagram into 16:9
    letterboxes it and shrinks the text; cover+pan keeps the text ~1.4x larger and still
    reveals every region over the shot."""
    # Pan via a moving overlay on a full-frame canvas: this build's crop filter lacks the
    # per-frame `eval` option, but overlay's does. The scaled diagram (width > frame) slides
    # left so its right edge comes into view by the end of the shot.
    w, h = spec.width, spec.height
    return (
        f"color=c={BG}:s={w}x{h}:r={spec.fps}:d={duration:.3f}[canvas];"
        f"[0:v]scale=-2:{h}:flags=lanczos,setsar=1[big];"
        f"[canvas][big]overlay=x='-(overlay_w-{w})*t/{duration:.3f}':y=0:eval=frame:shortest=1,"
        f"fps={spec.fps},format=yuv420p[bg]"
    )


def _zoom_chain(spec: VideoSpec, duration: float, zoom: float, in_label: str, out_label: str) -> str:
    """Gentle Ken-Burns zoom-in from 1.0 to `zoom` over the clip, centered.

    Uses zoompan driven by the absolute output-frame index `on` (not the `zoom` var, which
    resets per input frame at d=1 on video and would never accumulate). This ffmpeg build
    supports zoompan (crop's per-frame `eval` is unavailable — see _diagram_pan)."""
    frames = max(1, int(round(duration * spec.fps)))
    return (
        f"{in_label}zoompan=z='min(1+{zoom - 1:.4f}*on/{frames},{zoom:.4f})':d=1:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={spec.width}x{spec.height}:fps={spec.fps}{out_label}"
    )


def _caption_text(scene: Scene, burn: BurnTrack) -> str:
    if burn == "en":
        return scene.narration
    if burn == "ja":
        return scene.subtitle_ja
    return ""


def _write_scene_srt(scene: Scene, burn: BurnTrack) -> Path | None:
    """One-cue SRT spanning the whole clip; returned path lives in BUILD_DIR so the
    subtitles filter can reference it by basename (avoids Windows path escaping)."""
    text = _caption_text(scene, burn)
    if not text.strip():
        return None
    srt = BUILD_DIR / f"{scene.id}.cap.srt"
    srt.write_text(
        f"1\n{srt_timestamp(0)} --> {srt_timestamp(scene.duration)}\n{text}\n",
        encoding="utf-8",
    )
    return srt


def _video_chain(scene: Scene, spec: VideoSpec, burn_srt: Path | None) -> str:
    # optional speed-up on the raw video (screen scenes only; narration audio stays 1x).
    # setpts compresses idle/navigation time so more footage fits the same scripted slot.
    if scene.speed != 1.0 and scene.visual == "screen":
        pre = f"[0:v]setpts=PTS/{scene.speed}[sp];"
        vin = "[sp]"
    else:
        pre = ""
        vin = "[0:v]"

    # wide diagram: cover + slow pan for readability (no address bar to worry about)
    if scene.visual == "diagram":
        graph = _diagram_pan(spec, scene.duration)  # references [0:v]; diagram ignores speed/zoom
    # blur the top address-bar strip when flagged (presigned URL safety)
    elif scene.blur_top_bar:
        graph = (
            f"{vin}{_vscale(spec)}[base];"
            f"[base]split=2[full][top];"
            f"[top]crop={spec.width}:{BLUR_H}:0:0,boxblur=luma_radius=24:luma_power=2[bar];"
            f"[full][bar]overlay=0:0[bg]"
        )
    else:
        graph = f"{vin}{_vscale(spec)}[bg]"
    graph = pre + graph

    # optional gentle Ken-Burns zoom-in (skip diagram, which already pans)
    bg = "[bg]"
    if scene.zoom > 1.0 and scene.visual != "diagram":
        graph += ";" + _zoom_chain(spec, scene.duration, scene.zoom, "[bg]", "[bgz]")
        bg = "[bgz]"

    tail = f"{bg}{_fade(scene.duration)}"
    if burn_srt is not None:
        tail += f",subtitles={burn_srt.name}:force_style='{CAPTION_STYLE}'"
    return f"{graph};{tail}[v]"


def _resolve_source(scene: Scene) -> tuple[Path, bool]:
    """Return (source_path, is_still). Missing screen clips fall back to a placeholder."""
    src = scene.source_path
    is_still = scene.visual in ("card", "diagram")
    if src.exists():
        return src, is_still
    if scene.visual == "screen":
        print(f"  [{scene.id}] screen clip missing -> placeholder")
        return PLACEHOLDER, True
    raise PipelineError(f"missing source for {scene.id}: {src}")


def build_clip(scene: Scene, spec: VideoSpec, burn: BurnTrack, ffmpeg: str) -> Path:
    """Render one normalized scene clip into BUILD_DIR. Raises PipelineError on failure."""
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    if not scene.narration_path.exists():
        raise PipelineError(f"missing narration for {scene.id}: run tts_narrate.py")

    src, is_still = _resolve_source(scene)
    # warn if a real screen recording is shorter than its scripted slot (would end early).
    # speed>1 compresses the clip via setpts, so compare the *effective* on-screen length.
    if not is_still and scene.visual == "screen":
        try:
            clip_dur = probe_duration(src)
            effective = clip_dur / scene.speed
            if effective < scene.duration - 0.1:
                sp = f" (÷{scene.speed:g} speed = {effective:.1f}s)" if scene.speed != 1.0 else ""
                print(f"  [{scene.id}] WARNING: clip {clip_dur:.1f}s{sp} < scripted "
                      f"{scene.duration:.0f}s — tail will be short")
        except PipelineError:
            pass  # non-fatal: let the encode attempt proceed and surface any real error

    burn_srt = _write_scene_srt(scene, burn)
    out = BUILD_DIR / f"{scene.id}.mp4"

    if is_still:
        inputs = ["-loop", "1", "-t", f"{scene.duration}", "-i", str(src), "-i", str(scene.narration_path)]
    else:
        # seek into the recording before the shot window (skips lead-in / loading frames)
        seek = ["-ss", f"{scene.trim_start}"] if scene.trim_start > 0 else []
        inputs = [*seek, "-i", str(src), "-i", str(scene.narration_path)]

    fc = f"{_video_chain(scene, spec, burn_srt)};[1:a]apad[a]"
    cmd = [
        ffmpeg, "-y", *inputs,
        "-filter_complex", fc,
        "-map", "[v]", "-map", "[a]",
        "-t", f"{scene.duration}",
        *_encode_args(spec),
        out.name,
    ]
    # cwd=BUILD_DIR so the subtitles filter resolves the .srt by basename
    result = run(cmd, cwd=BUILD_DIR)
    if result.returncode != 0 or not out.exists():
        raise PipelineError(f"ffmpeg failed for {scene.id}:\n{(result.stderr or '')[-1500:]}")
    return out


def concat(clips: list[Path], out: Path, ffmpeg: str) -> None:
    listfile = BUILD_DIR / "concat.txt"
    listfile.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    cmd = [
        ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", listfile.name,
        "-c", "copy", "-movflags", "+faststart", str(out),
    ]
    result = run(cmd, cwd=BUILD_DIR)
    if result.returncode != 0 or not out.exists():
        raise PipelineError(f"concat failed:\n{(result.stderr or '')[-1500:]}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Assemble the BrandForge demo video.")
    ap.add_argument("--burn", choices=["en", "ja", "none"], default="en",
                    help="which caption track to burn onto the video (default en)")
    ap.add_argument("--only", help="build just this scene id (skips concat)")
    args = ap.parse_args(argv)

    try:
        spec, scenes = load_manifest()
        ffmpeg = resolve_ffmpeg()
    except (ValueError, FileNotFoundError, PipelineError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    targets = [s for s in scenes if not args.only or s.id == args.only]
    if not targets:
        print(f"no scene matches --only {args.only!r}", file=sys.stderr)
        return 1

    print(f"assembling {len(targets)} clip(s), burn={args.burn}\n")
    try:
        for s in targets:
            clip = build_clip(s, spec, args.burn, ffmpeg)
            print(f"  built {clip.name}")
    except PipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.only:
        print(f"\nsingle-clip build done: {BUILD_DIR / f'{targets[0].id}.mp4'}")
        return 0

    try:
        concat([BUILD_DIR / f"{s.id}.mp4" for s in scenes], FINAL, ffmpeg)
    except PipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    size_mb = FINAL.stat().st_size / (1024 * 1024)
    print(f"\nOK: {FINAL} ({size_mb:.1f} MB)")
    print("Next: python video/scripts/qa_report.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
