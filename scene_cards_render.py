"""Human-approved scene-card video renderer.

This is additive to the legacy stickman renderer. It renders only after the exact
scene plan has passed the existing human-review contract. Remote image generation
is never triggered here; use the Stage 2 planner's explicit --generate-images option
if paid/provider-backed assets are desired.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from moviepy.video.VideoClip import VideoClip

from captions import build_ass, word_timings
from scene_assets import DEFAULT_SIZE, procedural_illustration, validate_image
from scene_planner import validate_scene_plan_review
from story import digest
from tts import Narrator, RATE, tone
from story_store import write_json

FPS = 24
INTRO_SECONDS = 1.8
TRANSITION_SECONDS = 0.32
INK = "#2B2B2B"
IVORY = "#F7F1E3"
TEAL = "#1FA2A6"
GOLD = "#E0A526"


def _font(size: int, bold: bool = False):
    paths = ([
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ] if bold else [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ])
    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _text_fit(draw: ImageDraw.ImageDraw, text: str, max_width: int, size: int,
              minimum: int = 14, bold: bool = True):
    text = str(text)
    while size > minimum:
        f = _font(size, bold)
        if draw.textbbox((0, 0), text, font=f)[2] <= max_width:
            return f
        size -= 2
    return _font(minimum, bold)


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS)


def _load_scene_image(scene: dict, assets_dir: Path | None, cache_dir: Path) -> tuple[Image.Image, dict]:
    path = assets_dir / (scene["id"] + ".png") if assets_dir else None
    if path and path.is_file():
        with Image.open(path) as opened:
            image = _cover(opened.copy(), DEFAULT_SIZE)
        image = validate_image(image, DEFAULT_SIZE)
        return image, {"provider": "provided_asset", "path": str(path)}
    image = procedural_illustration(scene["visual_prompt"], DEFAULT_SIZE, scene["background"])
    image = validate_image(image, DEFAULT_SIZE)
    return image, {"provider": "procedural", "path": None}


def _compose_scene(image: Image.Image, plan: dict, scene: dict,
                   canvas_size: tuple[int, int], is_intro: bool = False) -> Image.Image:
    width, height = canvas_size
    # Full-frame background is derived from the current card art, blurred and darkened.
    background = _cover(image, canvas_size).filter(ImageFilter.GaussianBlur(max(12, width // 35)))
    shade = Image.new("RGBA", canvas_size, (20, 25, 35, 112))
    frame = Image.alpha_composite(background.convert("RGBA"), shade)

    card_w = int(width * (0.88 if height > width else 0.86))
    card_h = round(card_w * 9 / 16)
    card_h = min(card_h, int(height * 0.64))
    card_w = round(card_h * 16 / 9)
    x, y = (width - card_w) // 2, (height - card_h) // 2
    shadow = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((x + 7, y + 10, x + card_w + 7, y + card_h + 10),
                         radius=max(12, width // 90), fill=(0, 0, 0, 105))
    frame = Image.alpha_composite(frame, shadow.filter(ImageFilter.GaussianBlur(12)))

    card = _cover(image, (card_w, card_h)).convert("RGBA")
    # Light panel gives titles and labels a clean, repeatable reading surface.
    panel_h = max(74, int(card_h * 0.24))
    panel = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    pd.rectangle((0, 0, card_w, panel_h), fill=(247, 241, 227, 242))
    pd.rectangle((0, card_h - int(card_h * .23), card_w, card_h), fill=(247, 241, 227, 230))
    card = Image.alpha_composite(card, panel)
    d = ImageDraw.Draw(card)
    title = plan["title"] if is_intro else scene["title"]
    title_font = _text_fit(d, title, int(card_w * .9), max(20, int(card_w * .044)),
                           minimum=16)
    d.text((card_w // 2, int(panel_h * .48)), title, font=title_font, fill=INK, anchor="mm")
    if is_intro:
        subtitle = "A sourced visual explainer"
        sf = _text_fit(d, subtitle, int(card_w * .85), max(14, int(card_w * .023)), minimum=12, bold=False)
        d.text((card_w // 2, int(panel_h * .80)), subtitle, font=sf, fill=TEAL, anchor="mm")
    else:
        labels = scene.get("labels", [])[:3]
        if labels:
            bottom_y = card_h - int(card_h * .115)
            usable = card_w * .92
            slot = usable / len(labels)
            for i, item in enumerate(labels):
                center_x = int(card_w * .04 + slot * (i + .5))
                label = f'{item["label"]}: {item["value"]}'
                lf = _text_fit(d, label, int(slot * .92), max(12, int(card_w * .022)), minimum=10)
                d.text((center_x, bottom_y), label, font=lf, fill=INK, anchor="mm")
        kind = scene["kind"].replace("_", " ").upper()
        kf = _text_fit(d, kind, int(card_w * .28), max(11, int(card_w * .018)), minimum=10)
        d.rounded_rectangle((int(card_w * .035), int(card_h * .035),
                             int(card_w * .035) + int(card_w * .24), int(card_h * .105)),
                            radius=8, fill=TEAL)
        d.text((int(card_w * .155), int(card_h * .07)), kind, font=kf, fill="white", anchor="mm")
    frame.alpha_composite(card, (x, y))

    # Persistent hook bar: stays visible for every scene, not just the intro.
    d = ImageDraw.Draw(frame)
    margin = int(width * .055)
    bar_y = int(height * .045)
    bar_h = max(42, int(height * .065))
    d.rounded_rectangle((margin, bar_y, width - margin, bar_y + bar_h),
                         radius=max(12, bar_h // 3), fill=(247, 241, 227, 238))
    hook = plan["hook"]
    hf = _text_fit(d, hook, width - 2 * margin - 36, max(16, int(width * .024)), minimum=12)
    d.text((width // 2, bar_y + bar_h // 2), hook, font=hf, fill=INK, anchor="mm")
    return frame.convert("RGB")


def _make_audio(audio_path: Path, audio_rows: list[dict], intro_seconds: float,
                total_seconds: float) -> None:
    with wave.open(str(audio_path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(b"\0\0" * round(intro_seconds * RATE))
        cursor = round(intro_seconds * RATE)
        for row in audio_rows:
            with wave.open(row["path"], "rb") as src:
                if (src.getnchannels(), src.getsampwidth(), src.getframerate()) != (1, 2, RATE):
                    raise ValueError("Narration must be mono 24kHz PCM WAV")
                frames = src.readframes(src.getnframes())
            out.writeframes(frames)
            cursor += len(frames) // 2
        remaining = round(total_seconds * RATE) - cursor
        if remaining > 0:
            out.writeframes(b"\0\0" * remaining)


def _run(args: list[str], cwd: Path) -> None:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=5400)
    if result.returncode:
        raise RuntimeError("FFmpeg failed: " + result.stderr[-2000:])


def render_scene_cards(plan: dict, review: dict, output: Path, assets_dir: Path | None = None,
                       mode: str = "long", preview: bool = False, captions: bool = True,
                       smoke: bool = False, voice_name: str = "bm_george",
                       speed: float = 1.0) -> Path:
    """Render a reviewed scene-card plan. Raises before rendering if approval is missing."""
    if mode not in {"long", "shorts"}:
        raise ValueError("mode must be long or shorts")
    validate_scene_plan_review(plan, review)
    if plan.get("status") != "needs_human_review":
        raise ValueError("Unexpected plan status")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and ffprobe are required")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError(f"Output directory must be empty: {output}")
    work = output / "work"
    cache = output / "audio-cache"
    work.mkdir()
    cache.mkdir()
    size = ((540, 960) if preview else (1080, 1920)) if mode == "shorts" else (
        (960, 540) if preview else (1920, 1080))
    assets_dir = Path(assets_dir).resolve() if assets_dir else None
    started = time.monotonic()
    narrator = None if smoke else Narrator(voice_name, speed)
    images, image_meta, audios, entries = [], [], [], []
    cursor = INTRO_SECONDS
    for index, scene in enumerate(plan["scenes"]):
        image, info = _load_scene_image(scene, assets_dir, work)
        images.append(image)
        image_meta.append({"scene_id": scene["id"], **info})
        if smoke:
            path = work / f"tone-{index:03d}.wav"
            tone(path, 1.6)
            audio = {"path": str(path), "duration": 1.6,
                     "words": word_timings(scene["narration"], 1.6),
                     "alignment": "synthetic_test"}
        else:
            audio = narrator.synthesize(scene["narration"], cache)
        audios.append(audio)
        entries.append({"start": cursor, "duration": audio["duration"]})
        cursor += audio["duration"]
    total = cursor
    base_frames = [_compose_scene(img, plan, scene, size) for img, scene in zip(images, plan["scenes"])]
    intro_frame = _compose_scene(images[0], plan, plan["scenes"][0], size, is_intro=True)

    def frame(t: float):
        if t < INTRO_SECONDS:
            return np.asarray(intro_frame)
        idx = len(entries) - 1
        for i, entry in enumerate(entries):
            if t < entry["start"] + entry["duration"]:
                idx = i
                break
        entry = entries[idx]
        local_t = t - entry["start"]
        transition = plan["scenes"][idx].get("transition", "crossfade")
        if idx + 1 < len(entries) and transition == "crossfade":
            remaining = entry["duration"] - local_t
            if 0 <= remaining < TRANSITION_SECONDS:
                alpha = 1.0 - remaining / TRANSITION_SECONDS
                return np.asarray(Image.blend(base_frames[idx], base_frames[idx + 1], alpha))
        if transition == "push" and local_t < .4:
            alpha = min(1.0, local_t / .4)
            return np.asarray(Image.blend(intro_frame if idx == 0 else base_frames[idx - 1],
                                          base_frames[idx], alpha))
        return np.asarray(base_frames[idx])

    clip = VideoClip(frame, duration=total)
    try:
        clip.write_videofile(str(work / "silent.mp4"), fps=FPS, codec="libx264",
                             audio=False, preset="veryfast", threads=2, logger=None,
                             ffmpeg_params=["-crf", "23", "-pix_fmt", "yuv420p"])
    finally:
        clip.close()
    _make_audio(work / "audio.wav", audios, INTRO_SECONDS, total)
    events = [{"start": entry["start"], "words": audio["words"]}
              for entry, audio in zip(entries, audios)]
    args = ["ffmpeg", "-y", "-v", "error", "-i", "silent.mp4", "-i", "audio.wav"]
    if captions:
        (work / "captions.ass").write_text(build_ass(events, size, karaoke=(mode == "shorts")),
                                             encoding="utf-8")
        args += ["-vf", "subtitles=captions.ass", "-c:v", "libx264",
                 "-preset", "veryfast", "-crf", "23", "-threads", "2"]
    else:
        args += ["-c:v", "copy"]
    args += ["-c:a", "aac", "-ar", "24000", "-b:a", "96k",
             "-t", f"{total:.3f}", "-movflags", "+faststart", "../video.mp4"]
    _run(args, work)
    meta = {
        "title": plan["title"], "hook": plan["hook"], "mode": mode,
        "duration_seconds": total, "resolution": list(size), "fps": FPS,
        "voice": "synthetic tone; no speech" if smoke else voice_name,
        "word_alignment": "synthetic test" if smoke else "estimated from measured audio",
        "plan_sha256": digest(plan), "reviewer": review["reviewer"],
        "approval_required": True, "captions": captions,
        "image_assets": image_meta,
        "disclosure": "Smoke-test audio is a tone, not narration." if smoke else
                      "Synthetic narration; scene-card artwork may be generated or procedural.",
        "render_wall_seconds": round(time.monotonic() - started, 2),
    }
    write_json(output / "render-metadata.json", meta)
    (output / "description.txt").write_text(
        plan["title"] + "\n\n" + plan["hook"] + "\n\n" + meta["disclosure"] + "\n",
        encoding="utf-8")
    return output / "video.mp4"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path, help="scene_plan.json")
    parser.add_argument("--review", required=True, type=Path, help="completed review JSON")
    parser.add_argument("--out", required=True, type=Path, help="new or empty output directory")
    parser.add_argument("--assets-dir", type=Path, help="optional directory of scene_id.png files")
    parser.add_argument("--mode", choices=("long", "shorts"), default="long")
    parser.add_argument("--preview", action="store_true", help="render at reduced resolution")
    parser.add_argument("--no-captions", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="use test tones instead of TTS speech")
    parser.add_argument("--voice", choices=("bm_george", "bm_fable", "am_michael"), default="bm_george")
    parser.add_argument("--speed", type=float, default=1.0)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    review = json.loads(args.review.read_text(encoding="utf-8"))
    video = render_scene_cards(plan, review, args.out, args.assets_dir, args.mode,
                              args.preview, not args.no_captions, args.smoke,
                              args.voice, args.speed)
    print(f"Rendered reviewed scene-card video: {video}")
    print("This command does not publish or schedule the video.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
