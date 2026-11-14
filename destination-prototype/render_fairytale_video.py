#!/usr/bin/env python3
"""Render a private, review-only Rumpelstiltskin short from generated scene images.

The renderer never approves assets or publishes/sends the MP4. It requires all
nine generated images and records preview_only=true in the output metadata.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import json
import math
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTOTYPE_DIR = Path(__file__).resolve().parent
for import_path in (str(REPO_ROOT), str(PROTOTYPE_DIR)):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps
from moviepy.video.VideoClip import VideoClip
from fairytale_planner import validate_episode_plan

from captions import build_ass, word_timings
from folklore_plan import validate_folklore_plan
from tts import Narrator, RATE
from story_store import write_json

FPS = 30
WIDTH, HEIGHT = 1080, 1920
CROSSFADE_SECONDS = 0.38
TARGET_MIN, TARGET_MAX = 75.0, 115.0


@lru_cache(maxsize=32)
def _font(size: int, bold: bool = True):
    candidates = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "C:/Windows/Fonts/arialbd.ttf"]
        if bold else
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "C:/Windows/Fonts/arial.ttf"]
    )
    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _safe_output_dir(path: Path) -> Path:
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()):
        raise ValueError(f"Output directory must be empty: {path}")
    return path


def load_inputs(plan_path: Path, images_dir: Path):
    plan_raw = json.loads(plan_path.read_text(encoding="utf-8"))
    plan = (
        validate_episode_plan(plan_raw)
        if plan_raw.get("format") == "dark_fairytale_episode_v1"
        else validate_folklore_plan(plan_raw)
    )
    manifest_path = images_dir / "generation-manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Missing generation-manifest.json; refusing to render untracked images")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("approved") is not False or manifest.get("published") is not False:
        raise ValueError("Generated artwork manifest must remain explicitly unapproved and unpublished")
    if manifest.get("status") != "generated_unapproved_review_only":
        raise ValueError("Unexpected image-generation manifest status")
    rows = {str(row.get("scene_id")): row for row in manifest.get("images", []) if isinstance(row, dict)}
    expected = [scene["id"] for scene in plan["scenes"]]
    if manifest.get("scene_count") != len(expected) or set(rows) != set(expected):
        raise ValueError(f"Expected exactly {len(expected)} generated scene images; found {len(rows)}")
    images = []
    for scene in plan["scenes"]:
        row = rows[scene["id"]]
        image_path = images_dir / str(row.get("file", ""))
        if not image_path.is_file():
            raise ValueError(f"Missing generated image for {scene['id']}: {image_path.name}")
        data = image_path.read_bytes()
        if row.get("sha256") and hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError(f"Image checksum mismatch for {scene['id']}")
        with Image.open(image_path) as opened:
            opened.verify()
        with Image.open(image_path) as opened:
            image = opened.convert("RGB")
            if image.width < 700 or image.height < 1200:
                raise ValueError(f"Image resolution is too small for {scene['id']}: {image.size}")
            ratio = image.width / image.height
            if abs(ratio - (9 / 16)) > 0.035:
                raise ValueError(f"Image is not portrait 9:16 for {scene['id']}: {image.size}")
            if image.getbbox() is None:
                raise ValueError(f"Blank generated image for {scene['id']}")
            images.append(image.copy())
    return plan, manifest, rows, images


def make_contact_sheet(plan: dict, images: list[Image.Image], path: Path) -> None:
    thumb_w, thumb_h = 240, 426
    sheet = Image.new("RGB", (thumb_w * 3, thumb_h * 3), (22, 23, 27))
    draw = ImageDraw.Draw(sheet)
    for i, (scene, image) in enumerate(zip(plan["scenes"], images)):
        thumb = ImageOps.fit(image, (thumb_w, thumb_h), method=Image.Resampling.LANCZOS)
        x, y = (i % 3) * thumb_w, (i // 3) * thumb_h
        sheet.paste(thumb, (x, y))
        draw.rectangle((x, y + thumb_h - 48, x + thumb_w, y + thumb_h), fill=(0, 0, 0))
        label = f"{i + 1}. {scene.get('chapter', scene['title'])}"[:38]
        draw.text((x + 8, y + thumb_h - 37), label, font=_font(14), fill="white")
    sheet.save(path, quality=92)


def _prepare_canvas(image: Image.Image) -> Image.Image:
    # Overscan gives the slow Ken Burns move room without exposing blank edges.
    return ImageOps.fit(
        image.convert("RGB"),
        (round(WIDTH * 1.08), round(HEIGHT * 1.08)),
        method=Image.Resampling.LANCZOS,
    )


def _scene_frame(canvas: Image.Image, progress: float) -> Image.Image:
    progress = max(0.0, min(1.0, progress))
    zoom = 1.0 + 0.08 * progress
    crop_w = max(WIDTH, round(canvas.width / zoom))
    crop_h = max(HEIGHT, round(canvas.height / zoom))
    # A subtle alternating pan keeps the still images alive without aggressive motion.
    direction = 1 if progress < 0.5 else -1
    pan = round(direction * (progress if progress < 0.5 else 1 - progress) * 18)
    left = max(0, min(canvas.width - crop_w, (canvas.width - crop_w) // 2 + pan))
    top = max(0, min(canvas.height - crop_h, (canvas.height - crop_h) // 2))
    return canvas.crop((left, top, left + crop_w, top + crop_h)).resize(
        (WIDTH, HEIGHT), Image.Resampling.LANCZOS
    )


def _write_wav(path: Path, samples: np.ndarray) -> None:
    data = np.asarray(samples, dtype=np.float32).reshape(-1)
    if not data.size or not np.isfinite(data).all():
        raise ValueError("Audio is empty or contains non-finite samples")
    pcm = (np.clip(data, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(pcm)


def make_ambient(path: Path, duration: float) -> None:
    """Create a quiet original atmospheric drone; no external music asset is used."""
    count = max(1, round(duration * RATE))
    t = np.arange(count, dtype=np.float32) / RATE
    slow = 0.76 + 0.16 * np.sin(2 * np.pi * 0.075 * t) + 0.08 * np.sin(2 * np.pi * 0.031 * t + 1.2)
    bed = (
        0.030 * np.sin(2 * np.pi * 55.0 * t)
        + 0.018 * np.sin(2 * np.pi * 82.41 * t + 0.4)
        + 0.010 * np.sin(2 * np.pi * 110.0 * t + 1.1)
    ) * slow
    rng = np.random.default_rng(20261112)
    noise = rng.normal(0.0, 1.0, count).astype(np.float32)
    # Smooth filtered noise provides a little air without sounding like static.
    kernel = np.ones(420, dtype=np.float32) / 420
    airy = np.convolve(noise, kernel, mode="same")
    peak = max(1e-6, float(np.max(np.abs(airy))))
    bed += (airy / peak) * 0.0025
    fade = min(int(RATE * 1.5), count // 3)
    if fade:
        bed[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
        bed[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    _write_wav(path, bed)


def _story_word_timings_by_scene(scenes: list[dict], duration: float) -> list[dict]:
    """Time every authored word and retain the beat it belongs to."""
    tokens = []
    for scene in scenes:
        scene_id = str(scene["id"])
        for token in str(scene["narration"]).split():
            tokens.append((scene_id, token))
    if not tokens or duration <= 0:
        raise ValueError("Continuous story text or measured duration is empty")
    import re
    weights = [max(1, len(re.sub(r"\\W", "", token))) for _, token in tokens]
    total_weight = sum(weights)
    cursor = 0.0
    rows = []
    for (scene_id, token), weight in zip(tokens, weights):
        end = cursor + duration * weight / total_weight
        rows.append({
            "scene_id": scene_id,
            "word": token,
            "start": cursor,
            "end": end,
        })
        cursor = end
    rows[-1]["end"] = duration
    return rows


def _scene_entries(scenes: list[dict], words: list[dict], intro_seconds: float = 0.0) -> list[dict]:
    """Map word timings to the beat explicitly, not by a second tokenizer."""
    entries = []
    cursor = 0
    for scene in scenes:
        scene_id = str(scene["id"])
        segment = []
        while cursor < len(words) and words[cursor].get("scene_id") == scene_id:
            segment.append(words[cursor])
            cursor += 1
        if not segment:
            raise ValueError(f"No timed narration words found for scene {scene_id}")
        entries.append({
            "start": intro_seconds + float(segment[0]["start"]),
            "duration": max(0.05, float(segment[-1]["end"]) - float(segment[0]["start"])),
        })
    if cursor != len(words):
        raise ValueError("Some authored narration timings do not belong to a scene")
    return entries


def _build_story_text(scenes: list[dict]) -> str:
    """Join authored beats with real paragraph breaks for natural TTS phrasing."""
    return "\n\n".join(str(scene["narration"]).strip() for scene in scenes)


def _build_srt(events: list[dict]) -> str:
    """Create a standards-compliant SRT sidecar with real line endings."""
    blocks = []
    index = 1

    def stamp(value: float) -> str:
        milliseconds = round(value * 1000)
        hours, milliseconds = divmod(milliseconds, 3_600_000)
        minutes, milliseconds = divmod(milliseconds, 60_000)
        seconds, milliseconds = divmod(milliseconds, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    for event in events:
        words = event["words"]
        for start in range(0, len(words), 5):
            group = words[start:start + 5]
            begin = float(event["start"]) + float(group[0]["start"])
            end = float(event["start"]) + float(group[-1]["end"])
            text = " ".join(str(word["word"]) for word in group)
            blocks.append(f"{index}\n{stamp(begin)} --> {stamp(end)}\n{text}")
            index += 1
    return "\n\n".join(blocks) + "\n"


def write_continuous_narration(
    path: Path,
    audio_row: dict,
    total_seconds: float,
) -> list[dict]:
    """Write speech from the first frame with no silent title-card lead-in."""
    with wave.open(audio_row["path"], "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, RATE):
            raise ValueError("Kokoro output must be mono 24kHz PCM")
        frames = source.readframes(source.getnframes())
        speech_seconds = len(frames) / (2 * RATE)
    expected_speech = float(audio_row["duration"])
    if abs(speech_seconds - expected_speech) > 0.08:
        raise ValueError("Measured Kokoro WAV duration does not match its narration timing")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(frames)
        remaining = round(total_seconds * RATE) - round(speech_seconds * RATE)
        if remaining < -2:
            raise ValueError("Continuous narration is longer than the target video")
        if remaining > 0:
            output.writeframes(b"\0\0" * remaining)
    return [{"start": 0.0, "words": audio_row["words"]}]

def _probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height,r_frame_rate", "-show_entries",
         "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode:
        raise RuntimeError("ffprobe could not inspect the preview MP4")
    data = json.loads(result.stdout)
    video = next((x for x in data.get("streams", []) if x.get("codec_type") == "video"), None)
    audio = next((x for x in data.get("streams", []) if x.get("codec_type") == "audio"), None)
    if not video or not audio:
        raise ValueError("Rendered MP4 must contain video and narration audio")
    duration = float(data["format"]["duration"])
    if (video.get("width"), video.get("height"), video.get("r_frame_rate")) != (WIDTH, HEIGHT, "30/1"):
        raise ValueError(f"Wrong video format: {video}")
    if not TARGET_MIN <= duration <= TARGET_MAX:
        raise ValueError(f"Rendered duration {duration:.2f}s is outside {TARGET_MIN:.0f}–{TARGET_MAX:.0f}s")
    return {"duration_seconds": duration, "video_stream": video, "audio_stream": audio}


def _audio_mix_filter() -> str:
    """Mix narration (input 1) with ambience (input 2); input 0 is silent video."""
    return (
        "[1:a]volume=1.0[narr];[2:a]volume=0.22[bed];"
        "[narr][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        "alimiter=limit=0.95[mixed]"
    )


def render(plan_path: Path, images_dir: Path, output_dir: Path, voice: str, speed: float):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and ffprobe are required")
    plan, generation_manifest, rows, images = load_inputs(plan_path, images_dir)
    output_dir = _safe_output_dir(output_dir)
    work = output_dir / "work"
    cache = output_dir / "audio-cache"
    work.mkdir()
    cache.mkdir()
    contact_sheet = output_dir / "contact-sheet.jpg"
    make_contact_sheet(plan, images, contact_sheet)

    # One full-script synthesis gives Kokoro the whole narrative arc instead of
    # resetting voice/prosody at every still-image boundary.
    story_text = "\n\n".join(scene["narration"].strip() for scene in plan["scenes"])
    narrator = Narrator(voice, speed)
    print(f"Narrating the complete {len(plan['scenes'])}-beat story in one continuous pass")
    narration_row = narrator.synthesize(story_text, cache)
    expected_total = float(narration_row["duration"])
    # Rebuild timing tokens from the exact authored script text. Do not trust
    # a TTS-side tokenizer that may normalise or omit punctuation-bound words.
    narration_row["words"] = _story_word_timings_by_scene(plan["scenes"], float(narration_row["duration"]))

    if not TARGET_MIN <= expected_total <= TARGET_MAX:
        target_seconds = 92.0
        corrected_speed = max(0.78, min(1.25, speed * expected_total / target_seconds))
        if abs(corrected_speed - speed) < 0.015:
            raise ValueError(
                f"Continuous Kokoro narration is {expected_total:.2f}s, outside the "
                f"{TARGET_MIN:.0f}–{TARGET_MAX:.0f}s review range"
            )
        print(f"Adjusting continuous Kokoro pace from {speed:.2f} to {corrected_speed:.2f}")
        narrator = Narrator(voice, corrected_speed)
        narration_row = narrator.synthesize(story_text, cache)
        expected_total = float(narration_row["duration"])
        narration_row["words"] = _story_word_timings_by_scene(plan["scenes"], float(narration_row["duration"]))
        speed = corrected_speed
        if not TARGET_MIN <= expected_total <= TARGET_MAX:
            raise ValueError(
                f"Adjusted continuous narration is {expected_total:.2f}s, outside the "
                f"{TARGET_MIN:.0f}–{TARGET_MAX:.0f}s review range"
            )

    entries = _scene_entries(plan["scenes"], narration_row["words"], 0.0)
    total = expected_total
    events = write_continuous_narration(
        work / "narration.wav", narration_row, total
    )
    ambient_path = work / "ambient.wav"
    make_ambient(ambient_path, total)

    canvases = [_prepare_canvas(image) for image in images]
    def frame(t: float):
        scene_index = len(entries) - 1
        for i, entry in enumerate(entries):
            if t < entry["start"] + entry["duration"]:
                scene_index = i
                break
        entry = entries[scene_index]
        progress = (t - entry["start"]) / max(0.01, entry["duration"])
        current = _scene_frame(canvases[scene_index], progress)
        remaining = entry["start"] + entry["duration"] - t
        if scene_index + 1 < len(canvases) and 0 <= remaining < CROSSFADE_SECONDS:
            alpha = 1.0 - remaining / CROSSFADE_SECONDS
            next_image = _scene_frame(canvases[scene_index + 1], 0.02)
            current = Image.blend(current, next_image, max(0.0, min(1.0, alpha)))
        return np.asarray(current)

    clip = VideoClip(frame, duration=total)
    silent_path = work / "silent.mp4"
    try:
        clip.write_videofile(
            str(silent_path), fps=FPS, codec="libx264", audio=False,
            preset="veryfast", threads=2, logger=None,
            ffmpeg_params=["-crf", "21", "-pix_fmt", "yuv420p"],
        )
    finally:
        clip.close()

    narration_events = events
    ass_path = work / "captions.ass"
    ass_path.write_text(build_ass(narration_events, (WIDTH, HEIGHT), karaoke=True), encoding="utf-8")
    audio_video = output_dir / "video.mp4"
    mix_filter = _audio_mix_filter()
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(silent_path), "-i", str(work / "narration.wav"), "-i", str(ambient_path),
            "-filter_complex", mix_filter,
            "-vf", f"subtitles={ass_path.as_posix()}",
            "-map", "0:v:0", "-map", "[mixed]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-threads", "2",
            "-c:a", "aac", "-b:a", "160k", "-ar", str(RATE),
            "-t", f"{total:.3f}", "-movflags", "+faststart", str(audio_video),
        ],
        capture_output=True, text=True, timeout=5400,
    )
    if result.returncode:
        raise RuntimeError("Final FFmpeg mux failed: " + result.stderr[-2500:])

    probe = _probe(audio_video)
    # Sidecar SRT uses the timings for the single continuous narration take.
    (output_dir / "captions.srt").write_text(_build_srt(narration_events), encoding="utf-8")

    metadata = {
        "title": plan["title"],
        "series": plan.get("series"),
        "story_id": plan.get("story_id"),
        "source": plan.get("source"),
        "disclosure": plan.get("disclosure"),
        "model_strategy": generation_manifest.get("model_strategy", {}),
        "symbolic_final_scene_note": generation_manifest.get("symbolic_final_scene_note"),
        "scene_count": len(images),
        "duration_seconds": probe["duration_seconds"],
        "resolution": f"{WIDTH}x{HEIGHT}",
        "fps": FPS,
        "narration": "Kokoro CPU TTS, one continuous full-story synthesis pass",
        "voice": voice,
        "voice_speed": speed,
        "music": "original procedural low ambient drone",
        "captions": "estimated word-timed karaoke captions plus SRT sidecar",
        "transitions": f"{CROSSFADE_SECONDS:.2f}s crossfades aligned to story beat boundaries",
        "narration_word_count": len(story_text.split()),
        "storytelling_mode": "continuous conversational storyteller narration; no per-scene voice resets",
        "opening": "full_bleed_scene_one_no_title_overlay",
        "chapter_overlays": False,
        "narration_style": "spoken storyteller with direct dialogue, varied sentence lengths and dramatic punctuation",
        "image_manifest": str((images_dir / "generation-manifest.json").name),
        "preview_only": True,
        "approved": False,
        "published": False,
        "rendered_for_review": True,
        "fact_check_status": "requires human review before publication",
        "asset_review_status": "generated assets require human review before publication",
        "daily_fairytale_pipeline": plan.get("format") == "dark_fairytale_episode_v1",
    }
    write_json(output_dir / "render-metadata.json", metadata)
    (output_dir / "PREVIEW-ONLY.txt").write_text(
        "PRIVATE REVIEW PREVIEW — NOT APPROVED FOR PUBLICATION.\n"
        "All generated scene images and factual narration require human review.\n",
        encoding="utf-8",
    )
    description = (
        plan["title"] + "\n\n" + plan["narration"] + "\n\n"
        "PREVIEW ONLY — do not publish or distribute as a final factual episode.\n"
    )
    symbolic_note = generation_manifest.get("symbolic_final_scene_note")
    if symbolic_note:
        description += "\nVISUAL NOTE: " + symbolic_note + "\n"
    (output_dir / "description.txt").write_text(description, encoding="utf-8")
    print(f"Preview MP4 created: {audio_video} ({probe['duration_seconds']:.2f}s)")
    return audio_video, metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--voice", choices=("bm_george", "bm_fable", "am_michael"), default="bm_fable")
    parser.add_argument("--speed", type=float, default=1.0)
    args = parser.parse_args()
    video, _ = render(args.plan, args.images, args.out, args.voice, args.speed)
    print(f"Review-only video: {video}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
