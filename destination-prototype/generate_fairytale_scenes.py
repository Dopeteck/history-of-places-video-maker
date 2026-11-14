#!/usr/bin/env python3
"""Generate review-only fairytale scene images with Cloudflare Workers AI.

Rumpelstiltskin scenes use a visual character reference to maintain identity.
This tool never approves assets and never renders or publishes a video.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image

import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from fairytale_planner import validate_episode_plan

MODEL_ID = "@cf/black-forest-labs/flux-2-klein-4b"
RUMPELSTILTSKIN_MODEL_ID = "@cf/black-forest-labs/flux-2-klein-9b"
API_ROOT = "https://api.cloudflare.com/client/v4/accounts"
OUTPUT_WIDTH = 832
OUTPUT_HEIGHT = 1472
TRANSIENT_RETRIES = 3
RETRY_BASE_SECONDS = 5.0

STYLE_BIBLE = (
    "Dark medieval European fairytale, painterly cinematic realism, dramatic candlelight, "
    "deep forest green, old gold and charcoal shadows. Rich brush texture, believable anatomy."
)

# The image reference is the authority for Rumpelstiltskin's identity and costume.
# Keep the height/framing directions explicit so that a reference does not become
# an ordinary full-sized old man.
RUMPELSTILTSKIN_CHARACTER_LOCK = (
    "CHARACTER REFERENCE: input image 0 is Rumpelstiltskin. Recreate the SAME character "
    "from that reference: same elderly male face, large fiery-red nose, long white beard "
    "and moustache, pointed red cap, red cloak, blue tunic, ochre breeches and curled "
    "shoes. Do not copy the reference background or pose. He is a truly tiny 60-centimetre "
    "adult male folkloric dwarf, only about 40 percent of an adult woman's height. "
    "He is NOT a normal-sized man. Beside an adult woman 165 cm tall, the top of his cap "
    "reaches no higher than her KNEECAP, never her waist or chest. "
    "Input image 1 shows ONLY the height scale: dwarf = 40% of adult woman. "
    "Show both complete bodies from head to shoes on the same floor, with his short bowed "
    "legs visible. He is never "
    "a woman, girl, child, tall man or anonymous hooded figure. Never hide his legs."
)

NEGATIVE_GUIDANCE = (
    "No lettering, captions, watermark, collage, comic panels, modern objects, anime, "
    "plastic 3D toy appearance, malformed anatomy, duplicate people or repeated "
    "character copies, extra limbs, gore or graphic violence."
)

GENERIC_NEGATIVE_GUIDANCE = (
    "No lettering, captions, watermark, collage, comic panels, modern objects, anime, "
    "plastic 3D toy appearance, malformed anatomy, duplicate people or duplicate main "
    "characters, extra limbs, gore or graphic violence."
)

SCENE_VISUALS = {
    "beat-01-threat": (
        "The miller's daughter, an adult woman in her early twenties, alone in a vast "
        "stone chamber piled high with golden straw; a spinning wheel beside her and "
        "the king's threatening silhouette in the doorway. Wide establishing view."
    ),
    "beat-02-boast": (
        "A nervous miller presents his adult daughter, a woman in her early twenties, "
        "before a stern king in a candlelit medieval throne room. The daughter looks "
        "shocked by her father's boast; straw bales behind them."
    ),
    "beat-03-first-bargains": (
        "Wide full-length view of the tiny Rumpelstiltskin beside the much taller "
        "adult miller's daughter at an antique spinning wheel. He spins straw into gold. "
        "Keep the wheel clear of his short bowed legs and curled shoes. Her necklace "
        "is visible as the bargain's price."
    ),
    "beat-04-king-demands": (
        "The stern king leads the adult miller's daughter into a larger stone chamber "
        "filled with straw; golden light spills from the doorway and a spinning wheel waits."
    ),
    "beat-05-child-pact": (
        "WIDE FULL-BODY TWO-SHOT. A clearly adult woman in her early twenties, with "
        "adult facial features and adult body proportions, stands on the left. Rumpelstiltskin "
        "stands on the right, visibly tiny and only 60 centimetres tall; his red cap reaches "
        "only the adult woman's knee. The woman is at least 2.5 times taller than him; "
        "his cap aligns with her knee joint and is clearly shorter than her thigh. "
        "Both stand on the same bare stone floor and are visible head to shoes. His short "
        "bowed legs and curled shoes are clearly visible. He faces her beside a spinning "
        "wheel in a shadowy straw-filled chamber. She has nothing left to give; tense solemn "
        "bargain. There is NO child in this scene."
    ),
    "beat-06-claim": (
        "Wide full-length view in a medieval royal chamber. The adult queen, in her early "
        "twenties, stands beside her peacefully sleeping infant's cradle. Rumpelstiltskin "
        "faces her on the same floor: a tiny 60-centimetre male dwarf whose red cap reaches "
        "no higher than her knee. Show his full body, short bowed legs and curled shoes, with the "
        "queen's much taller adult body visible for scale. No threat to the baby."
    ),
    "beat-07-three-days": (
        "The anxious adult queen at a castle window speaking urgently with a mounted "
        "messenger preparing to ride into a moonlit forest; warm candlelight inside, "
        "cold blue night outside."
    ),
    "beat-08-forest-fire": (
        "A single tiny male dwarf, Rumpelstiltskin, dances alone beside a small forest fire "
        "and crooked woodland hut. His cap reaches only about one-third of the hut door height. "
        "Show exactly ONE person: no other people, no second dwarf, "
        "no female goblin. His whole 60-centimetre body is visible head to shoes, with short "
        "bowed legs and curled shoes; use the hut door for scale. Moonlit trees and drifting sparks."
    ),
    "beat-09-foot-revelation": (
        "One Rumpelstiltskin only. Wide full-body view of the tiny elderly male dwarf after "
        "the queen speaks his name. His short bowed leg is stamped deep into wooden floorboards "
        "as he strains to pull it free with both hands. Show his entire small body, white beard, "
        "red pointed cap and curled shoes; both legs and the trapped foot must be visible. "
        "The adult queen stands farther back for scale. No duplicate Rumpelstiltskin, no second "
        "dwarf, no gore."
    ),
}

RUMPELSTILTSKIN_SCENES = {
    "beat-03-first-bargains",
    "beat-05-child-pact",
    "beat-06-claim",
    "beat-08-forest-fire",
    "beat-09-foot-revelation",
}


def build_prompt(scene: dict[str, Any], index: int) -> str:
    scene_id = str(scene.get("id", ""))
    visual = SCENE_VISUALS.get(scene_id)
    if not visual:
        raise ValueError(f"No approved visual direction exists for scene {scene_id!r}")
    title = str(scene.get("title", scene_id)).strip()

    parts = []
    if scene_id in RUMPELSTILTSKIN_SCENES:
        parts.extend([RUMPELSTILTSKIN_CHARACTER_LOCK, visual])
    else:
        parts.append(visual)
    parts.extend([f"Scene {index}: {title}.", STYLE_BIBLE, NEGATIVE_GUIDANCE])
    prompt = " ".join(parts)
    if len(prompt) > 2048:
        raise ValueError(
            f"Prompt for {scene_id} is {len(prompt)} characters; refusing to truncate "
            "character identity, scale or composition instructions"
        )
    return prompt


def build_episode_prompt(plan: dict[str, Any], scene: dict[str, Any], index: int) -> str:
    """Build a story-agnostic scene prompt from the validated character bible."""
    visual = str(scene.get("visual_prompt", "")).strip()
    if len(visual) < 40:
        raise ValueError(f"Scene {scene.get('id')} needs a specific visual_prompt")
    characters = {item["id"]: item for item in plan.get("characters", [])}
    selected = []
    for character_id in scene.get("character_ids", []):
        character = characters.get(character_id)
        if not character:
            raise ValueError(f"Unknown recurring character in scene {scene.get('id')}: {character_id}")
        selected.append(
            f"{character['name']} ({character['role']}): {character['visual_identity']}"
        )
    if not selected:
        raise ValueError(f"Scene {scene.get('id')} must show at least one defined character")
    recurring_bible = "Recurring character designs; keep these appearances consistent: " + " ".join(selected)
    parts = [
        "Create one cinematic vertical 9:16 storybook illustration. " + visual,
        recurring_bible,
        f"Scene {index} of the fairy tale {plan.get('title', '')}.",
        str(plan.get("visual_style", STYLE_BIBLE)),
        NEGATIVE_GUIDANCE,
    ]
    prompt = " ".join(parts)
    if len(prompt) > 2048:
        raise ValueError(
            f"Prompt for {scene.get('id')} is {len(prompt)} characters; shorten the scene visual prompt, never truncate character identity."
        )
    return prompt


def _decode_image(payload: dict[str, Any]) -> bytes:
    image_value = payload.get("result", {}).get("image")
    if not isinstance(image_value, str) or not image_value:
        raise RuntimeError(f"Cloudflare response did not contain result.image: {json.dumps(payload)[:500]}")
    try:
        data = base64.b64decode(image_value, validate=True)
    except Exception as exc:
        raise RuntimeError("Cloudflare returned invalid base64 image data") from exc
    if not data:
        raise RuntimeError("Cloudflare returned an empty image")
    return data


def generate_one(
    prompt: str,
    account_id: str,
    api_token: str,
    reference_image: bytes | None = None,
    scale_reference_image: bytes | None = None,
    model_id: str = MODEL_ID,
    width: int = OUTPUT_WIDTH,
    height: int = OUTPUT_HEIGHT,
    guidance: float = 4.0,
    session: Any = requests,
) -> bytes:
    """Generate with FLUX.2 Klein, retrying transient provider errors only."""
    url = f"{API_ROOT}/{account_id}/ai/run/{model_id}"
    fields = [
        ("prompt", (None, prompt)),
        ("width", (None, str(width))),
        ("height", (None, str(height))),
        ("guidance", (None, str(guidance))),
    ]
    if reference_image is not None:
        fields.append(("input_image_0", ("rumpelstiltskin-reference.png", reference_image, "image/png")))
    if scale_reference_image is not None:
        fields.append(("input_image_1", ("height-scale-guide.png", scale_reference_image, "image/png")))

    for attempt in range(TRANSIENT_RETRIES + 1):
        try:
            response = session.post(
                url,
                headers={"Authorization": f"Bearer {api_token}"},
                files=fields,
                timeout=(20, 300),
            )
        except requests.RequestException as exc:
            if attempt >= TRANSIENT_RETRIES:
                raise RuntimeError(
                    f"Cloudflare Workers AI request failed after {TRANSIENT_RETRIES + 1} attempts: "
                    f"{type(exc).__name__}"
                ) from exc
            delay = RETRY_BASE_SECONDS * (2 ** attempt)
            print(f"Temporary Cloudflare network error ({type(exc).__name__}); retrying in {delay:.0f}s")
            time.sleep(delay)
            continue

        status = int(response.status_code)
        if status == 429 or status in {500, 502, 503, 504}:
            if attempt >= TRANSIENT_RETRIES:
                raise RuntimeError(
                    f"Cloudflare Workers AI transient HTTP {status} persisted after "
                    f"{TRANSIENT_RETRIES + 1} attempts: {response.text[:500]}"
                )
            retry_after = None
            try:
                retry_after = float(response.headers.get("Retry-After", ""))
            except (AttributeError, TypeError, ValueError):
                pass
            delay = max(1.0, min(retry_after, 60.0)) if retry_after is not None else RETRY_BASE_SECONDS * (2 ** attempt)
            print(f"Cloudflare Workers AI returned transient HTTP {status}; retrying in {delay:.0f}s")
            time.sleep(delay)
            continue
        if status >= 400:
            raise RuntimeError(f"Cloudflare Workers AI returned HTTP {status}: {response.text[:500]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Cloudflare Workers AI returned non-JSON response") from exc
        return _decode_image(payload)

    raise RuntimeError("Cloudflare Workers AI retry loop ended unexpectedly")


def _existing_scene_image(output_dir: Path, index: int, scene_id: str) -> tuple[Path, bytes, int, int, str] | None:
    """Find and validate a previously generated image for this exact scene."""
    stem = f"{index:02d}-{re.sub(r'[^a-z0-9]+', '-', scene_id.lower()).strip('-')}"
    for extension in (".jpg", ".png", ".webp", ".jpeg"):
        path = output_dir / (stem + extension)
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                image_format = image.format
            if width < 700 or height < 1200:
                raise ValueError(f"partial image too small: {width}x{height}")
            if abs((width / height) - (9 / 16)) > 0.035:
                raise ValueError(f"partial image is not portrait 9:16: {width}x{height}")
            if image_format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError(f"unsupported partial image format: {image_format}")
            return path, data, width, height, image_format
        except Exception as exc:
            print(f"Discarding invalid partial image {path.name}: {type(exc).__name__}: {exc}")
            path.unlink(missing_ok=True)
    return None


def run(
    plan_path: Path,
    output_dir: Path,
    account_id: str,
    api_token: str,
    reference_image_path: Path | None = None,
    scale_reference_path: Path | None = None,
    only_scene: str | None = None,
    pause_seconds: float = 1.0,
    resume: bool = False,
) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    generic_episode = plan.get("format") == "dark_fairytale_episode_v1"
    if generic_episode:
        plan = validate_episode_plan(plan)
    scenes = plan.get("scenes", [])
    if not scenes:
        raise ValueError("The story plan contains no scenes")
    if only_scene:
        scenes = [scene for scene in scenes if str(scene.get("id")) == only_scene]
        if not scenes:
            raise ValueError(f"Requested scene {only_scene!r} is not in the story plan")

    uses_rumpel = (not generic_episode) and any(str(scene.get("id")) in RUMPELSTILTSKIN_SCENES for scene in scenes)
    reference_bytes = None
    scale_reference_bytes = None
    if uses_rumpel:
        if reference_image_path is None or not reference_image_path.is_file():
            raise ValueError("Rumpelstiltskin scenes require a visual identity reference image")
        if scale_reference_path is None or not scale_reference_path.is_file():
            raise ValueError("Rumpelstiltskin scenes require a separate height-scale reference image")
        for ref_path, label in (
            (reference_image_path, "identity"),
            (scale_reference_path, "height-scale"),
        ):
            with Image.open(ref_path) as ref:
                ref.verify()
            with Image.open(ref_path) as ref:
                if ref.width >= 512 or ref.height >= 512:
                    raise ValueError(
                        f"FLUX.2 Klein {label} reference images must be strictly smaller than 512x512 pixels"
                    )
                ref_format = ref.format
            if ref_format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError(f"Unsupported {label} reference format: {ref_format}")
        reference_bytes = reference_image_path.read_bytes()
        scale_reference_bytes = scale_reference_path.read_bytes()

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "story": plan.get("title", plan.get("series", "Untitled story")),
        "story_id": plan.get("story_id"),
        "source": plan.get("source"),
        "disclosure": plan.get("disclosure"),
        "model_strategy": (
            {"all_scenes": MODEL_ID, "character_identity": "textual character bible in every scene prompt"}
            if generic_episode else
            {"character_scenes": RUMPELSTILTSKIN_MODEL_ID, "other_scenes": MODEL_ID}
        ),
        "width": OUTPUT_WIDTH,
        "height": OUTPUT_HEIGHT,
        "status": "generated_unapproved_review_only",
        "approved": False,
        "rendered": False,
        "published": False,
        "reference_image_sha256": hashlib.sha256(reference_bytes).hexdigest() if reference_bytes else None,
        "scale_reference_sha256": hashlib.sha256(scale_reference_bytes).hexdigest() if scale_reference_bytes else None,
        "images": [],
    }

    for index, scene in enumerate(scenes, 1):
        scene_id = str(scene["id"])
        prompt = build_episode_prompt(plan, scene, index) if generic_episode else build_prompt(scene, index)
        is_character_scene = (not generic_episode) and scene_id in RUMPELSTILTSKIN_SCENES
        scene_reference = reference_bytes if is_character_scene else None
        scene_scale_reference = scale_reference_bytes if is_character_scene else None
        preferred_model_id = RUMPELSTILTSKIN_MODEL_ID if is_character_scene else MODEL_ID
        scene_model_id = preferred_model_id
        model_fallback_used = False

        resumed_image = _existing_scene_image(output_dir, index, scene_id) if resume else None
        if resumed_image is not None:
            path, data, width, height, image_format = resumed_image
            resumed = True
            print(f"Reusing verified partial image {index}/{len(scenes)}: {path.name} ({width}x{height})")
        else:
            try:
                data = generate_one(
                    prompt,
                    account_id,
                    api_token,
                    reference_image=scene_reference,
                    scale_reference_image=scene_scale_reference,
                    model_id=scene_model_id,
                )
            except RuntimeError as exc:
                transient_failure = "transient HTTP" in str(exc) or "request failed after" in str(exc)
                if not (is_character_scene and scene_model_id == RUMPELSTILTSKIN_MODEL_ID and transient_failure):
                    raise
                # Keep the 9B model as the preferred character generator, but do
                # not let a temporary 9B outage block the whole short.
                print(
                    f"{scene_id}: Klein 9B remained unavailable after retries; "
                    "falling back to Klein 4B with both character references."
                )
                scene_model_id = MODEL_ID
                model_fallback_used = True
                data = generate_one(
                    prompt,
                    account_id,
                    api_token,
                    reference_image=scene_reference,
                    scale_reference_image=scene_scale_reference,
                    model_id=scene_model_id,
                )
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                image_format = image.format
            if width < 700 or height < 1200:
                raise RuntimeError(f"Generated image too small for {scene_id}: {width}x{height}")
            if abs((width / height) - (9 / 16)) > 0.035:
                raise RuntimeError(f"Generated image is not portrait 9:16 for {scene_id}: {width}x{height}")
            extension = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}.get(image_format)
            if extension is None:
                raise RuntimeError(f"Unsupported generated image format: {image_format}")
            filename = f"{index:02d}-{re.sub(r'[^a-z0-9]+', '-', scene_id.lower()).strip('-')}{extension}"
            path = output_dir / filename
            path.write_bytes(data)
            resumed = False

        manifest["images"].append({
            "scene_id": scene_id,
            "title": scene.get("title", scene_id),
            "file": path.name,
            "width": width,
            "height": height,
            "format": image_format,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "model": scene_model_id,
            "preferred_model": preferred_model_id,
            "model_fallback_used": model_fallback_used,
            "uses_character_reference": scene_reference is not None,
            "uses_scale_reference": scene_scale_reference is not None,
            "resumed_from_partial_run": resumed,
            "approved": False,
        })
        manifest["scene_count"] = len(manifest["images"])
        manifest_path = output_dir / "generation-manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        action = "Reused" if resumed else "Generated"
        print(f"{action} {index}/{len(scenes)}: {path.name} ({width}x{height})")
        if index < len(scenes) and pause_seconds > 0 and not resumed:
            time.sleep(pause_seconds)

    manifest["scene_count"] = len(manifest["images"])
    manifest_path = output_dir / "generation-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=Path("research/rumpelstiltskin-pilot.json"))
    parser.add_argument("--output", type=Path, default=Path("output/rumpelstiltskin-generated-scenes"))
    parser.add_argument("--reference-image", type=Path, default=None)
    parser.add_argument("--scale-reference-image", type=Path, default=None)
    parser.add_argument("--only-scene", type=str, default=None)
    parser.add_argument("--pause-seconds", type=float, default=1.0)
    parser.add_argument("--resume", action="store_true", help="reuse verified scene images already in the output directory")
    args = parser.parse_args()

    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not api_token:
        raise SystemExit("Missing Cloudflare Workers AI repository secrets.")

    manifest = run(
        args.plan,
        args.output,
        account_id,
        api_token,
        reference_image_path=args.reference_image,
        scale_reference_path=args.scale_reference_image,
        only_scene=args.only_scene,
        pause_seconds=args.pause_seconds,
        resume=args.resume,
    )
    print(f"Generated {manifest['scene_count']} unapproved scene images with {MODEL_ID}")
    print(f"Review directory: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
