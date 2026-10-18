"""Render a clearly-labelled, asset-free Tokyo storyboard animatic for review.

This is not the final photographic video: scenes needing licensed media are shown as
typographic placeholders. No rights are inferred and no assets are auto-approved.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import sys
from pathlib import Path

# Allow direct execution from the repository root in GitHub Actions and locally.
PROTOTYPE_DIR = Path(__file__).resolve().parent
if str(PROTOTYPE_DIR) not in sys.path:
    sys.path.insert(0, str(PROTOTYPE_DIR))

from PIL import Image, ImageDraw, ImageFont

from destination_render import render_scene_frame
from destination_history import validate_destination_plan

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "destination-prototype" / "tokyo-plan.json"
OUT = ROOT / "output" / "tokyo-storyboard-preview.mp4"
WIDTH, HEIGHT, FPS = 540, 960, 8


def font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def wrap(draw, text, fnt, max_width):
    lines, line = [], ""
    for word in text.split():
        test = (line + " " + word).strip()
        if draw.textbbox((0, 0), test, font=fnt)[2] > max_width and line:
            lines.append(line)
            line = word
        else:
            line = test
    if line:
        lines.append(line)
    return lines


def storyboard_frame(scene, index, total, t):
    # Use the existing production typography/layout renderer, but replace unlicensed
    # image-backed scenes with an explicit placeholder card, never fake archival media.
    needs_media = scene["type"] in {"still_kenburns", "clip", "split_compare"}
    if needs_media:
        view = dict(scene)
        view["type"] = "icon_card"
        view["title"] = "TOKYO: EDO → TOKYO"
        view["labels"] = ["ARCHIVAL ASSET PENDING"]
        view["narration"] = scene.get("narration", "")
        image = render_scene_frame(view, progress=t, width=WIDTH, height=HEIGHT)
        d = ImageDraw.Draw(image, "RGBA")
        label_font = font(16, True)
        label = "STORYBOARD PREVIEW • NOT FINAL FOOTAGE"
        d.rounded_rectangle((12, 12, WIDTH - 12, 48), radius=8, fill=(120, 26, 24, 235))
        d.text((22, 20), label, font=label_font, fill=(255, 255, 255, 255))
        tag = "VISUAL NEEDED: " + ", ".join(scene.get("asset_tags", [])[:2])
        tag_font = font(16, True)
        lines = wrap(d, tag, tag_font, WIDTH - 44)
        y = 65
        for line in lines[:3]:
            d.text((22, y), line, font=tag_font, fill=(255, 223, 164, 255), stroke_width=1, stroke_fill=(0,0,0,220))
            y += 22
        return image

    view = dict(scene)
    if view["type"] == "map":
        view["labels"] = [view.get("labels", [view.get("label", "HISTORICAL CONTEXT")])[0] if view.get("labels") else "HISTORICAL CONTEXT"]
    return render_scene_frame(view, progress=t, width=WIDTH, height=HEIGHT)


def main():
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    checked = validate_destination_plan(plan)
    scenes = checked["scenes"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tokyo-storyboard-") as td:
        td = Path(td)
        concat = td / "frames.txt"
        with concat.open("w", encoding="utf-8") as listing:
            frame_idx = 0
            for scene_idx, scene in enumerate(scenes, 1):
                duration = float(scene["duration_seconds"])
                count = max(1, round(duration * FPS))
                for j in range(count):
                    frame = storyboard_frame(scene, scene_idx, len(scenes), j / max(1, count - 1))
                    path = td / f"frame-{frame_idx:05d}.jpg"
                    frame.save(path, quality=88, optimize=True)
                    listing.write(f"file '{path.as_posix()}'\\n")
                    frame_idx += 1
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat),
            "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264",
            "-preset", "ultrafast", "-crf", "25", "-movflags", "+faststart",
            "-an", str(OUT),
        ], check=True)
    print(f"Storyboard preview created: {OUT}")
    print(f"Scenes: {len(scenes)}; planned duration: {checked['duration_seconds']:.1f}s")
    print("NOTE: image-backed scenes are placeholders; no licensed media or narration included.")


if __name__ == "__main__":
    main()
