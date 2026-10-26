#!/usr/bin/env python3
"""Daily orchestration entry point - connects all components for end-to-end video production."""
import json
import subprocess
import sys
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "destination-prototype" / "data"
REGIONS_DIR = ROOT / "regions"
OUTPUT_DIR = ROOT / "output"


@dataclass
class OrchestrationResult:
    stage: str
    success: bool
    message: str
    artifacts: Dict[str, Any]


def run_command(cmd: List[str], cwd: Path = ROOT, env: Optional[Dict] = None) -> subprocess.CompletedProcess:
    """Run a command and return result."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=full_env)


def select_topic(run_id: str) -> Optional[Dict]:
    """Select next topic using daily engine."""
    topic_file = DATA_DIR / f"topic_{run_id}.json"
    result = run_command([
        sys.executable, "destination-prototype/daily_content_engine.py", "--select-topic"
    ])
    if result.returncode != 0:
        return None
    try:
        topic = json.loads(result.stdout.strip())
        # Save topic for this run
        with open(topic_file, "w", encoding="utf-8") as f:
            json.dump(topic, f, ensure_ascii=False)
        return topic
    except json.JSONDecodeError:
        return None


def research_topic(topic: Dict, run_id: str) -> Optional[Dict]:
    """Research topic and save record to run-specific file."""
    topic_file = DATA_DIR / f"topic_{run_id}.json"
    with open(topic_file, "w", encoding="utf-8") as f:
        json.dump(topic, f, ensure_ascii=False)

    result = run_command([
        sys.executable, "destination-prototype/research_factcheck.py", "--topic", str(topic_file)
    ])
    if result.returncode != 0:
        return None

    # Research file is saved with date-based name: research_{topic_slug}_{date}.json
    today = datetime.now().date().isoformat()
    research_files = list(DATA_DIR.glob(f"research_{topic['destination_slug']}_{today}.json"))
    if not research_files:
        return None
    latest = max(research_files)
    with open(latest, encoding="utf-8") as f:
        return json.load(f)


def generate_script(topic: Dict, research: Dict, run_id: str) -> Optional[Dict]:
    """Generate narration script."""
    topic_file = DATA_DIR / f"topic_{run_id}.json"
    with open(topic_file, "w", encoding="utf-8") as f:
        json.dump(topic, f, ensure_ascii=False)

    result = run_command([
        sys.executable, "destination-prototype/generate_script.py", "--topic", str(topic_file)
    ])
    if result.returncode != 0:
        return None

    today = datetime.now().date().isoformat()
    script_files = list(DATA_DIR.glob(f"script_{topic['destination_slug']}_{today}.json"))
    if not script_files:
        return None
    latest = max(script_files)
    with open(latest, encoding="utf-8") as f:
        return json.load(f)


def validate_claims(script: Dict, research: Dict) -> Dict:
    """Validate claims and return mapping."""
    if not research:
        return {"status": "no_research", "claims_mapped": 0}

    verified = [c for c in research.get("claims", []) if c.get("status") == "verified"]
    total = len(research.get("claims", []))

    # Extract source URLs from evidence
    mapping = {}
    for c in verified:
        sources = [e.get("source_url") for e in c.get("evidence", []) if e.get("source_url")]
        mapping[c["text"]] = sources

    return {
        "status": "ok" if verified else "no_verified_claims",
        "claims_mapped": len(verified),
        "claims_total": total,
        "mapping": mapping
    }


def render_visual_review(region_slug: str, hook_variant: str = "A", run_id: str = "") -> Optional[Path]:
    """Render visual approval board."""
    output_path = OUTPUT_DIR / f"{region_slug}-region-short"
    # Clean previous output for this region
    if output_path.exists():
        import shutil
        shutil.rmtree(output_path)

    result = run_command([
        sys.executable, "destination-prototype/render_region_short.py",
        "--config", f"regions/{region_slug}.yaml",
        "--hook-variant", hook_variant,
        "--visual-review-only"
    ])
    if result.returncode != 0:
        return None

    if output_path.exists():
        return output_path
    return None


def run_review_qa(script_path: Path, output_dir: Path) -> Dict:
    """Run review QA checks - exits non-zero on failure."""
    cmd = [
        sys.executable, "destination-prototype/qa_checks.py",
        "--script", str(script_path),
        "--output-dir", str(output_dir),
        "--mode", "review",
        "--fail-on-error"
    ]
    result = run_command(cmd)
    return {
        "passed": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode
    }


def run_production_qa(script_path: Path, output_dir: Path, video_path: Path) -> Dict:
    """Run production QA checks - exits non-zero on failure."""
    cmd = [
        sys.executable, "destination-prototype/qa_checks.py",
        "--script", str(script_path),
        "--output-dir", str(output_dir),
        "--video", str(video_path),
        "--mode", "production",
        "--fail-on-error"
    ]
    result = run_command(cmd)
    return {
        "passed": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode
    }


def render_final_video(region_slug: str, hook_variant: str = "A") -> Optional[Path]:
    """Render final narrated MP4."""
    output_path = OUTPUT_DIR / f"{region_slug}-region-short"
    if output_path.exists():
        import shutil
        shutil.rmtree(output_path)

    result = run_command([
        sys.executable, "destination-prototype/render_region_short.py",
        "--config", f"regions/{region_slug}.yaml",
        "--hook-variant", hook_variant
    ])
    if result.returncode != 0:
        return None

    video_files = list(output_path.glob("*.mp4"))
    return video_files[0] if video_files else None


def save_artifacts(artifacts: Dict, run_id: str):
    """Save all artifacts for the run."""
    run_dir = DATA_DIR / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    for name, data in artifacts.items():
        if data is not None:
            filepath = run_dir / f"{name}.json"
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
    args = ap.parse_args()

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    artifacts = {}
    script_path = None
    review_dir = None
    video_path = None

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
        }
    else:
        topic = select_topic(run_id)
        if not topic:
            print("ERROR: No topic available")
            sys.exit(1)

    print(f"Selected: {topic['destination_name']} ({topic['category']}) - {topic['title']}")
    artifacts["topic"] = topic

    # Stage 2: Research
    print("\n[2/8] Researching and fact-checking...")
    research = research_topic(topic, run_id)
    if not research:
        print("ERROR: Research failed")
        sys.exit(1)
    print(f"  Extracted {len(research.get('claims', []))} claims")
    artifacts["research"] = research

    # Stage 3: Generate script
    print("\n[3/8] Generating narration script...")
    script = generate_script(topic, research, run_id)
    if not script:
        print("ERROR: Script generation failed")
        sys.exit(1)
    print(f"  Words: {script.get('word_count', 0)}, Est. duration: {script.get('estimated_duration', 0)}s")
    artifacts["script"] = script
    script_path = DATA_DIR / f"script_{run_id}.json"

    # Stage 4: Validate claims
    print("\n[4/8] Validating claims...")
    claim_validation = validate_claims(script, research)
    print(f"  Verified claims: {claim_validation['claims_mapped']}/{claim_validation['claims_total']}")
    artifacts["claim_validation"] = claim_validation

    # Stage 5: Visual review
    print("\n[5/8] Rendering visual review board...")
    review_dir = render_visual_review(topic["destination_slug"], args.hook_variant, run_id)
    if not review_dir:
        print("ERROR: Visual review rendering failed")
        sys.exit(1)
    print(f"  Review artifacts: {review_dir}")
    artifacts["review_dir"] = str(review_dir)

    # QA for review artifacts - MANDATORY, exits non-zero on failure
    print("\n[6/8] Running QA on review artifacts...")
    qa_result = run_review_qa(script_path, review_dir)
    print(f"  QA passed: {qa_result['passed']}")
    if not qa_result['passed']:
        print(f"  QA FAILED: {qa_result['stderr']}")
        sys.exit(1)
    artifacts["qa_review"] = qa_result

    if args.mode == "review":
        print("\n=== REVIEW MODE COMPLETE ===")
        print("Visual review artifacts ready for human approval.")
        print(f"Approval board: {review_dir}/approval-board.html")
        save_artifacts(artifacts, run_id)
        return

    # Stage 7: Production (requires explicit approval)
    if not args.approve:
        print("\nERROR: Production mode requires --approve flag (human approval)")
        print("Run with --mode review first, then --mode production --approve after review")
        sys.exit(1)

    print("\n[7/8] Rendering final narrated video...")
    video_path = render_final_video(topic["destination_slug"], args.hook_variant)
    if not video_path:
        print("ERROR: Final video rendering failed")
        sys.exit(1)
    print(f"  Video: {video_path}")
    artifacts["video_path"] = str(video_path)

    # Stage 8: Final QA - MANDATORY, exits non-zero on failure
    print("\n[8/8] Running final video QA...")
    prod_qa = run_production_qa(script_path, review_dir, video_path)
    print(f"  Production QA passed: {prod_qa['passed']}")
    if not prod_qa['passed']:
        print(f"  PRODUCTION QA FAILED: {prod_qa['stderr']}")
        sys.exit(1)
    artifacts["qa_production"] = prod_qa

    print("\n=== PRODUCTION COMPLETE ===")
    print(f"Video: {video_path}")
    print(f"Run ID: {run_id}")

    save_artifacts(artifacts, run_id)


if __name__ == "__main__":
    main()