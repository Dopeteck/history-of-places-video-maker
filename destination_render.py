"""Photo-first vertical destination renderer; isolated from legacy character scenes.

Uses Pillow for real typography and deterministic Ken Burns framing. It intentionally
refuses unresolved/unlicensed image assets instead of silently substituting artwork.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import math

from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np

WIDTH, HEIGHT = 1080, 1920
SAFE_X = 86
SAFE_TOP = 120
SAFE_BOTTOM = 150
INK = (20, 28, 38)
PAPER = (246, 243, 235)
ACCENT = (229, 172, 65)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"]
        if bold else
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"]
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Any, max_width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def _fit_cover(image: Image.Image, width: int, height: int, progress: float = 0.0) -> Image.Image:
    """Cover crop with a gentle deterministic push; never independently slides layers."""
    image = image.convert("RGB")
    scale = max(width / image.width, height / image.height) * (1.0 + 0.045 * max(0.0, min(1.0, progress)))
    resized = image.resize((math.ceil(image.width * scale), math.ceil(image.height * scale)), Image.Resampling.LANCZOS)
    max_x, max_y = max(0, resized.width - width), max(0, resized.height - height)
    x = int(max_x * (0.35 + 0.3 * progress))
    y = int(max_y * (0.35 + 0.3 * progress))
    return resized.crop((x, y, x + width, y + height))


def render_scene_frame(scene: dict[str, Any], progress: float = 0.0,
                       image_path: str | Path | None = None,
                       width: int = WIDTH, height: int = HEIGHT) -> Image.Image:
    """Render one frame. All titles/captions are actual text, never image text."""
    if scene.get("type") not in {"still_kenburns", "map", "icon_card", "split_compare", "clip"}:
        raise ValueError("Unsupported destination scene type")
    progress = max(0.0, min(1.0, float(progress)))
    layout = scene.get("layout", "framed")
    if layout not in {"framed", "full-bleed"}:
        raise ValueError("layout must be framed or full-bleed")
    kind = scene["type"]
    if kind in {"still_kenburns", "clip", "split_compare"} and not image_path:
        raise ValueError(f"{kind} requires a reviewed, manifest-backed local image")
    if image_path:
        source = Image.open(image_path).convert("RGB")
        if max(source.size) < 1920 or min(source.size) < 1080:
            raise ValueError("Source image must have a long side of at least 1920 px and a short side of at least 1080 px")
        if layout == "full-bleed":
            canvas = _fit_cover(source, width, height, progress)
        else:
            background = _fit_cover(source, width, height, progress).filter(ImageFilter.GaussianBlur(26))
            overlay = Image.new("RGBA", background.size, (5, 12, 20, 118))
            canvas = Image.alpha_composite(background.convert("RGBA"), overlay)
            band_w, band_h = width - 2 * SAFE_X, int(height * 0.47)
            band = _fit_cover(source, band_w, band_h, progress)
            canvas.alpha_composite(band.convert("RGBA"), (SAFE_X, int(height * 0.27)))
            canvas = canvas.convert("RGB")
    else:
        canvas = Image.new("RGB", (width, height), PAPER if kind == "icon_card" else (21, 42, 61))
    draw = ImageDraw.Draw(canvas, "RGBA")
    title_font = _font(max(38, int(width * 0.055)), True)
    caption_font = _font(max(34, int(width * 0.043)), True)
    title = str(scene.get("title", ""))
    if title:
        lines = _wrap(draw, title, title_font, width - 2 * SAFE_X)
        y = SAFE_TOP
        for line in lines[:3]:
            draw.text((SAFE_X, y), line, font=title_font, fill=(*PAPER, 255), stroke_width=2, stroke_fill=(*INK, 220))
            y += int(title_font.size * 1.15)
    if kind in {"icon_card", "map"}:
        label = str(scene.get("labels", [""])[0] if scene.get("labels") else scene.get("label", ""))
        main_font = _font(max(52, int(width * 0.075)), True)
        lines = _wrap(draw, label, main_font, width - 2 * SAFE_X)
        block_h = len(lines) * int(main_font.size * 1.2)
        y = int(height * 0.42) - block_h // 2
        draw.rounded_rectangle((SAFE_X, y - 36, width - SAFE_X, y + block_h + 36), radius=28,
                               fill=(7, 17, 28, 210), outline=(*ACCENT, 255), width=4)
        for line in lines:
            box = draw.textbbox((0, 0), line, font=main_font)
            draw.text(((width - (box[2] - box[0])) // 2, y), line, font=main_font, fill=(*PAPER, 255))
            y += int(main_font.size * 1.2)
    narration = str(scene.get("narration", ""))
    if narration:
        lines = _wrap(draw, narration, caption_font, width - 2 * SAFE_X)
        max_lines = 4
        lines = lines[:max_lines]
        line_h = int(caption_font.size * 1.22)
        box_h = len(lines) * line_h + 46
        y = height - SAFE_BOTTOM - box_h
        draw.rounded_rectangle((SAFE_X // 2, y, width - SAFE_X // 2, height - SAFE_BOTTOM // 2),
                               radius=24, fill=(4, 9, 15, 224))
        for line in lines:
            draw.text((SAFE_X, y + 20), line, font=caption_font, fill=(*PAPER, 255))
            y += line_h
    return canvas


def render_frame_array(scene: dict[str, Any], progress: float = 0.0,
                       image_path: str | Path | None = None,
                       width: int = WIDTH, height: int = HEIGHT) -> np.ndarray:
    """Convenience adapter for MoviePy VideoClip frame callbacks."""
    return np.asarray(render_scene_frame(scene, progress, image_path, width, height))


def validate_render_inputs(plan: dict[str, Any], manifest: list[dict[str, Any]], asset_root: Path) -> dict[str, Path]:
    """Require every image-backed scene to resolve to a licensed manifest asset."""
    from destination_history import validate_asset, validate_destination_plan
    validate_destination_plan(plan)
    resolved: dict[str, Path] = {}
    by_scene: dict[str, list[dict[str, Any]]] = {}
    for raw in manifest:
        asset = validate_asset(raw)
        if asset.get("approved") is not True:
            raise ValueError(f"Asset {asset['local_filename']} has not been explicitly approved after human review")
        by_scene.setdefault(asset["scene_id"], []).append(asset)
    for scene in plan["scenes"]:
        if scene["type"] in {"still_kenburns", "clip", "split_compare"}:
            matches = [item for item in by_scene.get(scene["id"], [])
                       if item.get("media_type", "photo") == "photo"]
            if not matches:
                raise ValueError(f"Scene {scene['id']} has no manifest-backed image/photo (video clips are not yet supported by this renderer)")
            candidate = asset_root / matches[0]["local_filename"]
            if not candidate.is_file():
                raise ValueError(f"Manifest image is missing on disk: {candidate}")
            resolved[scene["id"]] = candidate
    return resolved
