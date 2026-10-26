#!/usr/bin/env python3
"""Quality assurance checks for generated shorts."""
import json
import subprocess
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
    """Verify minimum claim support."""
    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)
    supported = script.get("claims_supported", 0)
    total = script.get("claims_total", 0)
    if total == 0:
        return QAResult("claims_support", True, "No claims to verify", "info")
    ratio = supported / total
    if ratio >= 0.7:
        return QAResult("claims_support", True, f"{supported}/{total} claims verified ({ratio:.0%})", "info")
    elif ratio >= 0.4:
        return QAResult("claims_support", True, f"{supported}/{total} claims verified ({ratio:.0%})", "warning")
    else:
        return QAResult("claims_support", False, f"Only {supported}/{total} claims verified ({ratio:.0%})", "error")


def check_video_output(video_path: Path) -> QAResult:
    """Verify video file exists and has valid properties."""
    if not video_path.exists():
        return QAResult("video_exists", False, "Video file not found", "error")
    if video_path.stat().st_size < 10000:
        return QAResult("video_size", False, f"Video too small: {video_path.stat().st_size} bytes", "error")
    # Could add ffprobe checks here
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
    # Check each credit has required fields
    required = ["provider", "rights", "source_url", "asset_url"]
    for c in credits:
        for field in required:
            if field not in c:
                return QAResult("credits_fields", False, f"Missing field: {field}", "error")
    return QAResult("credits_exist", True, f"{len(credits)} credits with all required fields", "info")


def run_qa_checks(script_path: Path, output_dir: Optional[Path] = None) -> List[QAResult]:
    """Run all QA checks."""
    results = []
    results.append(check_script_word_count(script_path))
    results.append(check_estimated_duration(script_path))
    results.append(check_claims_support(script_path))

    if output_dir:
        board_path = output_dir / "approval-board.html"
        results.append(check_approval_board(board_path))

        credits_path = output_dir / "credits.json"
        results.append(check_credits(credits_path))

        video_files = list(output_dir.glob("*.mp4"))
        if video_files:
            results.append(check_video_output(video_files[0]))

    return results


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Run QA checks on generated content")
    ap.add_argument("--script", required=True, help="Path to script JSON")
    ap.add_argument("--output-dir", help="Path to output directory with artifacts")
    ap.add_argument("--fail-on-error", action="store_true", help="Exit with code 1 on any error")
    args = ap.parse_args()

    script_path = Path(args.script)
    output_dir = Path(args.output_dir) if args.output_dir else None

    results = run_qa_checks(script_path, output_dir)

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
        exit(1)


if __name__ == "__main__":
    main()