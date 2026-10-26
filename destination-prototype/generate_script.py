#!/usr/bin/env python3
"""Script generation module for daily content engine.

Generates narration scripts from research records, optimized for 45-60 second videos.
"""
import json
import re
import yaml
import glob
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "destination-prototype" / "data"
REGIONS_DIR = ROOT / "regions"

# Target word count for 45-60s at ~150-160 WPM (Kokoro default ~150-170 WPM)
TARGET_WORDS_MIN = 120
TARGET_WORDS_MAX = 150
TARGET_WORDS_IDEAL = 135


@dataclass
class Script:
    topic_slug: str
    destination: str
    category: str
    title: str
    hook: str
    narration: str
    word_count: int
    estimated_duration: float
    claims_supported: int
    claims_total: int
    claims_verified: int
    claims_insufficient: int
    claims_disputed: int
    generated_at: str
    kokoro_voice: str
    narration_language: str
    research_record: str


def load_region_config(destination_slug: str) -> Optional[Dict]:
    """Load full region config."""
    config_path = REGIONS_DIR / f"{destination_slug}.yaml"
    if not config_path.exists():
        return None
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_research(topic_slug: str) -> Optional[Dict]:
    """Load latest research record for topic."""
    files = glob.glob(str(DATA_DIR / f"research_{topic_slug}_*.json"))
    if not files:
        return None
    latest = max(files)
    with open(latest, encoding="utf-8") as f:
        return json.load(f)


def build_script_from_research(topic_data: Dict, research: Optional[Dict]) -> Script:
    """Generate a complete script from topic config and research."""
    destination_slug = topic_data.get("destination_slug", "")
    destination = topic_data.get("destination", "")
    category = topic_data.get("category", "")
    title = topic_data.get("title", "")

    # Load full region config for complete narration
    region_cfg = load_region_config(destination_slug)
    if region_cfg:
        story = region_cfg.get("story", {})
        narration_base = story.get("narration", "")
        hook_variants = story.get("hook_variants", {})
        hook = hook_variants.get("A", story.get("hook", ""))
        kokoro_voice = story.get("kokoro_voice", "af_heart")
        narration_lang = story.get("narration_language", "English")
    else:
        narration_base = topic_data.get("description", "")
        hook = topic_data.get("hook", "")
        kokoro_voice = topic_data.get("kokoro_voice", "af_heart")
        narration_lang = topic_data.get("narration_language", "English")

    # Extract claims from research
    verified_claims = []
    insufficient_claims = []
    disputed_claims = []

    if research:
        for claim in research.get("claims", []):
            if claim.get("status") == "verified":
                verified_claims.append(claim["text"])
            elif claim.get("status") == "insufficient_evidence":
                insufficient_claims.append(claim["text"])
            elif claim.get("status") == "disputed":
                disputed_claims.append(claim["text"])

    # Build structured script using full narration
    parts = []

    # 1. HOOK (use hook variant A)
    parts.append(hook if hook else f"{destination} was once a very different place than the {destination} you know today.")

    # 2-6. Use the full narration, split into structural parts
    script_parts = split_narration_into_structure(narration_base, destination)
    parts.extend(script_parts)

    full_narration = " ".join(parts)
    word_count = len(full_narration.split())

    # Trim if too long
    if word_count > TARGET_WORDS_MAX:
        full_narration = trim_to_word_count(full_narration, TARGET_WORDS_MAX)
        word_count = len(full_narration.split())

    # Pad if too short (add detail from verified claims only)
    if word_count < TARGET_WORDS_MIN and research:
        full_narration = expand_with_research(full_narration, verified_claims, TARGET_WORDS_MIN)
        word_count = len(full_narration.split())

    estimated_duration = word_count / 150 * 60  # seconds at 150 WPM

    research_file = ""
    if research:
        # Find the research file path
        files = glob.glob(str(DATA_DIR / f"research_{destination_slug}_*.json"))
        if files:
            research_file = max(files)

    return Script(
        topic_slug=destination_slug,
        destination=destination,
        category=category,
        title=title,
        hook=hook,
        narration=full_narration,
        word_count=word_count,
        estimated_duration=round(estimated_duration, 1),
        claims_supported=len(verified_claims) + len(insufficient_claims),
        claims_total=len(research.get("claims", [])) if research else 0,
        claims_verified=len(verified_claims),
        claims_insufficient=len(insufficient_claims),
        claims_disputed=len(disputed_claims),
        generated_at=datetime.now().isoformat(),
        kokoro_voice=kokoro_voice,
        narration_language=narration_lang,
        research_record=research_file
    )


def split_narration_into_structure(narration: str, destination: str) -> List[str]:
    """Split full narration into structural parts for a compelling arc."""
    sentences = [s.strip() for s in re.split(r'[.!?]+', narration) if len(s.strip()) > 20]
    if not sentences:
        return [f"{destination} has a rich history waiting to be discovered."]

    total = len(sentences)
    parts = []

    # Context: first 2-3 sentences
    context_end = min(3, total)
    if context_end > 0:
        parts.append(" ".join(sentences[:context_end]) + ".")

    # Development: middle portion
    dev_start = context_end
    dev_end = min(dev_start + 4, total - 2)
    if dev_start < dev_end:
        parts.append(" ".join(sentences[dev_start:dev_end]) + ".")

    # Climax: next 1-2 sentences
    climax_start = dev_end
    climax_end = min(climax_start + 2, total - 1)
    if climax_start < climax_end:
        parts.append(" ".join(sentences[climax_start:climax_end]) + ".")

    # Resolution: penultimate sentence
    if total > 1:
        parts.append(sentences[-2] + ".")

    # Closing: last sentence
    parts.append(sentences[-1] + ".")

    return parts


def trim_to_word_count(text: str, max_words: int) -> str:
    """Trim text to max words, preserving sentence boundaries."""
    words = text.split()
    if len(words) <= max_words:
        return text
    truncated = " ".join(words[:max_words])
    last_period = max(truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?"))
    if last_period > max_words * 0.7:
        return truncated[:last_period + 1]
    return truncated


def expand_with_research(text: str, claims: List[str], min_words: int) -> str:
    """Expand text with verified claims to reach minimum word count."""
    words = text.split()
    if len(words) >= min_words:
        return text
    for claim in claims:
        if len(text.split()) >= min_words:
            break
        if claim not in text:
            text += " " + claim + "."
    return text


def save_script(script: Script, output_path: Path):
    """Save script to disk."""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(asdict(script), f, indent=2, ensure_ascii=False)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Generate narration script from topic and research")
    ap.add_argument("--topic", required=True, help="Path to topic JSON or 'latest' from schedule")
    ap.add_argument("--output", required=True, help="Output script path")
    args = ap.parse_args()

    if args.topic == "latest":
        schedule_file = DATA_DIR / "daily_schedule.json"
        if not schedule_file.exists():
            print("ERROR: No schedule found.")
            sys.exit(1)
        with open(schedule_file, encoding="utf-8") as f:
            schedule = json.load(f)
        today = datetime.now().date().isoformat()
        entry = next((e for e in schedule if e.get("date") == today and e.get("topic")), None)
        if not entry:
            print(f"ERROR: No topic scheduled for {today}")
            sys.exit(1)
        topic_data = entry["topic"]
    else:
        with open(args.topic, encoding="utf-8") as f:
            topic_data = json.load(f)

    research = load_research(topic_data.get("destination_slug", ""))
    script = build_script_from_research(topic_data, research)
    save_script(script, Path(args.output))

    print(f"Generated script for: {script.title}")
    print(f"Words: {script.word_count} | Est. duration: {script.estimated_duration}s")
    print(f"Claims: {script.claims_verified} verified, {script.claims_insufficient} insufficient, {script.claims_disputed} disputed")
    print(f"Voice: {script.kokoro_voice} ({script.narration_language})")
    print(f"Research record: {script.research_record}")
    print(f"\n--- NARRATION ---\n{script.narration}")


if __name__ == "__main__":
    import sys
    main()