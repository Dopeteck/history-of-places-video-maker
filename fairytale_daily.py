#!/usr/bin/env python3
"""Run one complete private daily dark-fairytale episode.

A queue item is marked complete only after all eight images, the MP4, captions,
metadata, and review disclosure pass validation. No approval or publishing occurs.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from datetime import date, datetime, timezone

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "destination-prototype"))

from fairytale_planner import (
    generate_episode, initial_state, load_queue, next_story, record_success,
)
from llm import JsonLLM
from story_store import write_json
from generate_fairytale_scenes import run as generate_scenes
from render_fairytale_video import render


def _read_state(path: Path) -> dict:
    if not path.exists():
        return initial_state()
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1:
        raise ValueError("Unsupported fairytale rotation state")
    return value


def run(queue_path: Path, state_path: Path, output_root: Path,
        voice: str = "bm_fable", speed: float = 0.92,
        env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    account_id = env.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = env.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not api_token:
        raise RuntimeError("Missing Cloudflare Workers AI secrets; no images were generated.")
    if not any(env.get(key, "").strip() for key in (
        "GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"
    )):
        raise RuntimeError("Missing a script provider secret (GROQ_API_KEY, GEMINI_API_KEY, or OPENROUTER_API_KEY).")

    queue = load_queue(queue_path)
    state = _read_state(state_path)
    selected, pending_state = next_story(queue, state)

    episode_date = env.get("EPISODE_DATE", date.today().isoformat())
    run_id = env.get("GITHUB_RUN_ID", "manual")
    run_attempt = env.get("GITHUB_RUN_ATTEMPT", "1")
    episode_key = f"{episode_date}-{selected['id']}-run-{run_id}-{run_attempt}"
    episode_dir = output_root / episode_key
    if episode_dir.exists() and any(episode_dir.iterdir()):
        raise RuntimeError(f"Episode output already exists: {episode_dir}; use a fresh workflow run.")
    episode_dir.mkdir(parents=True, exist_ok=True)
    image_dir = episode_dir / "scenes"
    review_dir = episode_dir / "review"

    llm = JsonLLM(
        cache_dir=ROOT / "work" / "fairytale-plan-cache",
        draft_cache_dir=ROOT / "work" / "fairytale-plan-progress",
    )
    plan = generate_episode(selected, llm)
    plan_path = episode_dir / "episode-plan.json"
    write_json(plan_path, plan)

    print(f"Selected tale: {selected['title']} ({selected['id']})")
    print(f"Source edition: {selected['source']['title']} — {selected['source']['url']}")
    print(f"Validated narration: {plan['narration_word_count']} words in {len(plan['scenes'])} scenes")

    manifest = generate_scenes(
        plan_path=plan_path,
        output_dir=image_dir,
        account_id=account_id,
        api_token=api_token,
        pause_seconds=1.0,
        resume=False,
    )
    if manifest.get("scene_count") != 8 or len(manifest.get("images", [])) != 8:
        raise ValueError("Image generation did not produce exactly eight scene images.")
    if manifest.get("approved") is not False or manifest.get("published") is not False:
        raise ValueError("Generated image manifest must remain unapproved and unpublished.")

    video_path, metadata = render(
        plan_path=plan_path,
        images_dir=image_dir,
        output_dir=review_dir,
        voice=voice,
        speed=speed,
    )
    if not video_path.is_file() or video_path.stat().st_size < 100_000:
        raise ValueError("Rendered MP4 is missing or unexpectedly small.")
    if metadata.get("scene_count") != 8:
        raise ValueError("Rendered metadata does not contain all eight scenes.")
    if metadata.get("chapter_overlays") is not False:
        raise ValueError("Chapter banners must remain disabled.")
    if metadata.get("opening") != "full_bleed_scene_one_no_title_overlay":
        raise ValueError("The video must open on the first full-bleed scene without a title card")
    if metadata.get("narration_word_count") != plan["narration_word_count"]:
        raise ValueError("Rendered narration word count differs from the validated story plan.")
    if metadata.get("approved") is not False or metadata.get("published") is not False:
        raise ValueError("Rendered preview must remain unapproved and unpublished.")
    if not 75 <= float(metadata.get("duration_seconds", 0)) <= 115:
        raise ValueError("Rendered runtime is outside the 75–115 second review range.")

    captions_path = review_dir / "captions.srt"
    captions = captions_path.read_text(encoding="utf-8")
    if not captions.strip() or "\\n" in captions:
        raise ValueError("SRT captions are missing or contain literal escaped newline text.")
    if not (review_dir / "contact-sheet.jpg").is_file():
        raise ValueError("Missing review contact sheet.")

    summary = {
        "status": "private_review_ready",
        "story_id": selected["id"],
        "title": plan["title"],
        "episode_date": episode_date,
        "source": plan["source"],
        "video": str(video_path.relative_to(ROOT)),
        "contact_sheet": str((review_dir / "contact-sheet.jpg").relative_to(ROOT)),
        "captions": str(captions_path.relative_to(ROOT)),
        "duration_seconds": metadata["duration_seconds"],
        "resolution": metadata["resolution"],
        "fps": metadata["fps"],
        "narration_word_count": plan["narration_word_count"],
        "image_count": manifest["scene_count"],
        "voice": metadata["voice"],
        "chapter_overlays": False,
        "preview_only": True,
        "approved": False,
        "published": False,
        "disclosure": plan["disclosure"],
    }
    write_json(episode_dir / "review-summary.json", summary)

    completed_state = record_success(pending_state, selected, episode_date, str(run_id))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(state_path, completed_state)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=ROOT / "fairytale_queue.json")
    parser.add_argument("--state", type=Path, default=ROOT / "state" / "fairytale_rotation.json")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "daily-fairytales")
    parser.add_argument("--voice", choices=("bm_fable", "bm_george", "am_michael"), default="bm_fable")
    parser.add_argument("--speed", type=float, default=0.92)
    args = parser.parse_args()
    run(args.queue, args.state, args.output, args.voice, args.speed)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        print("Daily fairytale failed safely: " + message, file=sys.stderr)
        raise SystemExit(1)
