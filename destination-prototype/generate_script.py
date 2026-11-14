#!/usr/bin/env python3
"""Script generation module for daily content engine.

Generates narration scripts from research records, optimized for 45-60 second videos.
"""
import json
import re
import sys
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
TARGET_WORDS_MIN = 110
TARGET_WORDS_MAX = 155
TARGET_WORDS_IDEAL = 135

# Minimum verified claims needed to produce a valid narration
# At ~12-15 words per claim sentence, need 8-9 claims for 110-155 words
MIN_VERIFIED_CLAIMS = 8


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
    claims_provider_failures: int
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


def load_research(topic_slug: str, research_path: Optional[str] = None) -> Optional[Dict]:
    """Load research record for topic. If research_path is provided, use it directly."""
    if research_path and Path(research_path).exists():
        with open(research_path, encoding="utf-8") as f:
            return json.load(f)
    # Fallback: search in DATA_DIR for backwards compatibility
    files = glob.glob(str(DATA_DIR / f"research_{topic_slug}_*.json"))
    if not files:
        return None
    latest = max(files)
    with open(latest, encoding="utf-8") as f:
        return json.load(f)


def build_script_from_research(topic_data: Dict, research: Optional[Dict], research_file: str = "") -> Optional[Script]:
    """Generate a complete script from topic config and research.
    
    Narration is built from verified research claims, not from config narration.
    Every factual claim in the output must be traceable to a verified claim.
    
    Returns None if insufficient verified claims to produce valid narration.
    """
    destination_slug = topic_data.get("destination_slug", "")
    destination = topic_data.get("destination", "")
    category = topic_data.get("category", "")
    title = topic_data.get("title", "")

    # Load full region config for defaults (voice, language)
    region_cfg = load_region_config(destination_slug)
    if region_cfg:
        story = region_cfg.get("story", {})
        hook_variants = story.get("hook_variants", {})
        kokoro_voice = story.get("kokoro_voice", "af_heart")
        narration_lang = story.get("narration_language", "English")
    else:
        hook_variants = {}
        kokoro_voice = topic_data.get("kokoro_voice", "af_heart")
        narration_lang = topic_data.get("narration_language", "English")

    # Use the story-specific hook
    hook = (
        topic_data.get("hook")
        or hook_variants.get("A")
        or topic_data.get("hook", "")
        or f"{destination} was once a very different place than the {destination} you know today."
    )

    # Extract and organize verified claims
    verified_claims = []
    insufficient_claims = []
    disputed_claims = []
    provider_failure_claims = []

    if research:
        for claim in research.get("claims", []):
            if claim.get("status") == "verified":
                verified_claims.append(claim)
            elif claim.get("status") == "insufficient_evidence":
                insufficient_claims.append(claim)
            elif claim.get("status") == "disputed":
                disputed_claims.append(claim)
            elif claim.get("status") == "provider_failure":
                provider_failure_claims.append(claim)

    # Build narration from verified claims only
    try:
        narration = build_narration_from_claims(hook, verified_claims, destination, category)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return None
    word_count = len(narration.split())

    # Trim if too long
    if word_count > TARGET_WORDS_MAX:
        narration = trim_to_word_count(narration, TARGET_WORDS_MAX)
        word_count = len(narration.split())

    # Should not happen with new logic, but keep as safety
    if word_count < TARGET_WORDS_MIN:
        print(f"ERROR: Narration still too short ({word_count} words) after claim distribution", file=sys.stderr)
        return None

    estimated_duration = word_count / 150 * 60  # seconds at 150 WPM

    return Script(
        topic_slug=destination_slug,
        destination=destination,
        category=category,
        title=title,
        hook=hook,
        narration=narration,
        word_count=word_count,
        estimated_duration=round(estimated_duration, 1),
        claims_supported=len(verified_claims) + len(insufficient_claims),
        claims_total=len(research.get("claims", [])) if research else 0,
        claims_verified=len(verified_claims),
        claims_insufficient=len(insufficient_claims),
        claims_disputed=len(disputed_claims),
        claims_provider_failures=len(provider_failure_claims),
        generated_at=datetime.now().isoformat(),
        kokoro_voice=kokoro_voice,
        narration_language=narration_lang,
        research_record=research_file
    )


def build_narration_from_claims(hook: str, verified_claims: List[Dict], destination: str, category: str) -> str:
    """Build a coherent narration from verified claims.
    
    Organizes claims into a narrative arc: context -> development -> climax -> resolution.
    Each sentence must be traceable to a verified claim.
    
    Raises:
        ValueError: If insufficient verified claims to meet minimum word count.
    """
    if not verified_claims:
        raise ValueError(f"No verified claims available for {destination}. Cannot generate narration without evidence.")
    
    if len(verified_claims) < MIN_VERIFIED_CLAIMS:
        raise ValueError(
            f"Insufficient verified claims for {destination}: {len(verified_claims)} verified, "
            f"minimum {MIN_VERIFIED_CLAIMS} required to produce {TARGET_WORDS_MIN}-{TARGET_WORDS_MAX} word narration."
        )
    
    # Sort claims by chronological hints (years, periods) if available
    sorted_claims = _sort_claims_chronologically(verified_claims)
    
    # Build narrative structure
    parts = [hook]
    
    # Distribute claims across narrative beats more evenly
    n = len(sorted_claims)
    # Opening: first 2 claims
    opening_claims = sorted_claims[:min(2, n)]
    for claim in opening_claims:
        parts.append(_claim_to_narrative_sentence(claim, "opening"))
    
    # Development: next claims up to n-2
    development_end = max(2, n - 2)
    development_claims = sorted_claims[2:development_end]
    for claim in development_claims[:3]:
        parts.append(_claim_to_narrative_sentence(claim, "development"))
    
    # Climax: second-to-last claim (if n > 3)
    if n > 3:
        parts.append(_claim_to_narrative_sentence(sorted_claims[n - 2], "climax"))
    
    # Resolution: last claim
    parts.append(_claim_to_narrative_sentence(sorted_claims[-1], "resolution"))
    
    # Ensure we have enough content - add more development claims if needed
    narration = " ".join(parts)
    if len(narration.split()) < TARGET_WORDS_MIN:
        # Add remaining claims not yet used
        used_indices = set()
        used_indices.update(range(min(2, n)))  # opening
        used_indices.update(range(2, min(development_end, 2 + 3)))  # development
        if n > 3:
            used_indices.add(n - 2)  # climax
        used_indices.add(n - 1)  # resolution
        
        remaining = [c for i, c in enumerate(sorted_claims) if i not in used_indices]
        for claim in remaining[:3]:
            parts.append(_claim_to_narrative_sentence(claim, "development"))
            narration = " ".join(parts)
            if len(narration.split()) >= TARGET_WORDS_MIN:
                break
    
    # Final trim if too long
    if len(narration.split()) > TARGET_WORDS_MAX:
        narration = trim_to_word_count(narration, TARGET_WORDS_MAX)
    
    return narration


def _sort_claims_chronologically(claims: List[Dict]) -> List[Dict]:
    """Sort claims by chronological order based on years mentioned in claim text."""
    def extract_earliest_year(claim_text: str) -> int:
        import re
        years = re.findall(r'\b(1[0-9]{3}|20[0-2][0-9])\b', claim_text)
        if years:
            return min(int(y) for y in years)
        return 9999  # Claims without dates go to the end
    
    return sorted(claims, key=lambda c: extract_earliest_year(c["text"]))


def _claim_to_narrative_sentence(claim: Dict, beat: str) -> str:
    """Convert a verified claim into a natural narrative sentence."""
    text = claim["text"].strip()
    
    # Clean up the claim text for narration
    # Remove redundant phrases
    text = text.replace("The city ", f"{claim.get('destination', 'The city')} ")
    text = text.replace("It was ", "It ")
    
    # Ensure it ends with a period
    if not text.endswith("."):
        text += "."
    
    return text


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

    # Resolution: penultimate sentence (only when climax did not already reach it)
    if total > 1 and climax_end < total - 1:
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
    ap.add_argument("--research", help="Path to research JSON (explicit path from orchestrator)")
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

    research = load_research(topic_data.get("destination_slug", ""), args.research)
    script = build_script_from_research(topic_data, research, args.research or "")
    save_script(script, Path(args.output))

    print(f"Generated script for: {script.title}")
    print(f"Words: {script.word_count} | Est. duration: {script.estimated_duration}s")
    print(f"Claims: {script.claims_verified} verified, {script.claims_insufficient} insufficient, {script.claims_disputed} disputed, {script.claims_provider_failures} provider failures")
    print(f"Voice: {script.kokoro_voice} ({script.narration_language})")
    print(f"Research record: {script.research_record}")
    print(f"\n--- NARRATION ---\n{script.narration}")


if __name__ == "__main__":
    import sys
    main()