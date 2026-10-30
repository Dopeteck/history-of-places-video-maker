#!/usr/bin/env python3
"""Daily orchestration entry point - connects all components for end-to-end video production."""
import json
import subprocess
import sys
import os
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "destination-prototype" / "data"
REGIONS_DIR = ROOT / "regions"
OUTPUT_DIR = ROOT / "output"

# Image-first pipeline modules (story emerges from material, not the reverse).
from image_inventory import discover_inventory, load_inventory
from story_from_inventory import (cluster_inventory, select_story,
                                  plan_scenes_from_story, generate_story_narration,
                                  save_story, load_story, StorySelection)


@dataclass
class RunPaths:
    """Paths for a single orchestration run.

    review_dir is deliberately named {region_slug}-region-short to match the
    artifact upload paths in .github/workflows/region-history-shorts.yml.
    """
    run_dir: Path
    topic_file: Path
    research_file: Path
    script_file: Path
    scene_plan_file: Path
    review_dir: Path
    video_file: Path
    qa_report_file: Path
    credits_file: Path
    image_manifest_file: Path

    @classmethod
    def create(cls, run_id: str, region_slug: str) -> "RunPaths":
        run_dir = DATA_DIR / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        review_dir = OUTPUT_DIR / f"{region_slug}-region-short"
        if review_dir.exists():
            shutil.rmtree(review_dir)
        return cls(
            run_dir=run_dir,
            topic_file=run_dir / "topic.json",
            research_file=run_dir / "research.json",
            script_file=run_dir / "script.json",
            scene_plan_file=run_dir / "scene_plan.json",
            review_dir=review_dir,
            video_file=review_dir / "final_video.mp4",
            qa_report_file=run_dir / "qa_report.json",
            credits_file=run_dir / "credits.json",
            image_manifest_file=run_dir / "image_manifest.json",
        )


@dataclass
class OrchestrationResult:
    stage: str
    success: bool
    message: str
    artifacts: Dict[str, Any]


def run_command(cmd: List[str], cwd: Path = ROOT, env: Optional[Dict] = None) -> subprocess.CompletedProcess:
    """Run a command and return result. Prints captured output on failure for diagnostics."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=full_env)
    if result.returncode != 0:
        cmd_str = " ".join(str(c) for c in cmd)
        print(f"COMMAND FAILED (exit={result.returncode}): {cmd_str}", file=sys.stderr)
        if result.stdout.strip():
            print(f"STDOUT:\n{result.stdout}", file=sys.stderr)
        if result.stderr.strip():
            print(f"STDERR:\n{result.stderr}", file=sys.stderr)
    return result


def select_topic(paths: RunPaths) -> Optional[Dict]:
    """Select next topic using daily engine."""
    result = run_command([
        sys.executable, "destination-prototype/daily_content_engine.py", "--select-topic", "--output", str(paths.topic_file)
    ])
    if result.returncode != 0:
        return None
    try:
        with open(paths.topic_file, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return None


def research_topic(topic: Dict, paths: RunPaths) -> Optional[Dict]:
    """Research topic and save record to run-specific file.

    Even if the research subprocess exits non-zero (e.g. QA gate failure),
    the research JSON is still written to args.output before the crash, so we
    attempt to read it and return it for diagnostics/upload.
    """
    with open(paths.topic_file, "w", encoding="utf-8") as f:
        json.dump(topic, f, ensure_ascii=False)

    result = run_command([
        sys.executable, "destination-prototype/research_factcheck.py", "--topic", str(paths.topic_file), "--output", str(paths.research_file)
    ])

    # Always try to read the research file: the subprocess writes it before
    # any QA/exit failure, so diagnostics are available even on non-zero exit.
    if paths.research_file.exists():
        with open(paths.research_file, encoding="utf-8") as f:
            research = json.load(f)
        # Copy research artifacts into the review dir so they are uploaded
        # regardless of whether the research subprocess succeeded.
        try:
            paths.review_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(paths.research_file, paths.review_dir / "research.json")
        except Exception:
            pass
        if result.returncode != 0:
            print(f"WARNING: research subprocess exited {result.returncode}; research JSON still available at {paths.research_file}")
        return research

    if result.returncode != 0:
        print(f"ERROR: research subprocess exited {result.returncode} and no research file was written")
    return None


def generate_script(topic: Dict, research: Dict, paths: RunPaths) -> Optional[Dict]:
    """Generate narration script."""
    with open(paths.topic_file, "w", encoding="utf-8") as f:
        json.dump(topic, f, ensure_ascii=False)

    result = run_command([
        sys.executable, "destination-prototype/generate_script.py", "--topic", str(paths.topic_file), "--output", str(paths.script_file)
    ])
    if result.returncode != 0:
        return None

    if not paths.script_file.exists():
        return None
    with open(paths.script_file, encoding="utf-8") as f:
        return json.load(f)


def validate_claims(script: Dict, research: Dict) -> Dict:
    """Validate claims and return mapping."""
    if not research:
        return {"status": "no_research", "claims_mapped": 0}

    claims = research.get("claims", [])
    total = len(claims)
    if total == 0:
        return {"status": "no_claims", "claims_mapped": 0, "claims_total": 0}

    verified = [c for c in claims if c.get("status") == "verified"]
    insufficient = [c for c in claims if c.get("status") == "insufficient_evidence"]
    disputed = [c for c in claims if c.get("status") == "disputed"]
    provider_failures = [c for c in claims if c.get("status") == "provider_failure"]

    # Extract source URLs from verified evidence
    mapping = {}
    for c in verified:
        sources = [e.get("source_url") for e in c.get("evidence", []) if e.get("source_url")]
        mapping[c["text"]] = sources

    provider_failure_list = [p for p in research.get("provider_failures", []) if p]

    return {
        "status": "ok" if verified else "no_verified_claims",
        "claims_mapped": len(verified),
        "claims_total": total,
        "claims_verified": len(verified),
        "claims_insufficient": len(insufficient),
        "claims_disputed": len(disputed),
        "claims_provider_failures": len(provider_failures),
        "provider_failures": provider_failure_list,
        "mapping": mapping,
        "verification_ratio": round(len(verified) / total, 2) if total else 0.0,
    }


def render_visual_review(region_slug: str, hook_variant: str, paths: RunPaths,
                         scenes_json: Optional[Path] = None,
                         script_json: Optional[Path] = None) -> Optional[Path]:
    """Render visual approval board, using story-driven scenes when provided."""
    if paths.review_dir.exists():
        shutil.rmtree(paths.review_dir)

    cmd = [
        sys.executable, "destination-prototype/render_region_short.py",
        "--config", f"regions/{region_slug}.yaml",
        "--hook-variant", hook_variant,
        "--visual-review-only",
        "--output", str(paths.review_dir),
    ]
    if scenes_json:
        cmd += ["--scenes-json", str(scenes_json)]
    if script_json:
        cmd += ["--script-json", str(script_json)]
    result = run_command(cmd)
    if result.returncode != 0:
        return None

    if paths.review_dir.exists():
        return paths.review_dir
    return None


def run_review_qa(paths: RunPaths, scene_count: Optional[int] = None) -> Dict:
    """Run review QA checks - exits non-zero on failure."""
    cmd = [
        sys.executable, "destination-prototype/qa_checks.py",
        "--script", str(paths.script_file),
        "--output-dir", str(paths.review_dir),
        "--mode", "review",
        "--fail-on-error"
    ]
    if scene_count is not None:
        cmd += ["--scene-count", str(scene_count)]
    result = run_command(cmd)
    return {
        "passed": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode
    }


def run_production_qa(paths: RunPaths, scene_count: Optional[int] = None) -> Dict:
    """Run production QA checks - exits non-zero on failure."""
    cmd = [
        sys.executable, "destination-prototype/qa_checks.py",
        "--script", str(paths.script_file),
        "--output-dir", str(paths.review_dir),
        "--video", str(paths.video_file),
        "--mode", "production",
        "--fail-on-error"
    ]
    if scene_count is not None:
        cmd += ["--scene-count", str(scene_count)]
    result = run_command(cmd)
    return {
        "passed": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode
    }


def render_final_video(region_slug: str, hook_variant: str, paths: RunPaths,
                       scenes_json: Optional[Path] = None,
                       script_json: Optional[Path] = None) -> Optional[Path]:
    """Render final narrated MP4, using story-driven scenes when provided."""
    if paths.review_dir.exists():
        shutil.rmtree(paths.review_dir)

    cmd = [
        sys.executable, "destination-prototype/render_region_short.py",
        "--config", f"regions/{region_slug}.yaml",
        "--hook-variant", hook_variant,
        "--output", str(paths.review_dir),
    ]
    if scenes_json:
        cmd += ["--scenes-json", str(scenes_json)]
    if script_json:
        cmd += ["--script-json", str(script_json)]
    result = run_command(cmd)
    if result.returncode != 0:
        return None

    video_files = list(paths.review_dir.glob("*.mp4"))
    if video_files:
        shutil.move(str(video_files[0]), str(paths.video_file))
        return paths.video_file
    return None


def save_artifacts(artifacts: Dict, paths: RunPaths):
    """Save all artifacts for the run."""
    for name, data in artifacts.items():
        if data is not None:
            filepath = paths.run_dir / f"{name}.json"
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Daily video production orchestration")
    ap.add_argument("--mode", choices=["review", "production"], default="review",
                    help="review=visual review only, production=full render after approval")
    ap.add_argument("--region", help="Specific region slug (overrides topic selection)")
    ap.add_argument("--hook-variant", default="A", help="Hook variant (A or B)")
    ap.add_argument("--approve", action="store_true", help="Mark as approved for production")
    ap.add_argument("--draft-preview", action="store_true", help="Render non-publishable draft MP4 (no approval gate)")
    args = ap.parse_args()

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Determine region slug early so RunPaths can use the correct output directory.
    if args.region:
        region_slug = args.region
    else:
        region_slug = None  # will be set after topic selection
    paths = None
    artifacts = {}

    print(f"=== DAILY ORCHESTRATION RUN: {run_id} ===")
    print(f"Mode: {args.mode}")

    # Stage 1: Select topic
    print("\n[1/8] Selecting topic...")
    if args.region:
        config_path = REGIONS_DIR / f"{args.region}.yaml"
        if not config_path.exists():
            print(f"ERROR: Region config not found: {config_path}")
            sys.exit(1)
        with open(config_path, encoding="utf-8") as f:
            import yaml
            cfg = yaml.safe_load(f)
        topic = {
            "destination_slug": args.region,
            "destination_name": cfg.get("name", args.region),
            "country": cfg.get("country", ""),
            "continent": cfg.get("continent", "Unknown"),
            "category": cfg.get("story", {}).get("category", "hidden_history"),
            "title": cfg.get("story", {}).get("hook", f"History of {args.region}"),
            "hook": cfg.get("story", {}).get("hook_variants", {}).get(args.hook_variant, ""),
            "description": cfg.get("story", {}).get("narration", ""),
            "sources": cfg.get("sources", []),
            "kokoro_voice": cfg.get("story", {}).get("kokoro_voice", "af_heart"),
            "narration_language": cfg.get("story", {}).get("narration_language", "English"),
            # Extract named entities from place_names spellings for scene planning.
            "entities": [p.get("spelling", "") for p in cfg.get("place_names", []) if p.get("spelling")],
        }
    else:
        topic = select_topic(paths)
        if not topic:
            print("ERROR: No topic available")
            sys.exit(1)

    print(f"Selected: {topic['destination_name']} ({topic['category']}) - {topic['title']}")
    artifacts["topic"] = topic

    # Initialize run paths now that we know the region slug.
    if paths is None:
        region_slug = topic["destination_slug"]
    paths = RunPaths.create(run_id, region_slug)

    # Stage 2: Discover region-first visual inventory (before any scene is authored).
    print("\n[2/8] Discovering region-first visual inventory...")
    config_path = REGIONS_DIR / f"{topic['destination_slug']}.yaml"
    if not config_path.exists():
        print(f"ERROR: Region config not found: {config_path}")
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        import yaml
        dest_cfg = yaml.safe_load(f)
    # Reuse a cached inventory when present; otherwise discover fresh.
    inventory = load_inventory(topic["destination_slug"])
    if inventory is None:
        inventory = discover_inventory(dest_cfg)
    print(f"  Inventory: {inventory.total_discovered} eligible assets, "
          f"{len([c for c in inventory.coverage if not c.skipped])} providers attempted")
    artifacts["inventory"] = {
        "region": inventory.region_slug,
        "total_discovered": inventory.total_discovered,
        "total_eligible": inventory.total_eligible,
        "coverage": [asdict(c) for c in inventory.coverage],
    }
    if inventory.total_eligible == 0:
        print("ERROR: No eligible assets discovered; cannot build a story from an empty inventory.")
        print("  Coverage:", [(c.provider, c.results_returned, c.skipped, c.skip_reason, len(c.errors))
                          for c in inventory.coverage])
        sys.exit(1)

    # Stage 3: Select a story from the inventory (story emerges from material).
    print("\n[3/8] Selecting story from inventory...")
    story_selection = load_story(topic["destination_slug"])
    if story_selection is None:
        clusters = cluster_inventory(inventory)
        story = select_story(clusters)
        if story is None:
            print("ERROR: No supported story could be selected from the inventory.")
            sys.exit(1)
        if story.rejection_reason:
            print(f"WARNING: {story.rejection_reason}")
        scenes = plan_scenes_from_story(story, inventory)
        if not scenes:
            print("ERROR: Could not plan scenes from selected assets.")
            sys.exit(1)
        narration = generate_story_narration(story, scenes)
        story_selection = StorySelection(
            region_slug=topic["destination_slug"],
            region_name=topic["destination_name"],
            selected_theme=story.selected_theme,
            story_title=story.title,
            story_summary=story.summary,
            story_assets=story.assets,
            candidates=story.candidates if hasattr(story, 'candidates') else [],
            claims=[],
            claim_support={},
        )
        save_story(topic["destination_slug"], story_selection)
    else:
        story = None  # loaded from cache; scenes rebuilt below
        narration = generate_story_narration(story_selection, plan_scenes_from_story(
            type("SC", (), {"theme": story_selection.selected_theme, "assets": story_selection.story_assets})(),
            inventory))
    print(f"  Selected: {story_selection.story_title} ({story_selection.selected_theme}) — "
          f"{len(story_selection.story_assets)} assets")
    artifacts["story"] = story_selection.to_dict()

    # Stage 4: Plan scenes from selected assets (each scene references a pinned asset).
    print("\n[4/8] Planning scenes from selected assets...")
    if story is not None:
        scenes = plan_scenes_from_story(story, inventory)
    else:
        scenes = plan_scenes_from_story(
            type("SC", (), {"theme": story_selection.selected_theme,
                            "assets": story_selection.story_assets})(),
            inventory)
    if not scenes:
        print("ERROR: Could not plan scenes from selected assets.")
        sys.exit(1)
    scene_count = len(scenes)
    print(f"  Planned scenes: {scene_count}")
    for s in scenes:
        print(f"  scene {s.get('scene_order')}: {s.get('title')} ({s.get('image_type')}) "
              f"pinned={bool(s.get('pinned_image'))}")
    with open(paths.scene_plan_file, "w", encoding="utf-8") as f:
        json.dump(scenes, f, indent=2, ensure_ascii=False)
    artifacts["scene_plan"] = scenes

    # Stage 5: Research claims suggested by the selected story.
    print("\n[5/8] Researching claims from the selected story...")
    topic_for_research = dict(topic)
    topic_for_research["description"] = narration
    topic_for_research["title"] = story_selection.story_title
    research = research_topic(topic_for_research, paths)
    if not research:
        print("ERROR: Research failed")
        sys.exit(1)
    print(f"  Extracted {len(research.get('claims', []))} claims")
    artifacts["research"] = research

    # Stage 6: Generate narration script from the story (not from a prewritten config story).
    print("\n[6/8] Generating narration script from story...")
    topic_for_script = dict(topic_for_research)
    script = generate_script(topic_for_script, research, paths)
    if not script:
        print("ERROR: Script generation failed")
        sys.exit(1)
    print(f"  Words: {script.get('word_count', 0)}, Est. duration: {script.get('estimated_duration', 0)}s")
    artifacts["script"] = script

    # Stage 5: Validate claims
    print("\n[5/8] Validating claims...")
    claim_validation = validate_claims(script, research)
    print(f"  Verified claims: {claim_validation['claims_mapped']}/{claim_validation['claims_total']}")
    artifacts["claim_validation"] = claim_validation

    # Stage 6: Visual review
    print("\n[6/8] Rendering visual review board...")
    review_dir = render_visual_review(topic["destination_slug"], args.hook_variant, paths,
                                      scenes_json=paths.scene_plan_file,
                                      script_json=paths.script_file)
    if not review_dir:
        print("ERROR: Visual review rendering failed")
        sys.exit(1)
    print(f"  Review artifacts: {review_dir}")
    artifacts["review_dir"] = str(review_dir)

    # QA for review artifacts - MANDATORY, exits non-zero on failure
    print("\n[7/8] Running QA on review artifacts...")
    qa_result = run_review_qa(paths, scene_count=scene_count)
    print(f"  QA passed: {qa_result['passed']}")
    artifacts["qa_review"] = qa_result
    if not qa_result['passed']:
        print(f"  QA FAILED: {qa_result['stderr']}")
        # Save artifacts so the review board and diagnostics remain available
        # for diagnosis even when the research gate fails.
        save_artifacts(artifacts, paths)
        sys.exit(1)

    if args.mode == "review":
        if args.draft_preview:
            # Draft preview: render non-publishable MP4 without approval gate
            print("\n[9/9] Rendering draft preview MP4 (no approval gate)...")
            video_path = render_final_video(topic["destination_slug"], args.hook_variant, paths,
                                            scenes_json=paths.scene_plan_file,
                                            script_json=paths.script_file)
            if not video_path:
                print("ERROR: Draft preview rendering failed")
                sys.exit(1)
            print(f"  Draft video: {video_path}")
            artifacts["video_path"] = str(video_path)
            print("\n=== DRAFT PREVIEW COMPLETE ===")
        else:
            print("\n=== REVIEW MODE COMPLETE ===")
            print("Visual review artifacts ready for human approval.")
            print(f"Approval board: {paths.review_dir}/approval-board.html")
        save_artifacts(artifacts, paths)
        return

    # Stage 9: Production (requires explicit approval)
    if not args.approve:
        print("\nERROR: Production mode requires --approve flag (human approval)")
        print("Run with --mode review first, then --mode production --approve after review")
        sys.exit(1)

    print("\n[10/8] Rendering final narrated video...")
    video_path = render_final_video(topic["destination_slug"], args.hook_variant, paths,
                                    scenes_json=paths.scene_plan_file,
                                    script_json=paths.script_file)
    if not video_path:
        print("ERROR: Final video rendering failed")
        sys.exit(1)
    print(f"  Video: {video_path}")
    artifacts["video_path"] = str(video_path)

    # Stage 11: Final QA - MANDATORY, exits non-zero on failure
    print("\n[11/8] Running final video QA...")
    prod_qa = run_production_qa(paths, scene_count=scene_count)
    print(f"  Production QA passed: {prod_qa['passed']}")
    if not prod_qa['passed']:
        print(f"  PRODUCTION QA FAILED: {prod_qa['stderr']}")
        sys.exit(1)
    artifacts["qa_production"] = prod_qa

    print("\n=== PRODUCTION COMPLETE ===")
    print(f"Video: {video_path}")
    print(f"Run ID: {run_id}")

    save_artifacts(artifacts, paths)


if __name__ == "__main__":
    main()