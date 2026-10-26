#!/usr/bin/env python3
"""Daily global content engine for history-of-places shorts.

Provides topic selection, scheduling, and editorial planning across continents
and content categories. Designed to run as a GitHub Actions scheduled workflow.
"""
import json
import os
import random
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Set
import yaml

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

@dataclass
class Topic:
    destination_slug: str
    destination_name: str
    country: str
    continent: str
    category: str
    title: str
    hook: str
    description: str
    sources: List[str]
    priority: float  # composite score
    selected_at: Optional[str] = None

@dataclass
class EditorialState:
    used_destinations: Dict[str, str]  # slug -> ISO date
    used_topics: List[str]  # topic titles
    used_categories: Dict[str, int]  # category -> count
    used_continents: Dict[str, int]  # continent -> count
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
        # Infer continent from country (simplified)
        continent = infer_continent(cfg.get("country", ""))
        dests.append(Destination(
            slug=cfg.get("slug", yaml_file.stem),
            name=cfg.get("name", yaml_file.stem),
            country=cfg.get("country", ""),
            continent=continent,
            config_path=str(yaml_file.relative_to(ROOT))
        ))
    return dests


def infer_continent(country: str) -> str:
    """Map country to continent (simplified)."""
    africa = {"Nigeria", "Egypt", "South Africa", "Kenya", "Ghana", "Morocco", "Ethiopia", "Tanzania"}
    asia = {"Japan", "China", "India", "Hong Kong", "South Korea", "Thailand", "Vietnam", "Indonesia", "Philippines"}
    europe = {"France", "Germany", "Italy", "Spain", "UK", "United Kingdom", "Greece", "Turkey", "Russia", "Netherlands"}
    north_america = {"USA", "United States", "Mexico", "Canada", "Cuba", "Jamaica"}
    south_america = {"Brazil", "Argentina", "Peru", "Chile", "Colombia", "Ecuador", "Bolivia"}
    oceania = {"Australia", "New Zealand", "Papua New Guinea", "Fiji"}

    if country in africa:
        return "Africa"
    if country in asia:
        return "Asia"
    if country in europe:
        return "Europe"
    if country in north_america:
        return "North America"
    if country in south_america:
        return "South America"
    if country in oceania:
        return "Oceania"
    return "Unknown"


def load_state() -> EditorialState:
    """Load editorial state from disk."""
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return EditorialState(**data)
    return EditorialState(
        used_destinations={},
        used_topics=[],
        used_categories={c: 0 for c in CATEGORIES},
        used_continents={c: 0 for c in CONTINENT_WEIGHTS},
        last_run=None
    )


def save_state(state: EditorialState):
    """Save editorial state to disk."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(asdict(state), f, indent=2, ensure_ascii=False)


def generate_topic_candidates(destinations: List[Destination]) -> List[Topic]:
    """Generate candidate topics for each destination and category."""
    # Predefined topic templates per destination and category
    # In production, this could use an LLM to generate topics from config data
    candidates = []

    for dest in destinations:
        cfg_path = ROOT / dest.config_path
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        narration = cfg.get("story", {}).get("narration", "")
        sources = cfg.get("sources", [])
        hook = cfg.get("story", {}).get("hook", "")
        period = cfg.get("period_focus", "")
        place_names = [p["spelling"] for p in cfg.get("place_names", [])]

        # Generate one topic per category
        for cat in CATEGORIES:
            topic = build_topic_for_category(
                dest, cat, narration, sources, hook, period, place_names
            )
            if topic:
                candidates.append(topic)

    return candidates


def build_topic_for_category(dest: Destination, category: str, narration: str,
                              sources: List[str], hook: str, period: str,
                              place_names: List[str]) -> Optional[Topic]:
    """Build a topic for a specific category and destination."""
    # Category-specific topic templates
    templates = {
        "hidden_history": [
            f"The Forgotten {place_names[0] if place_names else 'History'} of {dest.name}",
            f"Lost Cities of {dest.name}: What {dest.name} Was Before",
            f"Hidden {dest.name}: The {period.split(',')[0] if period else 'Ancient'} Past",
        ],
        "wonders_landmarks": [
            f"The {place_names[0] if place_names else 'Wonder'} of {dest.name}",
            f"{dest.name}'s Greatest {period.split(',')[0] if period else 'Historic'} Landmark",
            f"Building {dest.name}: The Story Behind {place_names[0] if place_names else 'Its Monuments'}",
        ],
        "culture_traditions": [
            f"Traditions of {dest.name}: {hook}",
            f"Cultural Heritage of {dest.name}: From {period}",
            f"The Soul of {dest.name}: Festivals, Food, and Folkways",
        ],
        "entertainment_popculture": [
            f"Entertainment Origins in {dest.name}: A Cultural History",
            f"How {dest.name} Shaped Global Pop Culture",
            f"The Arts of {dest.name}: From Tradition to Modern Stage",
        ],
        "remarkable_destinations": [
            f"{dest.name}: A Journey Through Time and Place",
            f"Remarkable {dest.name}: Streets, Stories, and Secrets",
            f"Discovering {dest.name}: Beyond the Tourist Trail",
        ],
        "people_turning_points": [
            f"Turning Points in {dest.name}: {hook}",
            f"The People Who Built {dest.name}: {period}",
            f"Defining Moments in {dest.name}'s History",
        ],
        "unexpected_stories": [
            f"Surprising {dest.name}: Connections You Never Knew",
            f"The Unexpected History of {dest.name}",
            f"{dest.name} and the World: Hidden Links Across Continents",
        ],
    }

    titles = templates.get(category, [f"{category.title()} in {dest.name}"])
    title = titles[0]  # Pick first template

    # Generate description from narration
    desc = narration[:300] + "..." if len(narration) > 300 else narration

    # Priority: favor unused continents/categories, recent destinations get lower priority
    now = datetime.now()
    last_used = None
    if dest.slug in load_state().used_destinations:
        try:
            last_used = datetime.fromisoformat(load_state().used_destinations[dest.slug])
        except:
            pass

    priority = 1.0
    if last_used:
        days_since = (now - last_used).days
        priority *= min(1.0, days_since / 30.0)  # decays over 30 days

    # Continental/category balance bonus
    state = load_state()
    cont_count = state.used_continents.get(dest.continent, 0)
    cat_count = state.used_categories.get(category, 0)
    total_topics = sum(state.used_continents.values()) or 1
    priority *= (1.0 + (1.0 - cont_count / max(1, total_topics)) * 0.5)
    priority *= (1.0 + (1.0 - cat_count / max(1, total_topics)) * 0.5)

    return Topic(
        destination_slug=dest.slug,
        destination_name=dest.name,
        country=dest.country,
        continent=dest.continent,
        category=category,
        title=title,
        hook=hook,
        description=desc,
        sources=sources[:3],  # Top 3 sources
        priority=priority,
        selected_at=None
    )


def select_next_topic(candidates: List[Topic], state: EditorialState,
                       avoid_recent_days: int = 30) -> Optional[Topic]:
    """Select the next topic using weighted scoring with diversity constraints."""
    now = datetime.now()

    # Filter out recently used destinations
    available = []
    for c in candidates:
        if c.destination_slug in state.used_destinations:
            try:
                last = datetime.fromisoformat(state.used_destinations[c.destination_slug])
                if (now - last).days < avoid_recent_days:
                    continue
            except:
                pass
        # Also avoid exact topic repeats
        if c.title in state.used_topics:
            continue
        available.append(c)

    if not available:
        # Relax constraints if nothing available
        available = [c for c in candidates if c.title not in state.used_topics]

    if not available:
        return None

    # Score candidates
    for c in available:
        # Continental balance
        cont_weight = CONTINENT_WEIGHTS.get(c.continent, 0.1)
        cat_weight = CATEGORY_WEIGHTS.get(c.category, 0.1)
        cont_used = state.used_continents.get(c.continent, 0)
        cat_used = state.used_categories.get(c.category, 0)
        total = sum(state.used_continents.values()) or 1

        balance_bonus = (
            (1.0 - cont_used / total) * cont_weight * 2.0 +
            (1.0 - cat_used / total) * cat_weight * 2.0
        )
        c.priority = c.priority + balance_bonus

    # Select highest priority
    available.sort(key=lambda x: x.priority, reverse=True)
    return available[0]


def update_state(state: EditorialState, topic: Topic):
    """Update editorial state after topic selection."""
    now = datetime.now().date().isoformat()
    state.used_destinations[topic.destination_slug] = now
    state.used_topics.append(topic.title)
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
                "note": "No available topics"
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
    ap.add_argument("--build-schedule", type=int, default=14,
                    help="Build N-day editorial schedule")
    ap.add_argument("--list-destinations", action="store_true",
                    help="List available destinations with continents")
    ap.add_argument("--reset-state", action="store_true",
                    help="Reset editorial state (for testing)")
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
            print(f"  {d.slug}: {d.name} ({d.country}) [{d.continent}]")
        return

    if args.generate_candidates:
        dests = load_destinations()
        candidates = generate_topic_candidates(dests)
        save_candidates(candidates)
        print(f"Generated {len(candidates)} topic candidates from {len(dests)} destinations")
        # Print summary by continent/category
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
            print(json.dumps(asdict(topic), indent=2, ensure_ascii=False))
            update_state(state, topic)
        else:
            print("No available topic found.")
        return

    if args.build_schedule:
        schedule = build_daily_schedule(args.build_schedule)
        save_schedule(schedule)
        print(f"Built {len(schedule)}-day schedule")
        for entry in schedule:
            if entry["topic"]:
                t = entry["topic"]
                print(f"  {entry['date']}: {t['destination_name']} ({t['continent']}) - {t['category']} - {t['title']}")
            else:
                print(f"  {entry['date']}: {entry.get('note', 'No topic')}")
        return

    # Default: show help
    ap.print_help()


if __name__ == "__main__":
    main()