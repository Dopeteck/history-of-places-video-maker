"""Standalone Stage 2 scene-card planner; stops at a human-review package."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from llm import JsonLLM
from scene_assets import ImageProviderChain
from scene_planner import make_review_template, plan_scene_cards
from story_store import write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", required=True, help="Topic to storyboard")
    parser.add_argument("--sources", required=True, type=Path,
                        help="JSON list of source records: id, title, url, excerpt")
    parser.add_argument("--out", type=Path, default=Path("output/scene-card-draft"),
                        help="New or existing output directory")
    parser.add_argument("--min-scenes", type=int, default=6)
    parser.add_argument("--max-scenes", type=int, default=10)
    parser.add_argument("--style", default="warm editorial 2D illustration")
    parser.add_argument("--generate-images", action="store_true",
                        help="Generate/cached images; remote providers may incur charges")
    parser.add_argument("--cache-dir", type=Path, default=Path("work/scene-image-cache"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not args.sources.is_file():
        parser.error(f"Source file not found: {args.sources}")
    try:
        sources = json.loads(args.sources.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"Could not read source JSON: {type(exc).__name__}")
    if args.out.exists() and any(args.out.iterdir()):
        parser.error(f"Output directory is not empty: {args.out}")
    args.out.mkdir(parents=True, exist_ok=True)

    plan = plan_scene_cards(JsonLLM(cache_dir=Path("work/scene-plan-cache")),
                            args.topic, sources, args.min_scenes, args.max_scenes, args.style)
    review = make_review_template(plan)
    write_json(args.out / "scene_plan.json", plan)
    write_json(args.out / "review.template.json", review)
    (args.out / "scene_plan.md").write_text(_markdown(plan), encoding="utf-8")

    if args.generate_images:
        chain = ImageProviderChain(args.cache_dir)
        metadata = []
        assets_dir = args.out / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        for scene in plan["scenes"]:
            image, info = chain.generate(scene["visual_prompt"], background=scene["background"])
            filename = f'{scene["id"]}.png'
            image.save(assets_dir / filename, format="PNG", optimize=True)
            metadata.append({"scene_id": scene["id"], "path": f"assets/{filename}", **info})
        write_json(args.out / "assets.json", metadata)

    print(f"Draft saved to {args.out}")
    print("Status: needs_human_review. No approval or video render was performed.")
    return 0


def _markdown(plan: dict) -> str:
    lines = [f'# {plan["title"]}', "", f'**Persistent hook:** {plan["hook"]}', "",
             f'Topic: {plan["topic"]}', "", "## Scenes", ""]
    for scene in plan["scenes"]:
        lines.extend([f'### {scene["id"]}: {scene["title"]} ({scene["kind"]})', "",
                      scene["narration"], "",
                      f'Visual prompt: {scene["visual_prompt"]}', "",
                      f'Sources: {", ".join(scene["source_ids"])}',
                      f'Uncertainty: {scene["uncertainty"]}', ""])
        for claim in scene["claims"]:
            lines.append(f'- Claim to verify: {claim["claim"]}')
    lines.extend(["", "## Review warning", "",
                  "LLM output and image generation are not verification. Read the complete narration and check original sources."])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
