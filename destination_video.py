"""Render a reviewed destination-history plan to a private MP4 preview.

This is additive: it does not alter the legacy stickman renderer or publishing workflow.
Every photo must resolve through the license-checked assets manifest. Use --smoke only for
renderer plumbing tests; smoke audio is a tone and is not suitable for publication.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
from moviepy.video.VideoClip import VideoClip

from captions import build_ass
from destination_history import load_asset_manifest, validate_destination_plan, credits_text
from destination_render import render_frame_array, validate_render_inputs
from tts import Narrator, RATE, tone
from story_store import write_json

FPS = 24


def _run(args: list[str], cwd: Path) -> None:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=5400)
    if proc.returncode:
        raise RuntimeError("FFmpeg failed: " + proc.stderr[-2500:])


def _duration(path: Path) -> float:
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                           "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                          capture_output=True, text=True, timeout=30)
    if proc.returncode:
        raise RuntimeError("ffprobe could not read rendered MP4: " + proc.stderr[-1000:])
    value = float(proc.stdout.strip())
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid rendered duration")
    return value


def _combine_audio(path: Path, audio_items: list[dict], entries: list[dict], total: float) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        cursor = 0
        for audio, entry in zip(audio_items, entries):
            start = round(entry["start"] * RATE)
            if start < cursor:
                raise ValueError("Narration segments overlap")
            out.writeframes(b"\\0\\0" * (start - cursor))
            with wave.open(audio["path"], "rb") as src:
                if (src.getnchannels(), src.getsampwidth(), src.getframerate()) != (1, 2, RATE):
                    raise ValueError("Expected mono 24 kHz PCM narration")
                data = src.readframes(src.getnframes())
                out.writeframes(data)
                cursor = start + len(data) // 2
        out.writeframes(b"\\0\\0" * max(0, round(total * RATE) - cursor))


def render_plan(plan_path: Path, manifest_path: Path, asset_root: Path, output: Path,
                voice_name: str = "bm_george", speed: float = 1.0,
                layout: str | None = None, smoke: bool = False, preview: bool = True) -> Path:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and ffprobe are required")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if layout:
        plan["layout"] = layout
    plan = validate_destination_plan(plan)
    manifest = load_asset_manifest(manifest_path)
    assets = validate_render_inputs(plan, manifest, asset_root)
    output.mkdir(parents=True, exist_ok=True)
    audio_items, entries = [], []
    cursor = 0.0
    narrator = None if smoke else Narrator(voice_name, speed)
    cache = output / "audio-cache"
    for index, scene in enumerate(plan["scenes"]):
        if smoke:
            wav = output / f"smoke-{index:02d}.wav"
            tone(wav, float(scene["duration_seconds"]))
            seconds = float(scene["duration_seconds"])
            words = [{"word": word, "start": seconds * i / max(1, len(scene["narration"].split())),
                      "end": seconds * (i + 1) / max(1, len(scene["narration"].split()))}
                     for i, word in enumerate(scene["narration"].split())]
            item = {"path": str(wav.resolve()), "duration": seconds, "words": words,
                    "alignment": "synthetic_smoke"}
        else:
            item = narrator.synthesize(scene["narration"], cache)
            seconds = float(item["duration"])
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError(f"Scene {scene['id']} has invalid narration duration")
        entries.append({"start": cursor, "duration": seconds})
        cursor += seconds
        audio_items.append(item)
    if not 45 <= cursor <= 60:
        raise ValueError(f"Measured narration totals {cursor:.1f}s; target is 45–60s. Edit narration or pacing before render.")
    width, height = (540, 960) if preview else (1080, 1920)
    _combine_audio(output / "audio.wav", audio_items, entries, cursor)

    scenes = plan["scenes"]
    def frame(t: float) -> np.ndarray:
        for scene, entry in zip(scenes, entries):
            if t < entry["start"] + entry["duration"]:
                progress = max(0.0, min(1.0, (t - entry["start"]) / entry["duration"]))
                return render_frame_array({**scene, "layout": plan["layout"]}, progress,
                                          assets.get(scene["id"]), width, height)
        last, entry = scenes[-1], entries[-1]
        return render_frame_array({**last, "layout": plan["layout"]}, 1.0,
                                  assets.get(last["id"]), width, height)

    clip = VideoClip(frame, duration=cursor)
    try:
        clip.write_videofile(str(output / "silent.mp4"), fps=FPS, codec="libx264",
                             audio=False, preset="veryfast", threads=2, logger=None,
                             ffmpeg_params=["-crf", "23", "-pix_fmt", "yuv420p"])
    finally:
        clip.close()
    events = [{"start": entry["start"], "words": audio["words"]}
              for entry, audio in zip(entries, audio_items)]
    (output / "captions.ass").write_text(build_ass(events, (width, height), karaoke=True), encoding="utf-8")
    _run(["ffmpeg", "-y", "-v", "error", "-i", "silent.mp4", "-i", "audio.wav",
          "-vf", "subtitles=captions.ass", "-c:v", "libx264", "-preset", "veryfast",
          "-crf", "23", "-threads", "2", "-c:a", "aac", "-ar", str(RATE),
          "-b:a", "96k", "-t", str(cursor), "-movflags", "+faststart", "preview.mp4"], output)
    (output / "credits.txt").write_text(credits_text(manifest), encoding="utf-8")
    write_json(output / "render-metadata.json", {
        "title": plan.get("title", "Destination history preview"),
        "duration_seconds": _duration(output / "preview.mp4"),
        "layout": plan["layout"], "aspect_ratio": "9:16",
        "preview_only": bool(preview), "smoke_audio": bool(smoke),
        "approval_status": "not_approved", "publishing_allowed": False,
        "asset_count": len(manifest), "scene_count": len(scenes),
        "alignment": [item["alignment"] for item in audio_items],
        "sources": plan.get("source_ids", []),
        "review_flags": [flag for scene in scenes for flag in scene.get("review_flags", [])],
    })
    (output / "PREVIEW-ONLY.txt").write_text(
        "PRIVATE PREVIEW ONLY. Not approved for publication. Verify every historical claim, "
        "photo license, attribution, crop, caption, and scene before any publishing step.\\n",
        encoding="utf-8")
    return output / "preview.mp4"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--voice", default="bm_george")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--layout", choices=["framed", "full-bleed"])
    parser.add_argument("--smoke", action="store_true", help="Use tones instead of speech; renderer test only")
    parser.add_argument("--full-resolution", action="store_true")
    args = parser.parse_args()
    result = render_plan(args.plan, args.manifest, args.assets, args.output,
                         args.voice, args.speed, args.layout, args.smoke,
                         preview=not args.full_resolution)
    print(f"Preview written: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
