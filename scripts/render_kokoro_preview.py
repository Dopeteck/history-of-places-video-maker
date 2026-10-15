"""Generate a technical Kokoro narration preview; this is not publish-ready content."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scene_cards_render import render_scene_cards
from scene_planner import make_review_template
from story import digest
from story_store import write_json


def fixture_plan() -> dict:
    source = {
        "id": "technical_preview",
        "title": "Technical preview fixture",
        "url": "https://example.org/technical-preview",
        "excerpt": "Synthetic fixture used only to validate the audio/video pipeline.",
    }
    lines = [
        ("A quiet opening", "This is a technical narration preview. The words and artwork are only a test."),
        ("A simple idea", "A clear voice should sound natural, steady, and easy to understand."),
        ("Illustrated scene", "The scene changes while the narration continues. Captions should follow the spoken passage."),
        ("A short pause", "A little space between ideas can make a video feel calmer and easier to follow."),
        ("Caption check", "Watch the words on screen. These timings are estimates, not exact speech alignment."),
        ("Preview complete", "This preview checks the real voice model and video render. It is not a finished educational video."),
    ]
    scenes = []
    kinds = ["illustration", "steps", "comparison", "timeline", "illustration", "verdict"]
    for index, (title, narration) in enumerate(lines, start=1):
        scenes.append({
            "id": f"scene_{index:02d}",
            "kind": kinds[index - 1],
            "title": title,
            "narration": narration,
            "visual_prompt": f"Friendly minimal stickman editorial illustration for a technical preview, scene {index}, clean bold outlines, limited warm palette, no text or letters.",
            "background": "ivory",
            "source_ids": ["technical_preview"],
            "labels": [],
            "claims": [],
            "uncertainty": "synthetic_test",
            "transition": "crossfade" if index < len(lines) else "cut",
        })
    return {
        "schema_version": 1,
        "title": "Kokoro Voice and Caption Test",
        "hook": "REAL VOICE • CAPTION TIMING • VIDEO RENDER TEST",
        "topic": "technical preview fixture only",
        "scenes": scenes,
        "source_ids": ["technical_preview"],
        "sources": [source],
        "status": "needs_human_review",
        "review": {"status": "needs_human_review", "warning": "Technical test fixture only.", "claims": []},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("output/kokoro-technical-preview"))
    parser.add_argument("--voice", choices=["bm_george", "bm_fable", "am_michael"], default="bm_george")
    parser.add_argument("--speed", type=float, default=1.0)
    args = parser.parse_args()

    output = args.out.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error(f"Output directory must be new or empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    plan = fixture_plan()
    review = make_review_template(plan)
    review.update({
        "reviewer": "Automated technical fixture",
        "full_script_reviewed": True,
        "sources_checked": ["technical_preview"],
    })
    write_json(output / "technical-preview-plan.json", plan)
    write_json(output / "technical-preview-review.json", review)
    video = render_scene_cards(
        plan, review, output / "render", mode="shorts", preview=True,
        captions=True, smoke=False, voice_name=args.voice, speed=args.speed,
    )
    metadata_path = output / "render" / "render-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["technical_preview_only"] = True
    metadata["plan_sha256_fixture"] = digest(plan)
    write_json(metadata_path, metadata)
    (output / "README.txt").write_text(
        "TECHNICAL PREVIEW ONLY — not approved for publishing.\n"
        "The script is synthetic test copy, artwork is procedural unless supplied, "
        "and word-level caption timing is estimated from each measured narration duration.\n"
        f"Voice: {args.voice}; speed: {args.speed}.\n"
        f"Video: {video.name}\n",
        encoding="utf-8",
    )
    print(f"Preview created: {video}")
    print(f"Metadata: {metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
