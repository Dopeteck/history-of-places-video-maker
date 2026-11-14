#!/usr/bin/env python3
"""Assemble a review-only nine-scene fairytale set when daily image quota is exhausted.

The first eight scenes must already exist and pass validation. The ninth scene is
an explicitly documented symbolic crop derived from the forest-fire illustration;
it is not represented as a new AI-generated or literal floorboard illustration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from generate_fairytale_scenes import (
    MODEL_ID,
    RUMPELSTILTSKIN_MODEL_ID,
    RUMPELSTILTSKIN_SCENES,
    build_prompt,
)

WIDTH, HEIGHT = 700, 1240
FINAL_SCENE_ID = "beat-09-foot-revelation"
SOURCE_SCENE_ID = "beat-08-forest-fire"
SYMBOLIC_NOTE = (
    "The final foot-revelation beat is represented by a symbolic close-up crop of the "
    "existing forest-fire illustration, with ember-lit ground cracks. Cloudflare's daily "
    "free Neuron allocation was exhausted before a dedicated final image could be made. "
    "This derivative is atmospheric symbolism; it does not literally depict the 1857 "
    "floorboard scene or the character wrenching his foot free."
)


def _verify_portrait(path: Path) -> tuple[int, int, str, bytes]:
    data = path.read_bytes()
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        width, height = image.size
        fmt = image.format or ""
    if width < 700 or height < 1200:
        raise ValueError(f"Scene image is too small: {path.name} ({width}x{height})")
    if abs((width / height) - (9 / 16)) > 0.035:
        raise ValueError(f"Scene image is not portrait 9:16: {path.name} ({width}x{height})")
    if fmt not in {"JPEG", "PNG", "WEBP"}:
        raise ValueError(f"Unsupported scene image format: {path.name} ({fmt})")
    return width, height, fmt, data


def create_symbolic_final(source_path: Path, destination_path: Path) -> dict[str, Any]:
    """Create a distinct lower-body/fire close-up with an ember-crack motif."""
    width, height, fmt, source_bytes = _verify_portrait(source_path)
    if fmt not in {"JPEG", "PNG", "WEBP"}:
        raise ValueError("Forest-fire source image format is unsupported")
    with Image.open(source_path) as source:
        image = source.convert("RGB")
        crop = image.crop((100, 600, 550, 1400)).resize(
            (WIDTH, HEIGHT), Image.Resampling.LANCZOS
        ).convert("RGBA")

    # Subtle glowing fissures are drawn into the ground below the figure. This
    # reads as a symbolic echo of the final foot-stamp without inventing anatomy.
    glow = Image.new("RGBA", crop.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    cracks = [
        [(310, 1135), (293, 1162), (316, 1182), (302, 1210), (318, 1235)],
        [(316, 1182), (348, 1170), (365, 1148)],
        [(302, 1210), (277, 1225), (264, 1238)],
        [(316, 1182), (335, 1205), (358, 1217)],
    ]
    for path in cracks:
        draw.line(path, fill=(255, 112, 24, 110), width=20, joint="curve")
    glow = glow.filter(ImageFilter.GaussianBlur(9))
    crop = Image.alpha_composite(crop, glow)
    draw = ImageDraw.Draw(crop)
    for path in cracks:
        draw.line(path, fill=(255, 190, 92, 205), width=3, joint="curve")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    crop.convert("RGB").save(destination_path, format="JPEG", quality=64, optimize=True, progressive=True)
    fw, fh, ff, final_bytes = _verify_portrait(destination_path)
    return {
        "width": fw,
        "height": fh,
        "format": ff,
        "bytes": len(final_bytes),
        "sha256": hashlib.sha256(final_bytes).hexdigest(),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
    }


def assemble(plan_path: Path, images_dir: Path, identity_reference: Path, scale_reference: Path) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    scenes = plan.get("scenes", [])
    if len(scenes) != 9 or str(scenes[-1].get("id")) != FINAL_SCENE_ID:
        raise ValueError("Expected the nine-scene Rumpelstiltskin plan with foot-revelation as its final beat")
    for ref_path, label in ((identity_reference, "identity"), (scale_reference, "scale")):
        if not ref_path.is_file():
            raise ValueError(f"Missing {label} reference image: {ref_path}")
        with Image.open(ref_path) as reference:
            reference.verify()
        with Image.open(ref_path) as reference:
            if reference.width >= 512 or reference.height >= 512:
                raise ValueError(f"{label} reference image must be strictly smaller than 512x512")

    existing_manifest_path = images_dir / "generation-manifest.json"
    existing_rows = {}
    if existing_manifest_path.is_file():
        try:
            existing_manifest = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
            existing_rows = {
                str(row.get("scene_id")): row
                for row in existing_manifest.get("images", [])
                if isinstance(row, dict)
            }
        except (OSError, json.JSONDecodeError):
            existing_rows = {}

    rows = []
    for index, scene in enumerate(scenes[:-1], 1):
        scene_id = str(scene["id"])
        candidates = sorted(
            path for path in images_dir.glob(f"*-{scene_id}.*")
            if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        )
        image_path = next((path for path in candidates if path.is_file()), None)
        if image_path is None:
            raise ValueError(f"Missing existing generated scene: {scene_id}")
        width, height, fmt, data = _verify_portrait(image_path)
        character_scene = scene_id in RUMPELSTILTSKIN_SCENES
        model_id = RUMPELSTILTSKIN_MODEL_ID if character_scene else MODEL_ID
        previous = existing_rows.get(scene_id, {})
        prompt_hash = previous.get("prompt_sha256") or hashlib.sha256(
            build_prompt(scene, index).encode("utf-8")
        ).hexdigest()
        rows.append({
            "scene_id": scene_id,
            "title": scene.get("title", scene_id),
            "file": image_path.name,
            "width": width,
            "height": height,
            "format": fmt,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "prompt_sha256": prompt_hash,
            "model": model_id,
            "preferred_model": model_id,
            "model_fallback_used": False,
            "uses_character_reference": character_scene,
            "uses_scale_reference": character_scene,
            "resumed_from_partial_run": True,
            "approved": False,
        })

    source_path = images_dir / "08-beat-08-forest-fire.jpg"
    final_path = images_dir / "09-beat-09-foot-revelation.jpg"
    final_info = create_symbolic_final(source_path, final_path)
    final_scene = scenes[-1]
    final_prompt = build_prompt(final_scene, 9)
    rows.append({
        "scene_id": FINAL_SCENE_ID,
        "title": final_scene.get("title", FINAL_SCENE_ID),
        "file": final_path.name,
        "width": final_info["width"],
        "height": final_info["height"],
        "format": final_info["format"],
        "bytes": final_info["bytes"],
        "sha256": final_info["sha256"],
        "prompt_sha256": hashlib.sha256(final_prompt.encode("utf-8")).hexdigest(),
        "model": "derived_symbolic_crop",
        "preferred_model": RUMPELSTILTSKIN_MODEL_ID,
        "model_fallback_used": False,
        "uses_character_reference": True,
        "uses_scale_reference": True,
        "resumed_from_partial_run": False,
        "derived_symbolically": True,
        "symbolic_source_scene": SOURCE_SCENE_ID,
        "symbolic_source_sha256": final_info["source_sha256"],
        "symbolic_note": SYMBOLIC_NOTE,
        "approved": False,
    })

    manifest = {
        "story": plan.get("title", plan.get("series", "Untitled story")),
        "model_strategy": (existing_manifest.get("model_strategy") if existing_rows else None) or {
            "character_scenes": RUMPELSTILTSKIN_MODEL_ID,
            "other_scenes": MODEL_ID,
            "final_scene": "symbolic crop derived from beat-08-forest-fire",
        },
        "width": WIDTH,
        "height": HEIGHT,
        "status": "generated_unapproved_review_only",
        "approved": False,
        "rendered": False,
        "published": False,
        "generation_mode": "partial_daily_allocation_symbolic_completion",
        "daily_cloudflare_allocation_exhausted": True,
        "reference_image_sha256": hashlib.sha256(identity_reference.read_bytes()).hexdigest(),
        "scale_reference_sha256": hashlib.sha256(scale_reference.read_bytes()).hexdigest(),
        "symbolic_final_scene_note": SYMBOLIC_NOTE,
        "scene_count": len(rows),
        "images": rows,
    }
    (images_dir / "generation-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print("Assembled nine-scene review set; final beat is explicitly marked symbolic and unapproved.")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--identity-reference", type=Path, required=True)
    parser.add_argument("--scale-reference", type=Path, required=True)
    args = parser.parse_args()
    manifest = assemble(args.plan, args.images, args.identity_reference, args.scale_reference)
    print(f"Review scene count: {manifest['scene_count']}; approved={manifest['approved']}; published={manifest['published']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
