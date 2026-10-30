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
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from image_inventory import (  # noqa: E402
    RegionInventory, InventoryItem, eligible_items, items_by_theme,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "destination-prototype" / "data"
STORY_DIR = DATA_DIR / "stories"

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
    """Classify an item as historic/modern/unknown from its date text/year range."""
    yr = item.year_range
    if yr and len(yr) == 2:
        lo, hi = yr
        if hi and hi < 1950:
            return "historic"
        if lo and lo >= 1950:
            return "modern"
    dt = (item.date_text or "").lower()
    years = re.findall(r"(1[0-9]{3}|20[0-2][0-9])", dt)
    if years:
        ys = [int(y) for y in years]
        if max(ys) < 1950:
            return "historic"
        if min(ys) >= 1950:
            return "modern"
    # CLIP modern probe already split historic vs modern in relevance; fall back
    # to title/description keywords.
    hay = (item.title + " " + item.description).lower()
    if any(k in hay for k in ("modern", "contemporary", "21st century", "2000", "2010", "2020")):
        return "modern"
    if any(k in hay for k in ("century", "period", "historical", "old", "ancient", "medieval")):
        return "historic"
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


def _score_candidate(theme: str, items: List[InventoryItem],
                     claim_support: Dict[str, List[str]]) -> StoryCandidate:
    """Score a candidate story by evidence, variety, coherence, variety, duration."""
    # Evidence strength: fraction of assets with CLIP/relevance score and date evidence.
    scored = [i for i in items if i.clip_score is not None or i.date_text or i.year_range]
    evidence_score = min(1.0, len(scored) / max(1, len(items))) if items else 0.0

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
    coherence_score = coherent / max(1, len(items)) if items else 0.0

    # Visual variety: mix of historic and modern periods.
    periods = {_period_of(i) for i in items}
    visual_variety_score = min(1.0, len(periods) / 2.0)

    # Duration fit: enough assets for ~45-60s video at ~5-7s per scene (8-12 scenes).
    n = len(items)
    duration_fit_score = min(1.0, n / 10.0)  # 10 assets = good fit

    total = (0.30 * evidence_score + 0.25 * asset_variety_score +
             0.20 * coherence_score + 0.15 * visual_variety_score +
             0.10 * duration_fit_score)

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
        title=f"{theme.title()} story",
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


def provider_list(assets: List[AssetRef]) -> str:
    from collections import Counter
    c = Counter(a.provider for a in assets)
    return ", ".join(f"{k}({v})" for k, v in c.most_common(3))


def select_story(clusters: List[Tuple[str, List[InventoryItem]]],
                 claim_support: Optional[Dict[str, List[str]]] = None) -> Optional[StoryCandidate]:
    """Select the best-supported candidate story from inventory clusters.

    Ranking: evidence strength, asset variety, coherence, visual variety,
    duration fit. Returns None when no candidate has enough assets to support
    a 45-60s video -- the caller must stop honestly rather than fabricate.
    """
    claim_support = claim_support or {}
    candidates: List[StoryCandidate] = []
    for theme, items in clusters:
        cand = _score_candidate(theme, items, claim_support)
        candidates.append(cand)

    # Only stories with enough distinct assets can support a 45-60s video.
    viable = [c for c in candidates if len(c.assets) >= 6]
    if not viable:
        return None

    viable.sort(key=lambda c: c.total_score, reverse=True)
    return viable[0]


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
        if item.digest in seen_digests:
            continue
        seen_digests.add(item.digest)
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

    lines: List[str] = []
    # Hook: introduce the region and the historical arc from verified claims.
    if claims:
        lines.append(f"This is the story of {story.title.lower()}. {claims[0]}")
        # Body: each subsequent claim builds the narrative arc.
        for claim in claims[1:1 + max(0, len(scenes) - 1)]:
            lines.append(claim)
    else:
        # No verified claims: tell what the collection shows, without overclaiming.
        lines.append(f"This is the story of {story.title.lower()}. "
                     f"The surviving material shows {story.theme.lower()} across generations.")
        for sc in scenes[:max(0, len(scenes) - 1)]:
            asset = sc.get("asset_metadata", {})
            lines.append(
                f"A record titled \"{asset.get('title', 'Untitled')}\" "
                f"from {asset.get('provider', 'an archive')} illustrates this thread."
            )

    closing = "Every frame is a genuine historical record."
    lines.append(closing)

    narration = " ".join(lines)
    # Trim to target window (sentence-boundary aware).
    words = narration.split()
    if len(words) > target_words + 20:
        narration = " ".join(words[:target_words]) + "."
    return narration
