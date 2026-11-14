#!/usr/bin/env python3
"""Story generation from eligible region inventory.

Stories emerge from the material actually discovered -- never from a
pre-authored storyboard. This module:

1. Clusters eligible inventory items by theme (architecture, trade, people,
   events, landmarks, culture, transport, conflict, independence, daily life).
2. Scores candidate stories by evidence strength, asset variety, narrative
   coherence, visual variety and duration fit.
3. Researches factual claims suggested by each candidate story using reliable
   sources (a photo alone does not prove its caption, date or interpretation).
4. Selects the story the evidence and available images can genuinely support.
5. Produces a scene plan where every scene references an actual inventoried
   asset (pinned image) -- no scene is authored before its image exists.

If the inventory cannot support a good story, the caller is told the actual
coverage shortfall so it can stop for review rather than fabricate a narrative.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from image_inventory import (  # noqa: E402
    RegionInventory, InventoryItem, eligible_items, items_by_theme,
)
from asset_resolver import _norm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "destination-prototype" / "data"
STORY_DIR = DATA_DIR / "stories"


def _searchable_text(item: InventoryItem, lower: bool = True) -> str:
    """Build normalized searchable text from all relevant inventory fields.

    This is the single source of truth for entity matching. It includes:
    - title, description, author
    - categories, facets

    All fields are concatenated with spaces. By default lowercased for case-insensitive matching.
    """
    parts = [
        item.title or "",
        item.description or "",
        item.author or "",
        " ".join(item.categories or []),
        " ".join(item.facets or []),
    ]
    text = " ".join(parts)
    return text.lower() if lower else text

# Theme clusters with representative terms. These are discovery aids, not
# scene templates: an item is assigned to a theme by metadata match only.
THEMES = [
    ("architecture", ["architecture", "building", "palace", "temple", "mosque", "church", "fort",
                      "castle", "tower", "bridge", "wall", "gate", "facade"]),
    ("trade", ["trade", "market", "port", "harbour", "harbor", "dock", "warehouse", "exchange",
               "merchant", "commerce", "warehouse", "bazaar"]),
    ("people", ["people", "portrait", "leader", "ruler", "king", "emperor", "shogun", "governor",
                "chief", "figure", "person", "statue"]),
    ("events", ["events", "revolution", "war", "battle", "independence", "annexation", "treaty",
                "proclamation", "coronation", "founding", "restoration"]),
    ("landmarks", ["landmarks", "landmark", "monument", "memorial", "square", "station", "airport",
                   "skyline", "tower", "cathedral"]),
    ("culture", ["culture", "festival", "dance", "music", "theatre", "theater", "print", "painting",
                 "craft", "ceremony", "tradition"]),
    ("transport", ["transport", "railway", "train", "ferry", "boat", "ship", "canal", "river", "road",
                   "tram", "carriage", "steamboat"]),
    ("conflict", ["conflict", "fort", "castle", "battle", "siege", "colonial", "occupation", "resistance"]),
    ("independence", ["independence", "freedom", "liberation", "republic", "sovereign"]),
    ("daily_life", ["daily_life", "street", "market", "crowd", "daily life", "neighbourhood", "neighborhood",
                    "home", "house", "family", "work"]),
]


@dataclass
class AssetRef:
    """A lightweight reference to an inventoried asset for scene planning."""
    provider: str
    title: str
    image_url: str
    source_url: str
    rights: str
    rights_statement: str
    rights_url: str
    license_url: str
    author: str
    identifier: str
    date_text: str
    year_range: Optional[List[int]]
    dimensions: Optional[List[int]]
    digest: str
    categories: List[str] = field(default_factory=list)
    facets: List[str] = field(default_factory=list)
    clip_score: Optional[float] = None
    relevance_score: float = 0.0
    image_type: str = "historic"  # historic or modern

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StoryCandidate:
    """A candidate story derived from inventory clusters."""
    theme: str
    title: str
    summary: str
    assets: List[AssetRef] = field(default_factory=list)
    evidence_score: float = 0.0
    asset_variety_score: float = 0.0
    coherence_score: float = 0.0
    visual_variety_score: float = 0.0
    duration_fit_score: float = 0.0
    total_score: float = 0.0
    claims: List[str] = field(default_factory=list)
    claim_support: Dict[str, List[str]] = field(default_factory=dict)  # claim text -> source urls
    rejection_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StorySelection:
    """The selected story and its scene plan."""
    region_slug: str
    region_name: str
    selected_theme: str
    story_title: str
    story_summary: str
    story_assets: List[AssetRef] = field(default_factory=list)
    candidates: List[StoryCandidate] = field(default_factory=list)
    claims: List[str] = field(default_factory=list)
    claim_support: Dict[str, List[str]] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().date().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _ensure_dirs() -> None:
    STORY_DIR.mkdir(parents=True, exist_ok=True)


def story_path(region_slug: str) -> Path:
    _ensure_dirs()
    return STORY_DIR / f"{region_slug}.json"


def save_story(region_slug: str, selection: StorySelection) -> Path:
    _ensure_dirs()
    path = story_path(region_slug)
    path.write_text(json.dumps(selection.to_dict(), indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def load_story(region_slug: str) -> Optional[StorySelection]:
    path = story_path(region_slug)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        assets = [AssetRef(**a) for a in data.get("story_assets", [])]
        candidates = []
        for c in data.get("candidates", []):
            ca = [AssetRef(**a) for a in c.get("assets", [])]
            candidates.append(StoryCandidate(
                theme=c.get("theme", ""), title=c.get("title", ""), summary=c.get("summary", ""),
                assets=ca, evidence_score=c.get("evidence_score", 0.0),
                asset_variety_score=c.get("asset_variety_score", 0.0),
                coherence_score=c.get("coherence_score", 0.0),
                visual_variety_score=c.get("visual_variety_score", 0.0),
                duration_fit_score=c.get("duration_fit_score", 0.0),
                total_score=c.get("total_score", 0.0),
                claims=c.get("claims", []),
                claim_support=c.get("claim_support", {}),
                rejection_reason=c.get("rejection_reason"),
            ))
        return StorySelection(
            region_slug=data.get("region_slug", region_slug),
            region_name=data.get("region_name", ""),
            selected_theme=data.get("selected_theme", ""),
            story_title=data.get("story_title", ""),
            story_summary=data.get("story_summary", ""),
            story_assets=assets,
            candidates=candidates,
            claims=data.get("claims", []),
            claim_support=data.get("claim_support", {}),
            created_at=data.get("created_at", ""),
        )
    except Exception:
        return None


def _period_of(item: InventoryItem) -> str:
    """Classify an item as historic/modern/unknown from original creation/capture date only.

    Distinguishes original artwork creation/capture date from digitisation, upload,
    publication or record-modification dates. Uses the earliest year found in
    ``date_text``/``year_range`` as the creation-date estimate: a work cannot be
    created before its earliest attested date, so a historic work must not become
    modern merely because its archive record was digitised or uploaded recently.

    Returns "historic" (creation before 1950), "modern" (creation 1950 or later,
    confirmed by year_range or a single date), or "unknown" (original date cannot
    be established — never invents a date).
    """
    years: List[int] = []
    yr = item.year_range
    if yr and len(yr) == 2:
        years.extend([int(y) for y in yr if y])
    dt = (item.date_text or "").lower()
    years.extend(int(y) for y in re.findall(r"(1[0-9]{3}|20[0-2][0-9])", dt))
    years = [y for y in years if 1500 <= y <= 2100]

    if not years:
        return "unknown"

    creation_year = min(years)
    # Any attested date before 1950 means the work (or its subject) predates 1950.
    if creation_year < 1950:
        return "historic"
    # All attested dates are recent: only classify modern when we have explicit
    # year_range confirmation (not just a date_text that may be an upload timestamp).
    if yr and len(yr) == 2 and yr[1] >= 1950:
        return "modern"
    # Recent date_text alone is ambiguous: could be upload/digitisation of a
    # historical work. Preserve unknown rather than falsely labelling modern.
    return "unknown"


def cluster_inventory(inventory: RegionInventory) -> List[Tuple[str, List[InventoryItem]]]:
    """Group eligible inventory items by theme via metadata match.

    Returns list of (theme, items) for themes that have at least one item.
    Items can appear in multiple themes (soft clustering).
    """
    eligible = eligible_items(inventory)
    clusters: List[Tuple[str, List[InventoryItem]]] = []
    for theme, terms in THEMES:
        matched = items_by_theme(inventory, terms)
        if matched:
            clusters.append((theme, matched))
    # Sort by cluster size desc so the richest themes are considered first.
    clusters.sort(key=lambda t: len(t[1]), reverse=True)
    return clusters


def _extract_entities_from_items(items: List[InventoryItem], region_name: str) -> Dict[str, List[str]]:
    """Extract specific historical entities from asset metadata.

    Returns dict with keys: people, places, events, periods.
    Only returns entities that appear in multiple assets (consensus).
    """
    people = Counter()
    places = Counter()
    events = Counter()
    periods = Counter()

    # Terms that look like person names but are actually art metadata / descriptors
    # These should be filtered out from people extraction
    NON_PERSON_TERMS = {
        "print japanese", "color woodblock", "woodblock print", "ukiyo e", "ukiyo e",
        "edo period", "meiji period", "taisho period", "showa period",
        "japanese print", "japanese woodblock", "japanese painting",
        "color print", "black white", "ink painting", "scroll painting",
        "album leaf", "fan print", "pillar print", "surimono print",
        "artist unknown", "unknown artist", "school of", "circle of",
        "style of", "after ", "attributed to", "manner of",
        "signed ", "sealed ", "dated ", "inscribed ",
        "mount fuji", "mt fuji", "tokyo bay", "edo bay",
        "nihonbashi bridge", "tokugawa shogunate", "imperial palace",
        "national museum", "metropolitan museum", "british museum",
        "victoria albert", "art institute", "cleveland museum",
        "smithsonian institution", "library congress", "europeana collections",
        "public domain", "creative commons", "open access", "no known copyright",
        # Additional false positives observed in production - multi-word phrases
        "painting japanese", "japanese painting", "art japanese", "japanese art",
        "color woodblock", "woodblock print", "ukiyo e",
        # Single words that are never part of person names (for exact match filtering)
        "woodblock", "ukiyo", "surimono", "shogunate",
    }

    # Known place names for the region
    place_names = {region_name.lower()}

    for item in items:
        # For person names, ONLY use the author field (most reliable in museum metadata)
        # This avoids false positives from title/description patterns like "Japanese Woodblock"
        if item.author:
            # Extract full name from author (e.g., "Utagawa Hiroshige")
            author_text = item.author.strip()
            # Check if it looks like a person name (First Last pattern)
            if re.match(r'^[A-Z][a-z]+ [A-Z][a-z]+$', author_text):
                name_lower = author_text.lower()
                if name_lower not in NON_PERSON_TERMS and not any(re.search(rf'\b{re.escape(term)}\b', name_lower) for term in NON_PERSON_TERMS if len(term) > 4):
                    people[author_text] += 1
            else:
                # Single-word surname (e.g., "Hiroshige", "Hokusai")
                author_words = author_text.split()
                for word in author_words:
                    if (len(word) > 3 and word[0].isupper() and word.lower() not in NON_PERSON_TERMS and
                        not any(re.search(rf'\b{re.escape(term)}\b', word.lower()) for term in NON_PERSON_TERMS if len(term) > 4)):
                        people[word] += 1

        # Extract years as periods from all searchable text
        text = _searchable_text(item)
        for year_match in re.finditer(r'\b(1[0-9]{3}|20[0-2][0-9])\b', text):
            periods[year_match.group()] += 1

    # Only keep entities that appear in multiple assets (consensus)
    min_occurrences = max(2, len(items) // 3)
    raw_people = [p for p, c in people.items() if c >= min_occurrences]
    raw_places = [p for p, c in places.items() if c >= min_occurrences]
    raw_events = [e for e, c in events.items() if c >= min_occurrences]
    raw_periods = [p for p, c in periods.items() if c >= min_occurrences]
    
    # Canonicalize person entities: map aliases to their fullest form
    canonical_people = _canonicalize_person_entities(raw_people)
    
    return {
        "people": canonical_people,
        "places": raw_places,
        "events": raw_events,
        "periods": raw_periods,
    }

def _canonicalize_person_entities(people: List[str]) -> List[str]:
    """Canonicalize person entities by mapping aliases to their fullest form.
    
    For example: "Hiroshige" and "Utagawa" both map to "Utagawa Hiroshige"
    if that full name is present. Returns deduplicated list of canonical names.
    """
    if not people:
        return []
    
    # Build a mapping from each name to its best canonical form
    # Strategy: For each person name, find the longest name that contains it
    # as a word-boundary substring. That longest name is the canonical form.
    canonical_map = {}
    people_lower = [p.lower() for p in people]
    
    for i, person in enumerate(people):
        person_lower = people_lower[i]
        # Find all other names that contain this name as a whole word
        containing = []
        for j, other in enumerate(people):
            if i == j:
                continue
            other_lower = people_lower[j]
            # Check if person_lower appears as a whole word in other_lower
            # e.g., "hiroshige" in "utagawa hiroshige"
            if re.search(rf'\b{re.escape(person_lower)}\b', other_lower):
                containing.append(other)
        
        if containing:
            # Use the longest containing name as canonical
            canonical = max(containing, key=len)
            canonical_map[person] = canonical
        else:
            # No containing name found, this might be the canonical form itself
            # Check if this name contains any other name
            contained = []
            for j, other in enumerate(people):
                if i == j:
                    continue
                other_lower = people_lower[j]
                if re.search(rf'\b{re.escape(other_lower)}\b', person_lower):
                    contained.append(other)
            if not contained:
                # This name doesn't contain others and isn't contained - it's canonical
                canonical_map[person] = person
            else:
                # This name contains others - it's the canonical form
                canonical_map[person] = person
    
    # Deduplicate by canonical form
    seen_canonical = set()
    result = []
    for person in people:
        canonical = canonical_map.get(person, person)
        if canonical not in seen_canonical:
            seen_canonical.add(canonical)
            result.append(canonical)
    
    return result


def _generate_specific_title(theme: str, entities: Dict[str, List[str]], region_name: str) -> Tuple[str, bool]:
    """Generate a specific story title from extracted entities.

    Returns (title, is_specific).
    is_specific is True if the title identifies a concrete historical subject.
    """
    if entities["people"]:
        person = entities["people"][0]
        if entities["periods"]:
            return f"The {person} Era in {region_name}", True
        return f"{person} and the History of {region_name}", True

    if entities["places"]:
        place = entities["places"][0]
        if entities["periods"]:
            return f"{place} ({entities['periods'][0]}) in {region_name}", True
        return f"The History of {place} in {region_name}", True

    if entities["events"]:
        event = entities["events"][0]
        if entities["periods"]:
            return f"The {event} ({entities['periods'][0]}) in {region_name}", True
        return f"The {event} in {region_name}", True

    if entities["periods"]:
        period = entities["periods"][0]
        return f"{region_name} in the {period}s", True

    # No specific entities found
    return f"{theme.title()} story", False


def _score_candidate(theme: str, items: List[InventoryItem],
                     claim_support: Dict[str, List[str]],
                     region_name: str = "") -> StoryCandidate:
    """Score a candidate story by evidence, verified claims, coherence, variety, duration.

    Verified research claims carry the most weight: a cluster label alone is not
    a story. The narration must explain a meaningful historical development
    supported by independently verified research, not simply catalogue image titles.
    """
    n = len(items)
    if n == 0:
        return StoryCandidate(theme=theme, title=f"{theme.title()} story", summary="")

    # Evidence strength: fraction of assets with CLIP/relevance score and date evidence.
    scored = [i for i in items if i.clip_score is not None or i.date_text or i.year_range]
    evidence_score = min(1.0, len(scored) / max(1, n))

    # Verified claims strength: how many research claims are supported by these assets.
    claim_texts = set(claim_support.keys()) if claim_support else set()
    claim_hits = 0
    for claim in claim_texts:
        c_low = claim.lower()
        for i in items:
            hay = (i.title + " " + i.description).lower()
            if c_low in hay or any(k in hay for k in c_low.split() if len(k) > 4):
                claim_hits += 1
                break
    claim_score = min(1.0, claim_hits / max(1, len(claim_texts))) if claim_texts else 0.0

    # Asset variety: distinct providers + distinct date spread.
    providers = {i.provider for i in items}
    date_spread = len({i.date_text for i in items if i.date_text})
    asset_variety_score = min(1.0, (len(providers) + date_spread) / 6.0)

    # Coherence: how tightly items cluster around the theme terms.
    theme_terms = [t for t, _ in THEMES if t == theme]
    terms = theme_terms[0][1] if theme_terms else []
    coherent = 0
    for i in items:
        hay = (i.title + " " + i.description).lower()
        if any(t.lower() in hay for t in terms):
            coherent += 1
    coherence_score = coherent / max(1, n)

    # Chronological coherence: items cluster within a related time period.
    periods = {_period_of(i) for i in items}
    hist_count = sum(1 for i in items if _period_of(i) == "historic")
    mod_count = sum(1 for i in items if _period_of(i) == "modern")
    if hist_count + mod_count > 0:
        # A story is more coherent when most items share a period classification.
        chron_score = max(hist_count, mod_count) / (hist_count + mod_count)
    else:
        chron_score = 0.5

    # Visual variety: mix of historic and modern periods.
    visual_variety_score = min(1.0, len(periods) / 2.0)

    # Duration fit: enough assets for ~45-60s video at ~5-7s per scene (8-12 scenes).
    duration_fit_score = min(1.0, n / 10.0)

    # Extract specific entities to generate a meaningful title
    entities = _extract_entities_from_items(items, region_name) if region_name else {"people": [], "places": [], "events": [], "periods": []}
    title, is_specific = _generate_specific_title(theme, entities, region_name)

    # Reject generic stories that lack a specific historical subject
    if not is_specific:
        return StoryCandidate(
            theme=theme, title=title, summary="",
            rejection_reason="no_specific_historical_subject"
        )

    # Narrative themes (events, people, independence) score higher than generic
    # containers (architecture, landmarks, daily_life) because they imply a story.
    # Only give bonus if we have specific entities.
    narrative_themes = {"events", "people", "independence", "conflict", "trade", "transport", "culture"}
    narrative_bonus = 0.10 if (theme in narrative_themes and is_specific) else 0.0

    total = (0.25 * evidence_score + 0.20 * claim_score +
             0.15 * asset_variety_score + 0.15 * coherence_score +
             0.10 * chron_score + 0.05 * visual_variety_score +
             0.10 * duration_fit_score + narrative_bonus)

    assets = [AssetRef(
        provider=i.provider, title=i.title, image_url=i.image_url, source_url=i.source_url,
        rights=i.rights, rights_statement=i.rights_statement, rights_url=i.rights_url,
        license_url=i.license_url, author=i.author, identifier=i.identifier,
        date_text=i.date_text, year_range=i.year_range, dimensions=i.dimensions,
        digest=i.digest, categories=i.categories, facets=i.facets,
        clip_score=i.clip_score, relevance_score=i.relevance_score,
        image_type=_period_of(i) if _period_of(i) in ("historic", "modern") else "historic",
    ) for i in items]

    return StoryCandidate(
        theme=theme,
        title=title,
        summary=f"A {theme}-focused narrative built from {len(items)} {provider_list(assets)} assets.",
        assets=assets,
        evidence_score=evidence_score,
        asset_variety_score=asset_variety_score,
        coherence_score=coherence_score,
        visual_variety_score=visual_variety_score,
        duration_fit_score=duration_fit_score,
        total_score=total,
        claims=list(claim_support.keys()),
        claim_support=claim_support,
    )


def _group_items_by_entity(items: List[InventoryItem], entity_type: str, entities: List[str]) -> Dict[str, List[InventoryItem]]:
    """Group inventory items by a specific entity (person, place, event).
    
    Returns dict mapping entity name to list of items mentioning that entity.
    """
    grouped: Dict[str, List[InventoryItem]] = {}
    for entity in entities:
        entity_items = []
        entity_lower = entity.lower()
        for item in items:
            if entity_lower in _searchable_text(item):
                entity_items.append(item)
        if entity_items:
            grouped[entity] = entity_items
    return grouped


def _score_candidate_for_entity(theme: str, items: List[InventoryItem],
                                entity_type: str, entity_name: str,
                                claim_support: Dict[str, List[str]],
                                region_name: str = "") -> StoryCandidate:
    """Score a candidate story focused on a specific entity within a theme.
    
    This is a variant of _score_candidate that creates a candidate for a specific
    entity (person, place, event) rather than the whole theme cluster.
    """
    n = len(items)
    if n == 0:
        return StoryCandidate(theme=theme, title=f"{theme.title()} story", summary="", rejection_reason="no_items_for_entity")

    # Evidence strength: fraction of assets with CLIP/relevance score and date evidence.
    scored = [i for i in items if i.clip_score is not None or i.date_text or i.year_range]
    evidence_score = min(1.0, len(scored) / max(1, n))

    # Verified claims strength: how many research claims are supported by these assets.
    claim_texts = set(claim_support.keys()) if claim_support else set()
    claim_hits = 0
    for claim in claim_texts:
        c_low = claim.lower()
        for i in items:
            hay = (i.title + " " + i.description).lower()
            if c_low in hay or any(k in hay for k in c_low.split() if len(k) > 4):
                claim_hits += 1
                break
    claim_score = min(1.0, claim_hits / max(1, len(claim_texts))) if claim_texts else 0.0

    # Asset variety: distinct providers + distinct date spread.
    providers = {i.provider for i in items}
    date_spread = len({i.date_text for i in items if i.date_text})
    asset_variety_score = min(1.0, (len(providers) + date_spread) / 6.0)

    # Coherence: how tightly items cluster around the theme terms.
    theme_terms = [t for t, _ in THEMES if t == theme]
    terms = theme_terms[0][1] if theme_terms else []
    coherent = 0
    for i in items:
        hay = (i.title + " " + i.description).lower()
        if any(t.lower() in hay for t in terms):
            coherent += 1
    coherence_score = coherent / max(1, n)

    # Chronological coherence: items cluster within a related time period.
    periods = {_period_of(i) for i in items}
    hist_count = sum(1 for i in items if _period_of(i) == "historic")
    mod_count = sum(1 for i in items if _period_of(i) == "modern")
    if hist_count + mod_count > 0:
        chron_score = max(hist_count, mod_count) / (hist_count + mod_count)
    else:
        chron_score = 0.5

    # Visual variety: mix of historic and modern periods.
    visual_variety_score = min(1.0, len(periods) / 2.0)

    # Duration fit: enough assets for ~45-60s video at ~5-7s per scene (8-12 scenes).
    duration_fit_score = min(1.0, n / 10.0)

    # Extract periods from items for title generation
    item_entities = _extract_entities_from_items(items, region_name)
    item_periods = item_entities.get("periods", [])

    # Generate title specific to this entity
    if entity_type == "people":
        if entity_name and item_periods:
            title = f"The {entity_name} Era in {region_name}"
        else:
            title = f"{entity_name} and the History of {region_name}"
    elif entity_type == "places":
        if entity_name and item_periods:
            title = f"{entity_name} ({item_periods[0]}) in {region_name}"
        else:
            title = f"The History of {entity_name} in {region_name}"
    elif entity_type == "events":
        if entity_name and item_periods:
            title = f"The {entity_name} ({item_periods[0]}) in {region_name}"
        else:
            title = f"The {entity_name} in {region_name}"
    else:
        title = f"{entity_name} in {region_name}"

    is_specific = bool(entity_name)

    # Reject generic stories that lack a specific historical subject
    if not is_specific:
        return StoryCandidate(
            theme=theme, title=title, summary="",
            rejection_reason="no_specific_historical_subject"
        )

    # Narrative themes (events, people, independence) score higher than generic
    # containers (architecture, landmarks, daily_life) because they imply a story.
    narrative_themes = {"events", "people", "independence", "conflict", "trade", "transport", "culture"}
    narrative_bonus = 0.10 if (theme in narrative_themes and is_specific) else 0.0

    total = (0.25 * evidence_score + 0.20 * claim_score +
             0.15 * asset_variety_score + 0.15 * coherence_score +
             0.10 * chron_score + 0.05 * visual_variety_score +
             0.10 * duration_fit_score + narrative_bonus)

    assets = [AssetRef(
        provider=i.provider, title=i.title, image_url=i.image_url, source_url=i.source_url,
        rights=i.rights, rights_statement=i.rights_statement, rights_url=i.rights_url,
        license_url=i.license_url, author=i.author, identifier=i.identifier,
        date_text=i.date_text, year_range=i.year_range, dimensions=i.dimensions,
        digest=i.digest, categories=i.categories, facets=i.facets,
        clip_score=i.clip_score, relevance_score=i.relevance_score,
        image_type=_period_of(i) if _period_of(i) in ("historic", "modern") else "historic",
    ) for i in items]

    return StoryCandidate(
        theme=theme,
        title=title,
        summary=f"A {theme}-focused narrative built from {len(items)} {provider_list(assets)} assets about {entity_name}.",
        assets=assets,
        evidence_score=evidence_score,
        asset_variety_score=asset_variety_score,
        coherence_score=coherence_score,
        visual_variety_score=visual_variety_score,
        duration_fit_score=duration_fit_score,
        total_score=total,
        claims=list(claim_support.keys()),
        claim_support=claim_support,
    )


def provider_list(assets: List[AssetRef]) -> str:
    from collections import Counter
    c = Counter(a.provider for a in assets)
    return ", ".join(f"{k}({v})" for k, v in c.most_common(3))


def _build_entity_candidates(theme: str, items: List[InventoryItem],
                             claim_support: Dict[str, List[str]],
                             region_name: str) -> List[StoryCandidate]:
    """Build multiple candidates from a theme cluster by splitting on distinct entities.
    
    For each entity type (people, places, events), create a separate candidate
    using only items that mention that entity. This allows multiple researchable
    stories from the same theme cluster.
    
    Deduplicates candidates by canonical subject identity and asset overlap.
    """
    entities = _extract_entities_from_items(items, region_name)
    candidates: List[StoryCandidate] = []
    seen_asset_digests: Set[str] = set()  # Track assets used by candidates
    
    # Create candidates for each person entity
    for person in entities.get("people", []):
        person_lower = person.lower()
        person_items = [i for i in items if person_lower in _searchable_text(i)]
        if len(person_items) >= 6:  # Minimum assets for a viable story
            # Check asset overlap with existing candidates
            person_digests = {i.digest for i in person_items if i.digest}
            if person_digests and person_digests & seen_asset_digests:
                # This entity's assets significantly overlap with existing candidate
                # Skip to avoid duplicate subject
                continue
            cand = _score_candidate_for_entity(theme, person_items, "people", person, claim_support, region_name)
            if not cand.rejection_reason:
                candidates.append(cand)
                seen_asset_digests.update(person_digests)
    
    # Create candidates for each place entity
    for place in entities.get("places", []):
        place_lower = place.lower()
        place_items = [i for i in items if place_lower in _searchable_text(i)]
        if len(place_items) >= 6:
            place_digests = {i.digest for i in place_items if i.digest}
            if place_digests and place_digests & seen_asset_digests:
                continue
            cand = _score_candidate_for_entity(theme, place_items, "places", place, claim_support, region_name)
            if not cand.rejection_reason:
                candidates.append(cand)
                seen_asset_digests.update(place_digests)
    
    # Create candidates for each event entity
    for event in entities.get("events", []):
        event_lower = event.lower()
        event_items = [i for i in items if event_lower in _searchable_text(i)]
        if len(event_items) >= 6:
            event_digests = {i.digest for i in event_items if i.digest}
            if event_digests and event_digests & seen_asset_digests:
                continue
            cand = _score_candidate_for_entity(theme, event_items, "events", event, claim_support, region_name)
            if not cand.rejection_reason:
                candidates.append(cand)
                seen_asset_digests.update(event_digests)
    
    # Fallback: if no entity-specific candidates, create one for the whole cluster
    if not candidates:
        cand = _score_candidate(theme, items, claim_support, region_name)
        if not cand.rejection_reason:
            candidates.append(cand)
    
    return candidates


def select_story_candidates(clusters: List[Tuple[str, List[InventoryItem]]],
                           claim_support: Optional[Dict[str, List[str]]] = None,
                           region_name: str = "") -> List[StoryCandidate]:
    """Return all viable candidate stories from inventory clusters, sorted by score.
    
    Returns empty list when no candidate has enough assets or sufficient narrative
    coherence to support a 45-60s video.
    
    Each theme cluster can now produce multiple candidates by splitting on
    distinct entities (people, places, events), allowing up to 3 fallback attempts.
    """
    claim_support = claim_support or {}
    candidates: List[StoryCandidate] = []
    rejected_reasons = []
    
    for theme, items in clusters:
        # Build entity-specific candidates from this cluster
        entity_candidates = _build_entity_candidates(theme, items, claim_support, region_name)
        
        for cand in entity_candidates:
            if cand.rejection_reason:
                rejected_reasons.append(f"{theme}/{cand.title}: {cand.rejection_reason}")
            else:
                candidates.append(cand)
    
    # Only stories with enough distinct assets AND sufficient narrative coherence
    # can support a 45-60s video. A generic cluster label is not a story.
    MIN_TOTAL_SCORE = 0.40
    viable = [c for c in candidates
              if len(c.assets) >= 6 and c.total_score >= MIN_TOTAL_SCORE]
    if not viable:
        if rejected_reasons:
            print(f"  Rejected candidates: {', '.join(rejected_reasons)}")
        return []
    
    viable.sort(key=lambda c: c.total_score, reverse=True)
    return viable


def select_story(clusters: List[Tuple[str, List[InventoryItem]]],
                 claim_support: Optional[Dict[str, List[str]]] = None,
                 region_name: str = "") -> Optional[StoryCandidate]:
    """Select the best-supported candidate story from inventory clusters.

    Ranking: evidence strength, verified claims, asset variety, coherence,
    chronological coherence, visual variety, duration fit. Returns None when no
    candidate has enough assets or sufficient narrative coherence to support a
    45-60s video -- the caller must stop honestly rather than fabricate.
    """
    viable = select_story_candidates(clusters, claim_support, region_name)
    return viable[0] if viable else None


def plan_scenes_from_story(story: StoryCandidate,
                           inventory: RegionInventory,
                           target_scenes: int = 9) -> List[Dict[str, Any]]:
    """Build a scene plan where every scene references an asset from the selected story.

    Scenes are authored *after* assets are chosen. Every scene references an
    asset belonging to ``story.assets`` -- never an asset from another cluster.
    Each scene:
    - references a pinned asset (image_url) from the selected story
    - carries narration_intent explaining the connection between narration and visuals
    - uses image_type from the asset's period classification
    - derives required_keywords / image_search_terms from asset metadata (not invented)
    """
    if not story or not story.assets:
        return []

    eligible = eligible_items(inventory)
    # Resolve story assets against eligible inventory by digest/identifier.
    by_digest = {i.digest: i for i in eligible if i.digest}
    by_identifier = {i.identifier: i for i in eligible if i.identifier}

    chosen_items: List[InventoryItem] = []
    seen_digests: Set[str] = set()
    seen_titles: Set[str] = set()
    for asset in story.assets:
        item = None
        if asset.digest and asset.digest in by_digest:
            item = by_digest[asset.digest]
        elif asset.identifier and asset.identifier in by_identifier:
            item = by_identifier[asset.identifier]
        if item is None:
            # Asset from the winning story is not in the eligible inventory;
            # never silently substitute from another cluster.
            continue
        # Deduplicate by both digest and normalized title to prevent
        # same visual asset appearing in multiple scenes.
        norm_title = _norm(item.title).strip()
        if item.digest in seen_digests or (norm_title and norm_title in seen_titles):
            continue
        if item.digest:
            seen_digests.add(item.digest)
        if norm_title:
            seen_titles.add(norm_title)
        chosen_items.append(item)

    if not chosen_items:
        return []

    # Select up to target_scenes with chronological and visual variety:
    # prefer a mix of historic and modern periods, ordered by relevance.
    historic = [i for i in chosen_items if _period_of(i) == "historic"]
    modern = [i for i in chosen_items if _period_of(i) == "modern"]
    other = [i for i in chosen_items if _period_of(i) not in ("historic", "modern")]
    ordered: List[InventoryItem] = []
    hi = mi = oi = 0
    while len(ordered) < min(target_scenes, len(chosen_items)):
        added = False
        if hi < len(historic):
            ordered.append(historic[hi]); hi += 1; added = True
        if mi < len(modern) and len(ordered) < min(target_scenes, len(chosen_items)):
            ordered.append(modern[mi]); mi += 1; added = True
        if oi < len(other) and len(ordered) < min(target_scenes, len(chosen_items)):
            ordered.append(other[oi]); oi += 1; added = True
        if not added:
            break

    scenes: List[Dict[str, Any]] = []
    for idx, item in enumerate(ordered, 1):
        image_type = _period_of(item)
        if image_type not in ("historic", "modern"):
            image_type = "modern" if item.clip_score is not None and item.clip_score > 0.25 else "historic"
        terms = list(dict.fromkeys(
            [item.title] + item.categories + item.facets
        ))
        terms = [t for t in terms if t]
        # Guarantee at least 3 search variants so the renderer's scene-plan
        # validation passes; pad from region identity + period, never inventing
        # scene-specific anchors.
        pad = [inventory.region_name, inventory.country, story.theme,
               ("historic" if image_type == "historic" else "modern")]
        for p in pad:
            if len(terms) >= 3:
                break
            if p and p not in terms:
                terms.append(p)
        terms = terms[:5]
        scenes.append({
            "scene_id": f"{inventory.region_slug}:s{idx:02d}",
            "scene_order": idx,
            "title": item.title[:80] or f"Region image {idx}",
            "narration_intent": (f"Show {item.title} ({item.provider}) -- {item.date_text or 'dated material'}"
                                 f" illustrating the {story.theme} narrative."),
            "image_type": image_type,
            "pinned_image": item.image_url,
            "asset_metadata": {
                "title": item.title,
                "author": item.author,
                "license": item.rights,
                "rights": item.rights,
                "rights_statement": item.rights_statement,
                "license_url": item.rights_url,
                "asset_url": item.source_url,
                "source_url": item.source_url,
                "provider": item.provider,
                "identifier": item.identifier,
                "date_text": item.date_text,
                "year_range": item.year_range,
                "dimensions": item.dimensions,
                "categories": item.categories,
                "facets": item.facets,
                "digest": item.digest,
            },
            "required_keywords": terms,
            "image_search_terms": terms,
            "max_year": None,
            "banned_keywords": [],
            "clip_min_score": 0.0,  # already vetted in inventory
        })
    return scenes


def generate_story_narration(story: StoryCandidate, scenes: List[Dict[str, Any]],
                             claims: Optional[List[str]] = None,
                             claim_support: Optional[Dict[str, List[str]]] = None,
                             target_words: int = 130) -> str:
    """Draft a historical narration from the selected story and verified claims.

    The narration tells the historical story suggested by the inventory and
    substantiated by research. It uses images to illustrate the narration
    without falsely claiming an image depicts an event or person it does not.
    Asset credits belong in credits and the description, not repeated throughout
    the spoken narration.

    Length is targeted to 110-155 words so the Kokoro render yields 45-60s.
    """
    claims = claims or list(story.claims)
    claim_support = claim_support or story.claim_support

    # Filter to only verified claims (from research)
    verified_claims = [c for c in claims if claim_support.get(c)]
    # Remove duplicates and generic filler claims
    seen = set()
    filtered_claims = []
    for c in verified_claims:
        c_norm = c.lower().strip()
        if c_norm in seen:
            continue
        # Skip generic filler patterns
        if any(phrase in c_norm for phrase in [
            "this is the story of",
            "surviving material shows",
            "across generations",
            "rich history waiting to be discovered",
        ]):
            continue
        seen.add(c_norm)
        filtered_claims.append(c)

    lines: List[str] = []
    # Hook: introduce the region and the historical arc from verified claims.
    if filtered_claims:
        # Use the specific story title, not "theme story"
        lines.append(f"Before {story.title} became what it is today, {filtered_claims[0].lower()}")
        # Body: each subsequent verified claim builds the narrative arc.
        for claim in filtered_claims[1:1 + max(0, len(scenes) - 1)]:
            lines.append(claim)
    else:
        # No verified claims yet: generate research-anchored narration from inventory metadata.
        # This provides specific factual hooks (dates, names, titles) for the research system
        # to investigate, without asserting unverified claims.
        # Extract unique dates, artist names, and titles from story assets.
        dates = sorted({a.date_text for a in story.assets if a.date_text})
        authors = sorted({a.author for a in story.assets if a.author})
        titles = sorted({a.title for a in story.assets if a.title})
        
        # Build a narration that describes what the inventory contains,
        # framed as research questions rather than assertions.
        if authors:
            lines.append(f"Records from {story.assets[0].provider if story.assets else 'archives'} document {story.title.lower()}, "
                         f"featuring works by {', '.join(authors[:3])}.")
        else:
            lines.append(f"Historical records from {story.assets[0].provider if story.assets else 'archives'} document {story.title.lower()}.")
        
        if dates:
            date_str = ", ".join(dates[:5])
            lines.append(f"These materials span {date_str}.")
        
        if titles:
            title_samples = ", ".join(titles[:3])
            lines.append(f"Key records include {title_samples}.")
        
        # Mention the visual evidence without asserting specific depictions.
        if scenes:
            asset = scenes[0].get("asset_metadata", {})
            lines.append(
                f"A record titled \"{asset.get('title', 'Untitled')}\" "
                f"from {asset.get('provider', 'an archive')} illustrates this period."
            )

    closing = "Every frame is a genuine historical record."
    lines.append(closing)

    narration = " ".join(lines)
    # Trim to target window (sentence-boundary aware).
    words = narration.split()
    if len(words) > target_words + 20:
        narration = " ".join(words[:target_words]) + "."
    return narration
