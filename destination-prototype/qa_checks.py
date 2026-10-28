#!/usr/bin/env python3
"""Quality assurance checks for generated shorts.

Mandatory production gate with ffprobe validation.
"""
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "destination-prototype" / "data"


@dataclass
class QAResult:
    check: str
    passed: bool
    details: str
    severity: str  # "error", "warning", "info"


def check_script_word_count(script_path: Path) -> QAResult:
    """Verify script word count is in range."""
    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)
    words = script.get("word_count", 0)
    if 120 <= words <= 150:
        return QAResult("script_word_count", True, f"{words} words (target 120-150)", "info")
    elif 100 <= words <= 180:
        return QAResult("script_word_count", True, f"{words} words (acceptable range 100-180)", "warning")
    else:
        return QAResult("script_word_count", False, f"{words} words (outside acceptable range)", "error")


def check_estimated_duration(script_path: Path) -> QAResult:
    """Verify estimated duration is in range."""
    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)
    duration = script.get("estimated_duration", 0)
    if 45 <= duration <= 60:
        return QAResult("estimated_duration", True, f"{duration}s (target 45-60s)", "info")
    elif 40 <= duration <= 65:
        return QAResult("estimated_duration", True, f"{duration}s (acceptable range 40-65s)", "warning")
    else:
        return QAResult("estimated_duration", False, f"{duration}s (outside acceptable range)", "error")


def check_claims_support(script_path: Path) -> QAResult:
    """Verify minimum claim support - requires >=70% verified."""
    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)
    verified = script.get("claims_verified", 0)
    total = script.get("claims_total", 0)
    if total == 0:
        return QAResult("claims_support", False, "No claims extracted - research failed", "error")
    ratio = verified / total
    if ratio >= 0.7:
        return QAResult("claims_support", True, f"{verified}/{total} claims verified ({ratio:.0%})", "info")
    elif ratio >= 0.4:
        return QAResult("claims_support", False, f"Only {verified}/{total} claims verified ({ratio:.0%}) - below 70% threshold", "error")
    else:
        return QAResult("claims_support", False, f"Only {verified}/{total} claims verified ({ratio:.0%}) - critical failure", "error")


def check_claims_no_disputed(script_path: Path) -> QAResult:
    """Verify no disputed claims in production script."""
    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)
    disputed = script.get("claims_disputed", 0)
    if disputed == 0:
        return QAResult("claims_no_disputed", True, "No disputed claims", "info")
    else:
        return QAResult("claims_no_disputed", False, f"{disputed} disputed claims present - must be resolved before production", "error")


def check_research_record(script_path: Path) -> QAResult:
    """Verify research record exists and has no critical failures."""
    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)
    research_path = script.get("research_record", "")
    if not research_path:
        return QAResult("research_record", False, "No research record linked", "error")

    if not Path(research_path).exists():
        return QAResult("research_record", False, f"Research record not found: {research_path}", "error")

    with open(research_path, encoding="utf-8") as f:
        research = json.load(f)

    if research.get("extraction_failed", False):
        return QAResult("research_record", False, "Claim extraction failed", "error")
    if research.get("verification_failed", False):
        return QAResult("research_record", False, "All claims disputed - verification failed", "error")
    if research.get("zero_claims", False):
        return QAResult("research_record", False, "Zero claims extracted", "error")

    return QAResult("research_record", True, "Research record valid", "info")


def check_video_output(video_path: Path) -> QAResult:
    """Verify video file exists and has valid properties."""
    if not video_path.exists():
        return QAResult("video_exists", False, "Video file not found", "error")
    if video_path.stat().st_size < 10000:
        return QAResult("video_size", False, f"Video too small: {video_path.stat().st_size} bytes", "error")
    return QAResult("video_exists", True, f"Video exists ({video_path.stat().st_size} bytes)", "info")


def check_approval_board(board_path: Path) -> QAResult:
    """Verify approval board exists and has content."""
    if not board_path.exists():
        return QAResult("approval_board_exists", False, "Approval board not found", "error")
    with open(board_path, encoding="utf-8") as f:
        content = f.read()
    if len(content) < 1000:
        return QAResult("approval_board_content", False, "Approval board too small", "warning")
    return QAResult("approval_board_exists", True, "Approval board found", "info")


def check_credits(credits_path: Path) -> QAResult:
    """Verify credits file exists and has proper attribution."""
    if not credits_path.exists():
        return QAResult("credits_exist", False, "Credits file not found", "error")
    with open(credits_path, encoding="utf-8") as f:
        credits = json.load(f)
    if not isinstance(credits, list) or len(credits) == 0:
        return QAResult("credits_content", False, "No credits entries", "error")
    required = ["provider", "rights", "source_url", "asset_url"]
    for c in credits:
        for field in required:
            if field not in c:
                return QAResult("credits_fields", False, f"Missing field: {field}", "error")
    return QAResult("credits_exist", True, f"{len(credits)} credits with all required fields", "info")


def validate_video_with_ffprobe(video_path: Path) -> Dict:
    """Validate final video with ffprobe. Returns dict with all checks."""
    try:
        subprocess.run(["ffprobe", "-version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {"passed": False, "error": "ffprobe not available", "checks": {}}

    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(video_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return {"passed": False, "error": "ffprobe failed", "checks": {}}

    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"passed": False, "error": "ffprobe output not JSON", "checks": {}}

    checks = {
        "format_valid": True,
        "resolution_ok": False,
        "framerate_ok": False,
        "duration_ok": False,
        "has_audio": False,
        "has_video": False,
        "duration_seconds": 0,
    }

    format_info = info.get("format", {})
    duration = float(format_info.get("duration", 0))
    checks["duration_seconds"] = duration
    checks["duration_ok"] = 45 <= duration <= 60

    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            checks["has_video"] = True
            width = stream.get("width", 0)
            height = stream.get("height", 0)
            checks["resolution_ok"] = (width == 1080 and height == 1920)

            r_frame_rate = stream.get("r_frame_rate", "0/1")
            try:
                num, den = map(int, r_frame_rate.split("/"))
                fps = num / den if den else 0
                checks["framerate_ok"] = abs(fps - 30) < 1
            except (ValueError, ZeroDivisionError):
                pass

        elif stream.get("codec_type") == "audio":
            checks["has_audio"] = True

    checks["passed"] = all([
        checks["resolution_ok"],
        checks["framerate_ok"],
        checks["duration_ok"],
        checks["has_audio"],
        checks["has_video"],
    ])

    return {"passed": checks["passed"], "checks": checks, "duration": duration}


def check_final_video(video_path: Path) -> QAResult:
    """Comprehensive final video validation."""
    result = validate_video_with_ffprobe(video_path)
    if "error" in result:
        return QAResult("final_video_ffprobe", False, f"ffprobe: {result['error']}", "error")

    checks = result.get("checks", {})
    failed = [k for k, v in checks.items() if v is False]
    if failed:
        details = f"Failed: {', '.join(failed)} | Duration: {result.get('duration', 0):.1f}s"
        return QAResult("final_video_ffprobe", False, details, "error")

    details = f"Resolution: OK, FPS: OK, Duration: {result.get('duration', 0):.1f}s, Audio: OK"
    return QAResult("final_video_ffprobe", True, details, "info")


def check_captions(captions_path: Path) -> QAResult:
    """Verify captions file exists and contains readable text entries."""
    if not captions_path.exists():
        return QAResult("captions_exist", False, "Captions file not found", "error")
    content = captions_path.read_text(encoding="utf-8")
    # Count SRT entries (blocks separated by blank lines)
    blocks = [b for b in content.strip().split("\n\n") if b.strip()]
    if not blocks:
        return QAResult("captions_content", False, "Captions file is empty", "error")
    # Verify each block has index, timestamp, and text
    valid = 0
    for b in blocks:
        lines = b.strip().split("\n")
        if len(lines) >= 3 and "-->" in lines[1]:
            valid += 1
    if valid == 0:
        return QAResult("captions_format", False, "No valid SRT entries found", "error")
    return QAResult("captions_content", True, f"{valid} caption entries in SRT", "info")


def check_caption_visibility(video_path: Path) -> QAResult:
    """Verify burned-in captions are present in rendered frames via OCR.

    Extracts sample frames and checks that text is rendered (not invisible).
    Requires pytesseract + tesseract binary on the runner.
    """
    try:
        import pytesseract
    except ImportError:
        return QAResult("caption_visibility", False, "pytesseract not installed; cannot verify caption visibility", "error")

    try:
        from PIL import Image
        import subprocess, tempfile, os
    except ImportError:
        return QAResult("caption_visibility", False, "PIL not available", "error")

    # Extract 3 sample frames from the middle of the video where captions should be visible
    tmpdir = tempfile.mkdtemp()
    try:
        frames_checked = 0
        frames_with_text = 0
        for i, t in enumerate([duration_approx(video_path) * 0.25,
                               duration_approx(video_path) * 0.50,
                               duration_approx(video_path) * 0.75]):
            frame_path = os.path.join(tmpdir, f"frame_{i}.png")
            r = subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-ss", str(t), "-i", str(video_path),
                 "-frames:v", "1", frame_path],
                capture_output=True, text=True
            )
            if r.returncode != 0 or not os.path.exists(frame_path):
                continue
            frames_checked += 1
            try:
                im = Image.open(frame_path).convert("RGB")
                # Crop to caption area (bottom center)
                w, h = im.size
                caption_region = im.crop((w*0.05, h*0.78, w*0.95, h*0.92))
                text = pytesseract.image_to_string(caption_region).strip()
                if text:
                    frames_with_text += 1
            except Exception:
                pass
        if frames_checked == 0:
            return QAResult("caption_visibility", False, "Could not extract frames for caption check", "error")
        if frames_with_text == 0:
            return QAResult("caption_visibility", False, f"No visible caption text in {frames_checked} sampled frames", "error")
        return QAResult("caption_visibility", True, f"Visible captions in {frames_with_text}/{frames_checked} sampled frames", "info")
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def duration_approx(video_path: Path) -> float:
    """Approximate video duration in seconds using ffprobe."""
    try:
        import subprocess, json
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(video_path)],
            capture_output=True, text=True
        )
        if r.returncode == 0:
            return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        pass
    return 55.0  # fallback


def check_scene_completeness(credits_path: Path, scene_count: int) -> QAResult:
    """Verify every planned scene has an approved source image (no missing cards)."""
    if not credits_path.exists():
        return QAResult("scene_completeness", False, "Credits file not found", "error")
    with open(credits_path, encoding="utf-8") as f:
        credits = json.load(f)
    if not isinstance(credits, list):
        return QAResult("scene_completeness", False, "Credits is not a list", "error")
    missing = [c for c in credits if c.get("provider") in ("missing asset", "fallback text card", "procedural illustration")]
    if missing:
        missing_titles = [c.get("scene", "unknown") for c in missing]
        return QAResult("scene_completeness", False,
                        f"{len(missing)} scene(s) missing approved image: {', '.join(missing_titles)}", "error")
    if len(credits) < scene_count:
        return QAResult("scene_completeness", False,
                        f"Only {len(credits)} credits for {scene_count} planned scenes", "error")
    return QAResult("scene_completeness", True, f"All {len(credits)} scenes have approved images", "info")


def run_review_qa(script_path: Path, output_dir: Optional[Path] = None,
                  scene_count: Optional[int] = None) -> List[QAResult]:
    """Run QA for visual-review-only runs (subset of checks)."""
    results = []
    results.append(check_script_word_count(script_path))
    results.append(check_estimated_duration(script_path))
    results.append(check_claims_support(script_path))

    if output_dir:
        board_path = output_dir / "approval-board.html"
        results.append(check_approval_board(board_path))

        credits_path = output_dir / "credits.json"
        results.append(check_credits(credits_path))

        # Scene completeness: every planned scene must have an approved image
        if scene_count is not None:
            results.append(check_scene_completeness(credits_path, scene_count))

        # Captions sidecar
        captions_files = list(output_dir.glob("*.srt")) + list(output_dir.glob("*.vtt"))
        if captions_files:
            results.append(check_captions(captions_files[0]))
        else:
            results.append(QAResult("captions_exist", False, "No SRT/VTT captions found", "error"))

    return results


def run_production_qa(script_path: Path, output_dir: Path, video_path: Path,
                      scene_count: Optional[int] = None) -> List[QAResult]:
    """Run full production QA - all checks mandatory."""
    results = []

    # Script checks
    results.append(check_script_word_count(script_path))
    results.append(check_estimated_duration(script_path))
    results.append(check_claims_support(script_path))
    results.append(check_claims_no_disputed(script_path))
    results.append(check_research_record(script_path))

    # Artifact checks
    board_path = output_dir / "approval-board.html"
    results.append(check_approval_board(board_path))

    credits_path = output_dir / "credits.json"
    results.append(check_credits(credits_path))

    # Scene completeness: every planned scene must have an approved image
    if scene_count is not None:
        results.append(check_scene_completeness(credits_path, scene_count))

    # Video checks
    results.append(check_video_output(video_path))
    results.append(check_final_video(video_path))

    # Captions: sidecar SRT plus visibility in rendered frames
    captions_files = list(output_dir.glob("*.srt")) + list(output_dir.glob("*.vtt"))
    if captions_files:
        results.append(check_captions(captions_files[0]))
        results.append(check_caption_visibility(video_path))
    else:
        results.append(QAResult("captions_exist", False, "No SRT/VTT captions found", "error"))

    return results


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Run QA checks on generated content")
    ap.add_argument("--script", required=True, help="Path to script JSON")
    ap.add_argument("--output-dir", help="Path to output directory with artifacts")
    ap.add_argument("--video", help="Path to final video MP4 (for production QA)")
    ap.add_argument("--mode", choices=["review", "production"], default="review",
                    help="review=visual review QA, production=full production QA")
    ap.add_argument("--fail-on-error", action="store_true", help="Exit with code 1 on any error")
    ap.add_argument("--scene-count", type=int, help="Number of planned scenes for completeness check")
    args = ap.parse_args()

    script_path = Path(args.script)
    output_dir = Path(args.output_dir) if args.output_dir else None
    video_path = Path(args.video) if args.video else None

    if args.mode == "production":
        if not output_dir:
            print("ERROR: --output-dir required for production mode")
            sys.exit(1)
        if not video_path:
            print("ERROR: --video required for production mode")
            sys.exit(1)
        results = run_production_qa(script_path, output_dir, video_path,
                                    scene_count=args.scene_count)
    else:
        results = run_review_qa(script_path, output_dir,
                                scene_count=args.scene_count)

    print("=== QA RESULTS ===")
    errors = 0
    warnings = 0
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        icon = "OK" if r.passed else "FAIL"
        print(f"  {icon} {r.check}: {r.details} [{r.severity}]")
        if not r.passed:
            if r.severity == "error":
                errors += 1
            elif r.severity == "warning":
                warnings += 1

    print(f"\nSummary: {len(results)} checks, {errors} errors, {warnings} warnings")

    if args.fail_on_error and errors > 0:
        sys.exit(1)

    # Also exit with error if production mode has any failures
    if args.mode == "production" and errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()