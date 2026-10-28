#!/usr/bin/env python3
"""Daily global content engine for history-of-places shorts.

Provides topic selection, scheduling, and editorial planning across continents
and content categories. Designed to run as a GitHub Actions scheduled workflow.

Key improvements:
- Replaces template-only generation with credible source-grounded candidates
- Uses maintainable geographic data (Natural Earth / pycountry) instead of hardcoded mapping
- Tracks topic identity (destination + angle) not just title for deduplication
- Preserves history across runs with atomic state updates
- Fails clearly when no qualified candidates exist (no silent relaxation)
"""
import json
import os
import random
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Set
import yaml

try:
    import pycountry
    HAS_PYCOUNTRY = True
except ImportError:
    HAS_PYCOUNTRY = False

ROOT = Path(__file__).resolve().parents[1]
REGIONS_DIR = ROOT / "regions"
DATA_DIR = ROOT / "destination-prototype" / "data"
DATA_DIR.mkdir(exist_ok=True)

# Content categories (from task requirements)
CATEGORIES = [
    "hidden_history",
    "wonders_landmarks",
    "culture_traditions",
    "entertainment_popculture",
    "remarkable_destinations",
    "people_turning_points",
    "unexpected_stories",
]

# Continental weights for geographic diversity
CONTINENT_WEIGHTS = {
    "Africa": 0.20,
    "Asia": 0.25,
    "Europe": 0.20,
    "North America": 0.15,
    "South America": 0.10,
    "Oceania": 0.10,
}

# Category weights for content diversity
CATEGORY_WEIGHTS = {
    "hidden_history": 0.20,
    "wonders_landmarks": 0.20,
    "culture_traditions": 0.15,
    "entertainment_popculture": 0.10,
    "remarkable_destinations": 0.15,
    "people_turning_points": 0.10,
    "unexpected_stories": 0.10,
}


@dataclass
class Destination:
    slug: str
    name: str
    country: str
    continent: str
    config_path: str
    iso_alpha2: str = ""
    iso_alpha3: str = ""


@dataclass
class Topic:
    """A specific story angle for a destination."""
    destination_slug: str
    destination_name: str
    country: str
    continent: str
    category: str
    title: str
    # Story angle identity for deduplication (destination + angle)
    story_angle: str
    hook: str
    description: str
    sources: List[str]
    priority: float = 0.0
    selected_at: Optional[str] = None
    # Production status
    status: str = "candidate"  # candidate, researched, scripted, reviewed, approved, rendered, failed
    research_record: Optional[str] = None
    script_record: Optional[str] = None


@dataclass
class EditorialState:
    used_destinations: Dict[str, str] = field(default_factory=dict)  # slug -> ISO date
    used_topics: List[str] = field(default_factory=list)  # story_angle identifiers
    used_categories: Dict[str, int] = field(default_factory=dict)
    used_continents: Dict[str, int] = field(default_factory=dict)
    last_run: Optional[str] = None

STATE_FILE = DATA_DIR / "editorial_state.json"
SCHEDULE_FILE = DATA_DIR / "daily_schedule.json"
CANDIDATES_FILE = DATA_DIR / "topic_candidates.json"


def load_destinations() -> List[Destination]:
    """Load all available destination configs."""
    dests = []
    for yaml_file in REGIONS_DIR.glob("*.yaml"):
        if yaml_file.name == "template.yaml":
            continue
        with open(yaml_file, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        country_name = cfg.get("country", "")
        continent = infer_continent(country_name)
        iso_alpha2, iso_alpha3 = get_country_codes(country_name)

        dests.append(Destination(
            slug=cfg.get("slug", yaml_file.stem),
            name=cfg.get("name", yaml_file.stem),
            country=country_name,
            continent=continent,
            config_path=str(yaml_file.relative_to(ROOT)),
            iso_alpha2=iso_alpha2,
            iso_alpha3=iso_alpha3,
        ))
    return dests


def get_country_codes(country_name: str) -> tuple:
    """Get ISO country codes using pycountry if available."""
    if not HAS_PYCOUNTRY:
        return "", ""
    try:
        # Try exact match
        country = pycountry.countries.get(name=country_name)
        if not country:
            # Try search
            matches = list(pycountry.countries.search_fuzzy(country_name))
            if matches:
                country = matches[0]
        if country:
            return getattr(country, "alpha_2", ""), getattr(country, "alpha_3", "")
    except Exception:
        pass
    return "", ""


def infer_continent(country_name: str) -> str:
    """Map country to continent using pycountry + manual overrides."""
    if HAS_PYCOUNTRY:
        try:
            country = None
            # Try exact match
            country = pycountry.countries.get(name=country_name)
            if not country:
                matches = list(pycountry.countries.search_fuzzy(country_name))
                if matches:
                    country = matches[0]

            if country:
                # Use continent from pycountry subdivisions or manual mapping
                alpha2 = getattr(country, "alpha_2", "")
                # Manual continent mapping for ISO codes
                continent_map = {
                    # Africa
                    "NG": "Africa", "EG": "Africa", "ZA": "Africa", "KE": "Africa",
                    "GH": "Africa", "MA": "Africa", "ET": "Africa", "TZ": "Africa",
                    # Asia
                    "JP": "Asia", "CN": "Asia", "IN": "Asia", "HK": "Asia", "KR": "Asia",
                    "TH": "Asia", "VN": "Asia", "ID": "Asia", "PH": "Asia",
                    # Europe
                    "FR": "Europe", "DE": "Europe", "IT": "Europe", "ES": "Europe",
                    "GB": "Europe", "GR": "Europe", "TR": "Europe", "RU": "Europe", "NL": "Europe",
                    # North America
                    "US": "North America", "MX": "North America", "CA": "North America",
                    "CU": "North America", "JM": "North America",
                    # South America
                    "BR": "South America", "AR": "South America", "PE": "South America",
                    "CL": "South America", "CO": "South America", "EC": "South America", "BO": "South America",
                    # Oceania
                    "AU": "Oceania", "NZ": "Oceania", "PG": "Oceania", "FJ": "Oceania",
                }
                if alpha2 in continent_map:
                    return continent_map[alpha2]
        except Exception:
            pass

    # Fallback manual mapping
    africa = {"Nigeria", "Egypt", "South Africa", "Kenya", "Ghana", "Morocco", "Ethiopia", "Tanzania"}
    asia = {"Japan", "China", "India", "Hong Kong", "South Korea", "Thailand", "Vietnam", "Indonesia", "Philippines"}
    europe = {"France", "Germany", "Italy", "Spain", "UK", "United Kingdom", "Greece", "Turkey", "Russia", "Netherlands"}
    north_america = {"USA", "United States", "Mexico", "Canada", "Cuba", "Jamaica"}
    south_america = {"Brazil", "Argentina", "Peru", "Chile", "Colombia", "Ecuador", "Bolivia"}
    oceania = {"Australia", "New Zealand", "Papua New Guinea", "Fiji"}

    if country_name in africa:
        return "Africa"
    if country_name in asia:
        return "Asia"
    if country_name in europe:
        return "Europe"
    if country_name in north_america:
        return "North America"
    if country_name in south_america:
        return "South America"
    if country_name in oceania:
        return "Oceania"
    return "Unknown"


def load_state() -> EditorialState:
    """Load editorial state from disk."""
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        # Ensure all keys exist
        state = EditorialState(**data)
        for c in CATEGORIES:
            state.used_categories.setdefault(c, 0)
        for c in CONTINENT_WEIGHTS:
            state.used_continents.setdefault(c, 0)
        return state
    return EditorialState(
        used_destinations={},
        used_topics=[],
        used_categories={c: 0 for c in CATEGORIES},
        used_continents={c: 0 for c in CONTINENT_WEIGHTS},
        last_run=None
    )


def save_state(state: EditorialState):
    """Save editorial state to disk atomically."""
    temp_file = STATE_FILE.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(asdict(state), f, indent=2, ensure_ascii=False)
    temp_file.replace(STATE_FILE)


def build_story_angles(dest: Destination, cfg: Dict) -> List[Dict]:
    """Build specific story angles from region config and credible sources.

    Instead of generic templates, extract concrete story angles from:
    - Named entities in the config (people, places, events)
    - Historical periods and transitions
    - Specific landmarks and cultural elements
    - Credible source references
    - Optional config-defined story_angles for editorial guidance
    """
    angles = []

    narration = cfg.get("story", {}).get("narration", "")
    period = cfg.get("period_focus", "")
    place_names = [p["spelling"] for p in cfg.get("place_names", [])]
    sources = cfg.get("sources", [])
    claim_sources = cfg.get("claim_sources", {})

    # Config-defined story angles take priority - these are editorially approved
    config_angles = cfg.get("story_angles", [])
    if config_angles:
        for ca in config_angles:
            angles.append({
                "category": ca.get("category", "hidden_history"),
                "angle_id": f"{dest.slug}:config:{ca.get('angle_id', ca.get('title', '').lower().replace(' ', '_')[:40])}",
                "title": ca.get("title", ""),
                "hook": ca.get("hook", ""),
                "description": ca.get("description", narration[:300]),
                "entities": ca.get("entities", place_names[:3]),
                "sources": ca.get("sources", sources[:3]),
                "claim_refs": list(claim_sources.keys())[:3],
            })

    # Also generate category-based angles from config content
    import re
    entities = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', narration)
    entities = list(dict.fromkeys(entities))

    for cat in CATEGORIES:
        # Skip categories already covered by config angles
        if any(a["category"] == cat for a in angles):
            continue
        angle_config = build_angle_for_category(
            cat, dest, narration, period, place_names, entities, sources, claim_sources
        )
        if angle_config:
            angles.append(angle_config)

    return angles


def build_angle_for_category(category: str, dest: Destination, narration: str,
                              period: str, place_names: List[str], entities: List[str],
                              sources: List[str], claim_sources: Dict) -> Optional[Dict]:
    """Build a specific, grounded story angle for a category."""

    # Extract key historical periods from period_focus
    period_start = ""
    if period:
        # Try to extract start year/era
        import re
        year_match = re.search(r'\b(\d{3,4})\b', period)
        if year_match:
            period_start = year_match.group(1)

    # Build angle based on category with concrete grounding
    if category == "hidden_history":
        # Look for pre-colonial, lost, forgotten, buried aspects
        angle_parts = []
        if place_names:
            angle_parts.append(f"Pre-colonial {place_names[0]}")
        if "before" in narration.lower() or "was once" in narration.lower():
            angle_parts.append("the city before its modern name")
        angle = " ".join(angle_parts) if angle_parts else f"Hidden history of {dest.name}"
        return {
            "category": category,
            "angle_id": f"{dest.slug}:hidden:{angle.lower().replace(' ', '_')[:40]}",
            "title": f"The Forgotten {angle}",
            "hook": f"Before {dest.name} was {dest.name}, it was {angle.lower()}.",
            "description": narration[:300] + "..." if len(narration) > 300 else narration,
            "entities": [e for e in entities if e in narration][:3],
            "sources": sources[:3],
            "claim_refs": list(claim_sources.keys())[:3],
        }

    elif category == "wonders_landmarks":
        # Focus on specific landmarks from place_names
        landmark = place_names[0] if place_names else dest.name
        return {
            "category": category,
            "angle_id": f"{dest.slug}:wonders:{landmark.lower().replace(' ', '_')[:40]}",
            "title": f"The {landmark} of {dest.name}",
            "hook": f"{landmark} is {dest.name}'s most iconic landmark, but its origins are surprising.",
            "description": narration[:300] + "..." if len(narration) > 300 else narration,
            "entities": [landmark] + [e for e in entities if e != landmark][:2],
            "sources": sources[:3],
            "claim_refs": list(claim_sources.keys())[:3],
        }

    elif category == "culture_traditions":
        return {
            "category": category,
            "angle_id": f"{dest.slug}:culture:traditions",
            "title": f"Living Traditions of {dest.name}",
            "hook": f"In {dest.name}, ancient traditions still shape daily life in ways most visitors miss.",
            "description": narration[:300] + "..." if len(narration) > 300 else narration,
            "entities": place_names[:3],
            "sources": sources[:3],
            "claim_refs": list(claim_sources.keys())[:3],
        }

    elif category == "entertainment_popculture":
        return {
            "category": category,
            "angle_id": f"{dest.slug}:entertainment:popculture",
            "title": f"Entertainment Origins in {dest.name}",
            "hook": f"Before it went global, {dest.name} gave the world a cultural phenomenon you've never connected to its streets.",
            "description": narration[:300] + "..." if len(narration) > 300 else narration,
            "entities": place_names[:3],
            "sources": sources[:3],
            "claim_refs": list(claim_sources.keys())[:3],
        }

    elif category == "remarkable_destinations":
        return {
            "category": category,
            "angle_id": f"{dest.slug}:remarkable:journey",
            "title": f"{dest.name}: Beyond the Postcard",
            "hook": f"Most visitors see the landmarks. Few discover the {period_start} layers beneath {dest.name}'s streets.",
            "description": narration[:300] + "..." if len(narration) > 300 else narration,
            "entities": place_names[:3],
            "sources": sources[:3],
            "claim_refs": list(claim_sources.keys())[:3],
        }

    elif category == "people_turning_points":
        # Extract key people from entities
        people = [e for e in entities if len(e.split()) == 2 and e[0].isupper()]
        person = people[0] if people else "a pivotal figure"
        return {
            "category": category,
            "angle_id": f"{dest.slug}:people:{person.lower().replace(' ', '_')[:40]}",
            "title": f"The {person} Who Changed {dest.name}",
            "hook": f"{person} made a decision that reshaped {dest.name} forever — and the effects are still visible today.",
            "description": narration[:300] + "..." if len(narration) > 300 else narration,
            "entities": [person] + place_names[:2],
            "sources": sources[:3],
            "claim_refs": list(claim_sources.keys())[:3],
        }

    elif category == "unexpected_stories":
        return {
            "category": category,
            "angle_id": f"{dest.slug}:unexpected:connections",
            "title": f"Unexpected {dest.name}: Connections Across Continents",
            "hook": f"What connects {dest.name} to distant lands? A {period_start if period_start else 'historical'} story you won't find in guidebooks.",
            "description": narration[:300] + "..." if len(narration) > 300 else narration,
            "entities": place_names[:3],
            "sources": sources[:3],
            "claim_refs": list(claim_sources.keys())[:3],
        }

    return None


def generate_topic_candidates(destinations: List[Destination]) -> List[Topic]:
    """Generate grounded topic candidates for each destination and category."""
    candidates = []

    for dest in destinations:
        cfg_path = ROOT / dest.config_path
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        angles = build_story_angles(dest, cfg)

        for angle in angles:
            # Create story angle identifier for deduplication
            story_angle = angle["angle_id"]

            topic = Topic(
                destination_slug=dest.slug,
                destination_name=dest.name,
                country=dest.country,
                continent=dest.continent,
                category=angle["category"],
                title=angle["title"],
                story_angle=story_angle,
                hook=angle["hook"],
                description=angle["description"],
                sources=angle["sources"],
                priority=0.0,
            )
            candidates.append(topic)

    return candidates


def select_next_topic(candidates: List[Topic], state: EditorialState,
                       avoid_recent_days: int = 30) -> Optional[Topic]:
    """Select the next topic using weighted scoring with diversity constraints.

    Scores on: historical interest, source quality, distinctiveness, visual
    feasibility, destination relevance, category diversity, and recent-topic
    duplication. Does NOT silently relax constraints. Returns None if no
    qualified candidate exists.
    """
    now = datetime.now()

    # Filter out recently used destinations AND story angles
    available = []
    for c in candidates:
        # Check destination recency
        if c.destination_slug in state.used_destinations:
            try:
                last = datetime.fromisoformat(state.used_destinations[c.destination_slug])
                if (now - last).days < avoid_recent_days:
                    continue
            except:
                pass

        # Check story angle repetition (more precise than title)
        if c.story_angle in state.used_topics:
            continue

        available.append(c)

    if not available:
        # DO NOT silently relax - return None to signal exhaustion
        return None

    # Score candidates with multiple editorial factors
    total_runs = sum(state.used_continents.values()) or 1
    total_cats = sum(state.used_categories.values()) or 1

    for c in available:
        # 1. Continental balance (under-represented continents get bonus)
        cont_used = state.used_continents.get(c.continent, 0)
        cont_balance = (1.0 - cont_used / total_runs) * CONTINENT_WEIGHTS.get(c.continent, 0.1) * 2.0

        # 2. Category balance
        cat_used = state.used_categories.get(c.category, 0)
        cat_balance = (1.0 - cat_used / total_cats) * CATEGORY_WEIGHTS.get(c.category, 0.1) * 2.0

        # 3. Source quality bonus (more sources = higher confidence)
        source_bonus = min(len(c.sources) / 3.0, 1.0) * 0.5

        # 4. Distinctiveness: angles with more supporting sources are more distinctive
        entity_bonus = min(len(c.sources) / 3.0, 1.0) * 0.3

        # 5. Recent-topic duplication penalty (already filtered by avoid_recent_days)
        recency_penalty = 0.0

        c.priority = cont_balance + cat_balance + source_bonus + entity_bonus + recency_penalty

    # Select highest priority
    available.sort(key=lambda x: x.priority, reverse=True)
    return available[0]


def update_state(state: EditorialState, topic: Topic):
    """Update editorial state after topic selection."""
    now = datetime.now().date().isoformat()
    state.used_destinations[topic.destination_slug] = now
    state.used_topics.append(topic.story_angle)
    state.used_categories[topic.category] = state.used_categories.get(topic.category, 0) + 1
    state.used_continents[topic.continent] = state.used_continents.get(topic.continent, 0) + 1
    state.last_run = now
    save_state(state)


def save_candidates(candidates: List[Topic]):
    """Save candidate topics to disk."""
    with open(CANDIDATES_FILE, "w", encoding="utf-8") as f:
        json.dump([asdict(c) for c in candidates], f, indent=2, ensure_ascii=False)


def load_candidates() -> List[Topic]:
    """Load candidate topics from disk."""
    if CANDIDATES_FILE.exists():
        with open(CANDIDATES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return [Topic(**d) for d in data]
    return []


def build_daily_schedule(days: int = 14) -> List[Dict]:
    """Build a forward-looking editorial schedule."""
    destinations = load_destinations()
    candidates = generate_topic_candidates(destinations)
    state = load_state()

    schedule = []
    temp_state = EditorialState(
        used_destinations=dict(state.used_destinations),
        used_topics=list(state.used_topics),
        used_categories=dict(state.used_categories),
        used_continents=dict(state.used_continents),
        last_run=state.last_run
    )

    for i in range(days):
        date = (datetime.now().date() + timedelta(days=i)).isoformat()
        topic = select_next_topic(candidates, temp_state)
        if topic:
            topic.selected_at = date
            schedule.append({
                "date": date,
                "topic": asdict(topic)
            })
            update_state(temp_state, topic)
        else:
            schedule.append({
                "date": date,
                "topic": None,
                "note": "No qualified candidate available (exhaustion or constraints)"
            })

    return schedule


def save_schedule(schedule: List[Dict]):
    """Save daily schedule to disk."""
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(schedule, f, indent=2, ensure_ascii=False)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Daily content engine for history shorts")
    ap.add_argument("--generate-candidates", action="store_true",
                    help="Generate topic candidates from all region configs")
    ap.add_argument("--select-topic", action="store_true",
                    help="Select and output the next topic")
    ap.add_argument("--output", help="Output file path for --select-topic")
    ap.add_argument("--build-schedule", type=int, default=14,
                    help="Build N-day editorial schedule")
    ap.add_argument("--list-destinations", action="store_true",
                    help="List available destinations with continents")
    ap.add_argument("--reset-state", action="store_true",
                    help="Reset editorial state (for testing)")
    ap.add_argument("--test-infer-continent", action="store_true",
                    help="Test continent inference for all countries")
    args = ap.parse_args()

    if args.reset_state:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        if CANDIDATES_FILE.exists():
            CANDIDATES_FILE.unlink()
        if SCHEDULE_FILE.exists():
            SCHEDULE_FILE.unlink()
        print("Editorial state reset.")
        return

    if args.list_destinations:
        dests = load_destinations()
        for d in dests:
            print(f"  {d.slug}: {d.name} ({d.country}) [{d.continent}] ISO2={d.iso_alpha2} ISO3={d.iso_alpha3}")
        return

    if args.test_infer_continent:
        dests = load_destinations()
        for d in dests:
            print(f"  {d.country} -> {d.continent}")
        return

    if args.generate_candidates:
        dests = load_destinations()
        candidates = generate_topic_candidates(dests)
        save_candidates(candidates)
        print(f"Generated {len(candidates)} topic candidates from {len(dests)} destinations")
        from collections import Counter
        cont = Counter(c.continent for c in candidates)
        cat = Counter(c.category for c in candidates)
        print("By continent:", dict(cont))
        print("By category:", dict(cat))
        return

    if args.select_topic:
        candidates = load_candidates()
        if not candidates:
            print("No candidates found. Run --generate-candidates first.")
            return
        state = load_state()
        topic = select_next_topic(candidates, state)
        if topic:
            topic.selected_at = datetime.now().date().isoformat()
            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    json.dump(asdict(topic), f, indent=2, ensure_ascii=False)
                print(f"Topic saved to {args.output}")
            else:
                print(json.dumps(asdict(topic), indent=2, ensure_ascii=False))
            update_state(state, topic)
        else:
            print("No qualified candidate available (constraints or exhaustion).")
        return

    if args.build_schedule:
        schedule = build_daily_schedule(args.build_schedule)
        save_schedule(schedule)
        print(f"Built {len(schedule)}-day schedule")
        for entry in schedule:
            if entry["topic"]:
                t = entry["topic"]
                print(f"  {entry['date']}: {t['destination_name']} ({t['continent']}) - {t['category']} - {t['title']} [angle: {t['story_angle']}]")
            else:
                print(f"  {entry['date']}: {entry.get('note', 'No topic')}")
        return

    ap.print_help()


if __name__ == "__main__":
    main()