#!/usr/bin/env python3
"""Scene planner: turns a selected story into a coherent sequence of renderer-ready scenes.

Each scene carries a narrative purpose, story-specific entities, period constraints,
and multiple targeted image-search queries derived from the story - not from a fixed
region template. Output scenes are compatible with asset_resolver.resolve_scene_image
and render_region_short.get_image.
"""

import json
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any

ROOT = Path(__file__).resolve().parents[1]

# Generic English stopwords used to build short query forms
GENERIC_WORDS = {
    "the","a","an","and","or","but","in","on","at","to","for","of","with","by","from",
    "as","is","was","were","be","been","being","have","has","had","do","does","did",
    "will","would","could","should","may","might","can","must","shall","into","onto",
    "upon","over","under","between","among","through","across","during","before",
    "after","since","until","while","where","when","why","how","who","what","which",
    "that","this","these","those","there","here","then","now","today","tomorrow",
    "yesterday","history","historical","historic","city","town","place","people",
    "person","story","tale","past","present","future","time","day","night","life",
    "death","world","earth","land","sea","air","fire","water","sun","moon","star",
}

@dataclass
class SceneSpec:
    scene_id: str
    scene_order: int
    title: str
    narration_intent: str
    narration_text: str
    image_type: str  # historic, modern, map
    named_entities: List[str] = field(default_factory=list)
    visual_tags: List[str] = field(default_factory=list)
    image_search_terms: List[str] = field(default_factory=list)
    required_keywords: List[str] = field(default_factory=list)
    banned_keywords: List[str] = field(default_factory=list)
    max_year: Optional[int] = None
    min_year: Optional[int] = None
    sources: List[str] = field(default_factory=list)
    claim_refs: List[str] = field(default_factory=list)
    allow_modern_photo: bool = False
    identity_fallback_to_clip: bool = False
    map_lat: Optional[float] = None
    map_lon: Optional[float] = None
    map_label: Optional[str] = None

    def to_dict(self) -> Dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None and v != [] and v != "" and v != False}


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _dedupe(seq: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in seq:
        if x and x not in seen:
            seen.add(x); out.append(x)
    return out


def _short_form(phrase: str) -> str:
    """Take the first 1-3 content words of a phrase as a short query form."""
    words = [w for w in re.findall(r"[\w'-]+", phrase) if w.lower() not in GENERIC_WORDS]
    return " ".join(words[:3]) if words else phrase


def _build_queries(entities: List[str], tags: List[str], search_terms: List[str], limit: int = 6) -> List[str]:
    """Entities first, then visual tags, then search variants plus short forms."""
    raw = list(entities) + list(tags) + list(search_terms)
    out: List[str] = []
    for q in raw:
        q = str(q).strip()
        if q and q not in out:
            out.append(q)
    for q in list(out):
        short = _short_form(q)
        if short and short not in out and len(q.split()) >= 2:
            out.append(short)
    long_q = [q for q in out if len(q.split()) > 3]
    short_q = [q for q in out if len(q.split()) <= 3]
    merged: List[str] = []
    for a, b in zip(short_q + [None] * len(long_q), long_q + [None] * len(short_q)):
        for x in (a, b):
            if x and x not in merged:
                merged.append(x)
    unique: List[str] = []
    for q in merged:
        norm = re.sub(r"\s+", " ", q.lower()).strip()
        if norm and not any(norm == re.sub(r"\s+", " ", x.lower()).strip() or
                            (len(norm.split()) >= 2 and norm in re.sub(r"\s+", " ", x.lower()).strip())
                            for x in unique):
            unique.append(q)
    return unique[:limit]


def scene_dict(spec: SceneSpec) -> Dict:
    """Convert SceneSpec to a renderer/resolver-compatible scene dict."""
    return {
        "scene_id": spec.scene_id,
        "title": spec.title,
        "narration_intent": spec.narration_intent,
        "narration_text": spec.narration_text,
        "image_type": spec.image_type,
        "named_entities": spec.named_entities,
        "visual_tags": spec.visual_tags,
        "image_search_terms": spec.image_search_terms,
        "required_keywords": spec.required_keywords,
        "banned_keywords": spec.banned_keywords,
        "max_year": spec.max_year,
        "min_year": spec.min_year,
        "image_search_queries": _build_queries(spec.named_entities, spec.visual_tags, spec.image_search_terms),
        "allow_modern_photo": spec.allow_modern_photo,
        "identity_fallback_to_clip": spec.identity_fallback_to_clip,
        "map_lat": spec.map_lat,
        "map_lon": spec.map_lon,
        "map_label": spec.map_label,
    }


def _category_for(topic: Dict, dest_cfg: Dict) -> str:
    """Extract the story category from the topic (selected story), falling back to config."""
    # The selected story's category takes precedence
    topic_cat = topic.get("category", "")
    if topic_cat:
        return topic_cat
    story = dest_cfg.get("story", {})
    if isinstance(story, dict):
        return story.get("category", "hidden_history")
    return "hidden_history"


def _entities_from(topic: Dict) -> List[str]:
    """Pull named entities from the topic/story."""
    ents = list(topic.get("entities", []) or [])
    # Also pull from entities list if it's a string
    ents = [e for e in ents if isinstance(e, str)]
    return ents


def plan_scenes(topic: Dict, dest_cfg: Dict) -> List[Dict]:
    """Generate a coherent scene sequence from a selected story.

    The story determines:
      - title, hook, description/thesis
      - named entities (people, places, events, artifacts)
      - category (hidden_history, culture_traditions, etc.)
      - sources / claim references
      - date range hints

    Returns a list of scene dicts compatible with asset_resolver and the renderer.
    """
    destination = topic.get("destination_name", topic.get("destination", ""))
    dest_slug = topic.get("destination_slug", "")
    category = _category_for(topic, dest_cfg)
    entities = _entities_from(topic)
    hooks = topic.get("hook", "")
    description = topic.get("description", topic.get("narration", ""))
    sources = topic.get("sources", dest_cfg.get("sources", []) or [])
    claim_refs = list(topic.get("claim_refs", []) or [])

    story_id = topic.get("story_id", topic.get("angle_id", f"{dest_slug}:story"))
    story_title = topic.get("title", f"History of {destination}")

    # Build category-specific scene templates
    scenes: List[SceneSpec] = []

    if category == "culture_traditions":
        scenes.extend(_culture_traditions_scenes(destination, dest_slug, story_id, entities, description, sources, claim_refs))
    elif category in ("founding_political", "independence_colonial", "wars_revolutions"):
        scenes.extend(_political_turning_scenes(destination, dest_slug, story_id, entities, description, sources, claim_refs))
    elif category in ("ancient_civilization", "archaeology"):
        scenes.extend(_ancient_civilization_scenes(destination, dest_slug, story_id, entities, description, sources, claim_refs))
    elif category in ("urban_transformation", "trade_migration"):
        scenes.extend(_urban_growth_scenes(destination, dest_slug, story_id, entities, description, sources, claim_refs))
    elif category in ("people_communities", "historical_people"):
        scenes.extend(_people_story_scenes(destination, dest_slug, story_id, entities, description, sources, claim_refs))
    elif category in ("architecture_engineering", "wonders"):
        scenes.extend(_architecture_scenes(destination, dest_slug, story_id, entities, description, sources, claim_refs))
    elif category in ("music_film_entertainment", "popular_culture"):
        scenes.extend(_culture_pop_scenes(destination, dest_slug, story_id, entities, description, sources, claim_refs))
    elif category in ("disasters_resilience", "survival_recovery"):
        scenes.extend(_disaster_scenes(destination, dest_slug, story_id, entities, description, sources, claim_refs))
    elif category in ("surprising_lesser_known", "hidden_history"):
        scenes.extend(_hidden_history_scenes(destination, dest_slug, story_id, entities, description, sources, claim_refs))
    else:
        scenes.extend(_generic_story_scenes(destination, dest_slug, story_id, category, entities, description, sources, claim_refs))

    # Ensure 8-12 scenes
    if len(scenes) < 8:
        scenes.extend(_filler_scenes(destination, dest_slug, story_id, entities, len(scenes), sources, claim_refs))
    scenes = scenes[:12]

    # Map scene: pick a geographic anchor if available
    map_scene_idx = next((i for i, s in enumerate(scenes) if s.get("image_type") == "map"), None)
    if map_scene_idx is None and scenes:
        # Insert a map as scene 2
        map_spec = SceneSpec(
            scene_id=f"{story_id}:map",
            scene_order=2,
            title=f"{destination} on the Map",
            narration_intent="Show where this story unfolds geographically.",
            narration_text=f"{destination} is the stage for this story.",
            image_type="map",
            named_entities=[destination],
            visual_tags=[destination, "map", "geography", "region"],
            image_search_terms=_ensure_min_terms([f"{destination} map", f"{destination} geography"]),
            required_keywords=[destination],
            banned_keywords=["portrait", "banner", "poster", "event", "flyer"],
            sources=sources[:2],
            claim_refs=claim_refs[:1],
            map_lat=dest_cfg.get("map", {}).get("center_lat"),
            map_lon=dest_cfg.get("map", {}).get("center_lon"),
            map_label=destination,
        )
        scenes.insert(1, scene_dict(map_spec))
        # Renumber orders
        for i, s in enumerate(scenes):
            s["scene_order"] = i + 1

    return scenes


# ---------------------------------------------------------------------------
# Category-specific scene builders
# ---------------------------------------------------------------------------

def _ensure_min_terms(terms: List[str], min_n: int = 3, fallback: str = "") -> List[str]:
    """Guarantee at least min_n search terms by padding with fallbacks."""
    out = _dedupe(terms)
    while len(out) < min_n:
        out.append(fallback or out[0] if out else "history")
    return out


def _historic_scene(story_id: str, order: int, title: str, intent: str, text: str,
                    entities: List[str], tags: List[str], search_terms: List[str],
                    keywords: List[str], banned: List[str], max_year: Optional[int],
                    sources: List[str], claims: List[str], modern_ok: bool = False,
                    clip_fallback: bool = False) -> Dict:
    return scene_dict(SceneSpec(
        scene_id=f"{story_id}:s{order:02d}",
        scene_order=order,
        title=title,
        narration_intent=intent,
        narration_text=text,
        image_type="historic",
        named_entities=_dedupe(entities),
        visual_tags=_dedupe(tags),
        image_search_terms=_ensure_min_terms(search_terms),
        required_keywords=_dedupe(keywords),
        banned_keywords=_dedupe(banned),
        max_year=max_year,
        sources=sources[:3],
        claim_refs=claims[:2],
        allow_modern_photo=modern_ok,
        identity_fallback_to_clip=clip_fallback,
    ))


def _modern_scene(story_id: str, order: int, title: str, intent: str, text: str,
                  entities: List[str], tags: List[str], search_terms: List[str],
                  keywords: List[str], banned: List[str],
                  sources: List[str], claims: List[str]) -> Dict:
    return scene_dict(SceneSpec(
        scene_id=f"{story_id}:s{order:02d}",
        scene_order=order,
        title=title,
        narration_intent=intent,
        narration_text=text,
        image_type="modern",
        named_entities=_dedupe(entities),
        visual_tags=_dedupe(tags),
        image_search_terms=_ensure_min_terms(search_terms),
        required_keywords=_dedupe(keywords),
        banned_keywords=_dedupe(banned),
        sources=sources[:3],
        claim_refs=claims[:2],
    ))


def _culture_traditions_scenes(destination, dest_slug, story_id, entities, description, sources, claims):
    """Scene sequence for a cultural tradition story."""
    main_entity = entities[0] if entities else destination
    return [
        _historic_scene(story_id, 1, f"{destination}: The Tradition Today",
            "Open with the living practice as people experience it now.",
            f"Today, {destination} keeps {main_entity} alive in ways visitors can see.",
            [main_entity], [destination, "celebration", "tradition", "culture"],
            [f"{destination} {main_entity} today", f"{main_entity} celebration"],
            [main_entity, "celebration", "tradition"], ["poster", "banner", "event flyer"],
            None, sources, claims),
        _historic_scene(story_id, 2, f"Origins of {main_entity}",
            "Trace the practice back to its earliest documented roots.",
            f"The roots of {main_entity} reach back centuries, before modern borders.",
            entities[:3], ["ancient", "origin", "artifact", "tradition", "heritage"],
            [f"{main_entity} origin history", f"{destination} ancient tradition"],
            entities[:2], ["modern", "poster", "banner", "event"],
            1900, sources, claims),
        _historic_scene(story_id, 3, f"Colonial Encounter and {main_entity}",
            "Show how outside contact changed or merged with the tradition.",
            f"Colonial contact reshaped {main_entity}, blending old and new meanings.",
            entities[:3], ["colonial", "encounter", "meeting", "cultural exchange"],
            [f"{main_entity} colonial history", f"{destination} colonial encounter"],
            entities[:2], ["modern", "poster", "banner"],
            1950, sources, claims),
        _historic_scene(story_id, 4, f"{main_entity} Through the 19th Century",
            "Show the practice as it existed before photography became common.",
            f"By the 1800s, {main_entity} had taken forms still recognizable today.",
            entities[:3], ["19th century", "historic", "document", "engraving", "painting"],
            [f"{main_entity} 19th century", f"{destination} historic tradition"],
            entities[:2], ["modern photo", "poster", "banner"],
            1900, sources, claims),
        _modern_scene(story_id, 5, f"{main_entity} in Living Memory",
            "Show the tradition as practiced in recent decades.",
            f"Photographs from the last century show {main_entity} still thriving.",
            entities[:3], ["modern celebration", "photograph", "community", "festival"],
            [f"{main_entity} 20th century photograph", f"{destination} {main_entity} modern"],
            entities[:2], ["painting", "engraving", "illustration", "poster"],
            sources, claims),
        _modern_scene(story_id, 6, f"{main_entity} Today",
            "Show the practice as it exists now.",
            f"Today, {main_entity} continues in {destination}, passed hand to hand.",
            entities[:3], ["modern photograph", "contemporary", "celebration", "community"],
            [f"{main_entity} today photograph", f"{destination} contemporary tradition"],
            entities[:2], ["painting", "engraving", "historic illustration"],
            sources, claims),
        _modern_scene(story_id, 7, f"The Meaning of {main_entity} Now",
            "Close with why the tradition matters to the city's identity.",
            f"{main_entity} remains a living thread through {destination}'s story.",
            entities[:3], ["community", "identity", "heritage", "modern photograph"],
            [f"{destination} cultural identity today", f"{main_entity} meaning today"],
            entities[:2], ["painting", "engraving", "historic illustration"],
            sources, claims),
    ]


def _political_turning_scenes(destination, dest_slug, story_id, entities, description, sources, claims):
    """Scene sequence for political turning points (independence, revolution, founding)."""
    main_entity = entities[0] if entities else destination
    return [
        _historic_scene(story_id, 1, f"{destination} Before the Turning Point",
            "Establish the status quo that the story disrupts.",
            f"Before the change, {destination} lived under the old order.",
            entities[:3], ["historic", "city view", "colonial", "establishment"],
            [f"{destination} before {main_entity}", f"{destination} historic city"],
            entities[:2], ["modern", "poster", "banner", "event"],
            1900, sources, claims),
        _historic_scene(story_id, 2, f"The Spark: {main_entity}",
            "Introduce the central person, event, or idea.",
            f"{main_entity} stood at the center of what came next.",
            entities[:3], ["portrait", "person", "leader", "figure", "historic"],
            [f"{main_entity} portrait historic", f"{main_entity} historical figure"],
            entities[:2], ["modern", "poster", "banner", "cartoon"],
            None, sources, claims, clip_fallback=True),
        _historic_scene(story_id, 3, f"The Moment of Change",
            "Show the event itself or its immediate setting.",
            f"The turning point unfolded in {destination}'s own streets and squares.",
            entities[:3], ["square", "plaza", "gathering", "crowd", "historic"],
            [f"{destination} historic square", f"{main_entity} event location"],
            entities[:2], ["modern", "poster", "banner"],
            None, sources, claims, clip_fallback=True),
        _historic_scene(story_id, 4, f"Resistance and Response",
            "Show how people met the change - opposition, support, adaptation.",
            f"Not everyone welcomed the change; {destination} was divided.",
            entities[:3], ["protest", "meeting", "document", "newspaper", "historic"],
            [f"{destination} resistance history", f"{main_entity} opposition"],
            entities[:2], ["modern", "poster", "banner"],
            None, sources, claims),
        _historic_scene(story_id, 5, f"The New Order Takes Shape",
            "Show the result of the change in institutions and daily life.",
            f"The old symbols came down; new ones went up across {destination}.",
            entities[:3], ["building", "institution", "government", "historic"],
            [f"{destination} new government building", f"{main_entity} aftermath"],
            entities[:2], ["modern", "poster", "banner"],
            1950, sources, claims),
        _modern_scene(story_id, 6, f"{destination} Remembers",
            "Show memorials, plaques, or preserved sites from the era.",
            f"Today, {destination} marks this chapter with plaques and preserved places.",
            entities[:3], ["memorial", "plaque", "museum", "modern photograph"],
            [f"{destination} memorial {main_entity}", f"{destination} historic site today"],
            entities[:2], ["painting", "engraving", "illustration"],
            sources, claims),
        _modern_scene(story_id, 7, f"The Legacy Lives",
            "Close with how the turning point shaped today's city.",
            f"The story of {main_entity} is still written in {destination}'s streets.",
            entities[:3], ["modern photograph", "street", "cityscape", "contemporary"],
            [f"{destination} modern cityscape", f"{main_entity} legacy today"],
            entities[:2], ["painting", "engraving", "historic illustration"],
            sources, claims),
    ]


def _ancient_civilization_scenes(destination, dest_slug, story_id, entities, description, sources, claims):
    """Scene sequence for ancient civilization / archaeology stories."""
    main_entity = entities[0] if entities else destination
    return [
        _historic_scene(story_id, 1, f"Before the City: {destination}'s Ancient Setting",
            "Show the landscape and first settlement.",
            f"Long before modern {destination}, people lived on this land.",
            entities[:3], ["landscape", "settlement", "ancient", "archaeology"],
            [f"{destination} ancient settlement", f"{main_entity} early history"],
            entities[:2], ["modern city", "poster", "banner", "photograph"],
            1500, sources, claims),
        _historic_scene(story_id, 2, f"The Rise of {main_entity}",
            "Show the civilization at its height.",
            f"{main_entity} grew into a center of power and culture.",
            entities[:3], ["temple", "palace", "monument", "civilization", "historic"],
            [f"{main_entity} ancient city", f"{destination} archaeological site"],
            entities[:2], ["modern", "poster", "banner", "photograph"],
            1500, sources, claims),
        _historic_scene(story_id, 3, f"Daily Life in Ancient {destination}",
            "Show artifacts, tools, and scenes of ordinary life.",
            f"Ordinary people built lives around {main_entity}'s temples and markets.",
            entities[:3], ["artifact", "pottery", "tool", "daily life", "historic"],
            [f"{main_entity} artifact museum", f"{destination} ancient daily life"],
            entities[:2], ["modern", "poster", "banner", "photograph"],
            1500, sources, claims),
        _historic_scene(story_id, 4, f"Sacred Space and {main_entity}",
            "Show religious or ceremonial structures.",
            f"The sacred center of {main_entity} drew people from across the region.",
            entities[:3], ["temple", "shrine", "ceremonial", "sacred", "historic"],
            [f"{main_entity} temple historic", f"{destination} sacred site"],
            entities[:2], ["modern", "poster", "banner", "photograph"],
            1500, sources, claims),
        _historic_scene(story_id, 5, f"Decline and Abandonment",
            "Show the civilization's end or transformation.",
            f"Empires rise and fall; {main_entity} was no exception.",
            entities[:3], ["ruin", "abandoned", "historic", "decline"],
            [f"{main_entity} ruins historic", f"{destination} civilization decline"],
            entities[:2], ["modern", "poster", "banner", "photograph"],
            1500, sources, claims),
        _modern_scene(story_id, 6, f"Rediscovering {main_entity}",
            "Show archaeological excavation and modern rediscovery.",
            f"Modern archaeology brought {main_entity} back into view.",
            entities[:3], ["excavation", "archaeology", "dig", "modern photograph"],
            [f"{main_entity} excavation photograph", f"{destination} archaeological dig"],
            entities[:2], ["painting", "engraving", "illustration"],
            sources, claims),
        _modern_scene(story_id, 7, f"{main_entity} Today",
            "Show the site as visitors encounter it now.",
            f"Today, {main_entity} stands as a link to {destination}'s deepest past.",
            entities[:3], ["ruin today", "historic site", "modern photograph", "visitor"],
            [f"{main_entity} today photograph", f"{destination} archaeological site today"],
            entities[:2], ["painting", "engraving", "illustration"],
            sources, claims),
    ]


def _urban_growth_scenes(destination, dest_slug, story_id, entities, description, sources, claims):
    """Scene sequence for urban transformation / trade / migration stories."""
    main_entity = entities[0] if entities else destination
    return [
        _historic_scene(story_id, 1, f"{destination} as a Trading Post",
            "Show the city's early commercial role.",
            f"{destination} began as a meeting point for trade and travelers.",
            entities[:3], ["market", "port", "trading post", "historic"],
            [f"{destination} historic market", f"{main_entity} early trade"],
            entities[:2], ["modern skyscraper", "poster", "banner", "photograph"],
            1900, sources, claims),
        _historic_scene(story_id, 2, f"Migration Shapes {destination}",
            "Show the people who came and built the city.",
            f"Waves of migrants brought new languages, foods, and skills.",
            entities[:3], ["migration", "arrival", "community", "neighborhood", "historic"],
            [f"{destination} migration history", f"{main_entity} immigrant community"],
            entities[:2], ["modern", "poster", "banner", "photograph"],
            1950, sources, claims),
        _historic_scene(story_id, 3, f"The Port and the World",
            "Show the harbor, docks, or trade routes.",
            f"Goods and ideas flowed through {destination}'s gates to the world.",
            entities[:3], ["port", "harbor", "dock", "ship", "trade", "historic"],
            [f"{destination} historic port", f"{main_entity} trading harbor"],
            entities[:2], ["modern", "poster", "banner", "photograph"],
            1950, sources, claims),
        _historic_scene(story_id, 4, f"{destination} Transformed",
            "Show industrialization or rapid growth.",
            f"The city grew fast - new streets, new buildings, new lives.",
            entities[:3], ["industrial", "factory", "railway", "growth", "historic"],
            [f"{destination} industrial history", f"{main_entity} urban growth"],
            entities[:2], ["modern skyscraper", "poster", "banner", "photograph"],
            1950, sources, claims),
        _modern_scene(story_id, 5, f"{destination} at Mid-Century",
            "Show the city as it looked in the 20th century.",
            f"By the 1900s, {destination} had become a modern metropolis.",
            entities[:3], ["mid-century", "cityscape", "modern photograph", "street"],
            [f"{destination} 20th century photograph", f"{main_entity} mid-century"],
            entities[:2], ["painting", "engraving", "illustration"],
            sources, claims),
        _modern_scene(story_id, 6, f"The City Today",
            "Show contemporary {destination}.",
            f"Today's {destination} carries all its layers at once.",
            entities[:3], ["modern photograph", "cityscape", "skyline", "contemporary"],
            [f"{destination} modern photograph", f"{destination} skyline today"],
            entities[:2], ["painting", "engraving", "illustration"],
            sources, claims),
        _modern_scene(story_id, 7, f"Layers of Time",
            "Close with how old and new coexist.",
            f"Every street in {destination} holds centuries of stories.",
            entities[:3], ["layers", "heritage", "modern photograph", "identity"],
            [f"{destination} heritage today", f"{main_entity} layers of time"],
            entities[:2], ["painting", "engraving", "illustration"],
            sources, claims),
    ]


def _people_story_scenes(destination, dest_slug, story_id, entities, description, sources, claims):
    """Scene sequence for a historical person / forgotten community story."""
    person = entities[0] if entities else "a historical figure"
    return [
        _historic_scene(story_id, 1, f"Meet {person}",
            "Introduce the person and why they matter.",
            f"{person} lived and worked in {destination}, leaving a mark still felt.",
            entities[:3], ["portrait", "person", "historic figure", "biography"],
            [f"{person} portrait historic", f"{destination} {person}"],
            entities[:2], ["modern", "poster", "banner", "cartoon"],
            None, sources, claims, clip_fallback=True),
        _historic_scene(story_id, 2, f"{person}'s World in {destination}",
            "Show the city as the person knew it.",
            f"{destination} was the stage for {person}'s life and choices.",
            entities[:3], ["street", "neighborhood", "historic city", "period"],
            [f"{destination} historic street", f"{person} time period city"],
            entities[:2], ["modern", "poster", "banner"],
            None, sources, claims),
        _historic_scene(story_id, 3, f"The Work of {person}",
            "Show what the person created, built, or changed.",
            f"{person}'s work reshaped part of {destination}'s story.",
            entities[:3], ["workshop", "studio", "office", "creation", "historic"],
            [f"{person} work historic", f"{destination} {person} contribution"],
            entities[:2], ["modern", "poster", "banner"],
            None, sources, claims),
        _historic_scene(story_id, 4, f"Opposition and Challenge",
            "Show the obstacles the person faced.",
            f"Not everyone agreed with {person}; the path was contested.",
            entities[:3], ["conflict", "challenge", "document", "historic"],
            [f"{person} challenge history", f"{destination} opposition"],
            entities[:2], ["modern", "poster", "banner"],
            None, sources, claims),
        _historic_scene(story_id, 5, f"The Turning Point",
            "Show the climax of the person's story.",
            f"One moment defined {person}'s place in {destination}'s history.",
            entities[:3], ["event", "gathering", "decisive", "historic"],
            [f"{person} turning point", f"{destination} historic event"],
            entities[:2], ["modern", "poster", "banner"],
            None, sources, claims),
        _modern_scene(story_id, 6, f"Remembering {person}",
            "Show memorials, plaques, or preserved places tied to the person.",
            f"{destination} remembers {person} in stone and story.",
            entities[:3], ["memorial", "plaque", "museum", "modern photograph"],
            [f"{destination} {person} memorial", f"{person} historic site today"],
            entities[:2], ["painting", "engraving", "illustration"],
            sources, claims),
        _modern_scene(story_id, 7, f"{person}'s Legacy",
            "Close with how the person shaped today's city.",
            f"{person}'s story is still part of {destination}'s identity.",
            entities[:3], ["modern photograph", "legacy", "identity", "contemporary"],
            [f"{destination} modern identity", f"{person} legacy today"],
            entities[:2], ["painting", "engraving", "illustration"],
            sources, claims),
    ]


def _architecture_scenes(destination, dest_slug, story_id, entities, description, sources, claims):
    """Scene sequence for architecture / engineering / wonders."""
    landmark = entities[0] if entities else destination
    return [
        _historic_scene(story_id, 1, f"The Vision for {landmark}",
            "Show the idea and planning stage.",
            f"{landmark} began as a vision for {destination}.",
            entities[:3], ["blueprint", "plan", "drawing", "architect", "historic"],
            [f"{landmark} design historic", f"{destination} architecture vision"],
            entities[:2], ["modern photograph", "poster", "banner"],
            None, sources, claims, clip_fallback=True),
        _historic_scene(story_id, 2, f"Building {landmark}",
            "Show construction and craftsmanship.",
            f"Workers raised {landmark} stone by stone, by hand.",
            entities[:3], ["construction", "building", "craft", "historic"],
            [f"{landmark} construction historic", f"{destination} building history"],
            entities[:2], ["modern photograph", "poster", "banner"],
            None, sources, claims),
        _historic_scene(story_id, 3, f"{landmark} Completed",
            "Show the finished work as it first appeared.",
            f"When {landmark} opened, it changed {destination}'s skyline.",
            entities[:3], ["completed", "opening", "inauguration", "historic"],
            [f"{landmark} completed historic", f"{destination} landmark opening"],
            entities[:2], ["modern photograph", "poster", "banner"],
            None, sources, claims),
        _historic_scene(story_id, 4, f"{landmark} in Use",
            "Show the building serving its purpose.",
            f"People worked, worshipped, or gathered inside {landmark}.",
            entities[:3], ["interior", "use", "people", "historic"],
            [f"{landmark} interior historic", f"{destination} landmark in use"],
            entities[:2], ["modern photograph", "poster", "banner"],
            None, sources, claims),
        _historic_scene(story_id, 5, f"Changes and Survival",
            "Show alterations, damage, or preservation efforts.",
            f"Time left its mark on {landmark}; some parts survived, others changed.",
            entities[:3], ["alteration", "preservation", "damage", "historic"],
            [f"{landmark} restoration historic", f"{destination} landmark changes"],
            entities[:2], ["modern photograph", "poster", "banner"],
            1950, sources, claims),
        _modern_scene(story_id, 6, f"{landmark} Today",
            "Show the landmark as visitors see it now.",
            f"Today, {landmark} stands as {destination}'s signature.",
            entities[:3], ["modern photograph", "landmark", "visitor", "contemporary"],
            [f"{landmark} today photograph", f"{destination} landmark today"],
            entities[:2], ["painting", "engraving", "illustration"],
            sources, claims),
        _modern_scene(story_id, 7, f"Why {landmark} Matters",
            "Close with the building's meaning for the city.",
            f"{landmark} is more than stone - it is {destination}'s memory.",
            entities[:3], ["meaning", "identity", "modern photograph", "heritage"],
            [f"{destination} heritage meaning", f"{landmark} significance today"],
            entities[:2], ["painting", "engraving", "illustration"],
            sources, claims),
    ]


def _culture_pop_scenes(destination, dest_slug, story_id, entities, description, sources, claims):
    """Scene sequence for music / film / entertainment / popular culture."""
    subject = entities[0] if entities else destination
    return [
        _historic_scene(story_id, 1, f"{destination}'s Cultural Scene Begins",
            "Show the origins of the tradition.",
            f"{destination}'s {subject} story started long before it was famous.",
            entities[:3], ["origin", "early", "venue", "historic"],
            [f"{destination} {subject} origin", f"{subject} history"],
            entities[:2], ["modern", "poster", "banner", "album cover"],
            1950, sources, claims),
        _historic_scene(story_id, 2, f"The Golden Era of {subject}",
            "Show the peak period.",
            f"The golden age of {subject} filled {destination}'s stages and screens.",
            entities[:3], ["golden era", "stage", "screen", "historic"],
            [f"{subject} golden age", f"{destination} entertainment history"],
            entities[:2], ["modern", "poster", "banner", "album cover"],
            1980, sources, claims),
        _historic_scene(story_id, 3, f"Key Figures of {subject}",
            "Show the artists, musicians, or performers.",
            f"Names from {destination} defined {subject} for the world.",
            entities[:3], ["artist", "performer", "portrait", "historic"],
            [f"{subject} artist portrait", f"{destination} cultural figure"],
            entities[:2], ["modern", "poster", "banner", "album cover"],
            1980, sources, claims),
        _historic_scene(story_id, 4, f"Iconic Venues of {subject}",
            "Show theaters, clubs, or studios.",
            f"Certain rooms in {destination} witnessed history being made.",
            entities[:3], ["theater", "club", "studio", "venue", "historic"],
            [f"{subject} venue historic", f"{destination} entertainment venue"],
            entities[:2], ["modern", "poster", "banner", "album cover"],
            1980, sources, claims),
        _modern_scene(story_id, 5, f"{subject} in Recent Decades",
            "Show the tradition continuing.",
            f"{subject} kept evolving through the late 20th century.",
            entities[:3], ["modern photograph", "concert", "performance", "contemporary"],
            [f"{subject} modern photograph", f"{destination} entertainment today"],
            entities[:2], ["painting", "engraving", "illustration", "album cover"],
            sources, claims),
        _modern_scene(story_id, 6, f"{subject} Today",
            "Show the living practice.",
            f"Today, {destination}'s {subject} still draws crowds.",
            entities[:3], ["modern photograph", "performance", "crowd", "contemporary"],
            [f"{subject} today photograph", f"{destination} culture now"],
            entities[:2], ["painting", "engraving", "illustration", "album cover"],
            sources, claims),
        _modern_scene(story_id, 7, f"The Sound and Image of {destination}",
            "Close with the city's cultural signature.",
            f"{subject} is how the world hears and sees {destination}.",
            entities[:3], ["signature", "identity", "modern photograph", "heritage"],
            [f"{destination} cultural signature", f"{subject} meaning today"],
            entities[:2], ["painting", "engraving", "illustration", "album cover"],
            sources, claims),
    ]


def _disaster_scenes(destination, dest_slug, story_id, entities, description, sources, claims):
    """Scene sequence for disasters / survival / resilience."""
    event = entities[0] if entities else destination
    return [
        _historic_scene(story_id, 1, f"{destination} Before the Disaster",
            "Show the city in its normal state.",
            f"Life in {destination} followed its usual rhythm before {event}.",
            entities[:3], ["city view", "daily life", "normal", "historic"],
            [f"{destination} before {event}", f"{destination} historic city"],
            entities[:2], ["modern", "poster", "banner"],
            None, sources, claims),
        _historic_scene(story_id, 2, f"The Event: {event}",
            "Show the disaster itself or its immediate setting.",
            f"{event} struck {destination} and changed everything.",
            entities[:3], ["disaster", "crisis", "emergency", "historic"],
            [f"{event} historic", f"{destination} disaster"],
            entities[:2], ["modern", "poster", "banner"],
            None, sources, claims),
        _historic_scene(story_id, 3, f"Destruction and Loss",
            "Show the aftermath - damaged buildings, displaced people.",
            f"The city counted its losses in ruins and lives.",
            entities[:3], ["ruin", "damage", "destruction", "historic"],
            [f"{event} aftermath historic", f"{destination} destruction"],
            entities[:2], ["modern", "poster", "banner"],
            None, sources, claims),
        _historic_scene(story_id, 4, f"The First Response",
            "Show rescue, aid, and immediate recovery.",
            f"People helped each other; aid arrived; survival began.",
            entities[:3], ["rescue", "aid", "relief", "historic"],
            [f"{event} rescue historic", f"{destination} recovery"],
            entities[:2], ["modern", "poster", "banner"],
            None, sources, claims),
        _historic_scene(story_id, 5, f"Rebuilding {destination}",
            "Show reconstruction and new construction.",
            f"The city rose again, sometimes in a new form.",
            entities[:3], ["rebuilding", "construction", "renewal", "historic"],
            [f"{destination} rebuilding historic", f"{event} reconstruction"],
            entities[:2], ["modern", "poster", "banner"],
            1950, sources, claims),
        _modern_scene(story_id, 6, f"{destination} Remembers",
            "Show memorials, markers, or preserved ruins.",
            f"Today, {destination} remembers {event} in stone and story.",
            entities[:3], ["memorial", "marker", "museum", "modern photograph"],
            [f"{destination} {event} memorial", f"{event} historic site today"],
            entities[:2], ["painting", "engraving", "illustration"],
            sources, claims),
        _modern_scene(story_id, 7, f"Resilience as Identity",
            "Close with how survival shapes the city today.",
            f"Surviving {event} became part of {destination}'s character.",
            entities[:3], ["resilience", "identity", "modern photograph", "contemporary"],
            [f"{destination} resilience today", f"{event} legacy today"],
            entities[:2], ["painting", "engraving", "illustration"],
            sources, claims),
    ]


def _hidden_history_scenes(destination, dest_slug, story_id, entities, description, sources, claims):
    """Scene sequence for surprising / lesser-known / hidden history."""
    subject = entities[0] if entities else destination
    return [
        _historic_scene(story_id, 1, f"The {destination} You Don't Know",
            "Open with a question that challenges assumptions.",
            f"Most visitors to {destination} miss this part of the story.",
            entities[:3], ["hidden", "lesser known", "surprising", "historic"],
            [f"{destination} hidden history", f"{subject} lesser known"],
            entities[:2], ["modern", "poster", "banner"],
            None, sources, claims, clip_fallback=True),
        _historic_scene(story_id, 2, f"The Forgotten {subject}",
            "Introduce the overlooked person, place, or event.",
            f"{subject} shaped {destination} without getting the credit.",
            entities[:3], ["forgotten", "overlooked", "person", "place", "historic"],
            [f"{subject} forgotten history", f"{destination} overlooked"],
            entities[:2], ["modern", "poster", "banner", "cartoon"],
            None, sources, claims, clip_fallback=True),
        _historic_scene(story_id, 3, f"Evidence of {subject}",
            "Show artifacts, documents, or traces that survive.",
            f"The evidence is there - if you know where to look.",
            entities[:3], ["artifact", "document", "trace", "evidence", "historic"],
            [f"{subject} artifact museum", f"{destination} hidden evidence"],
            entities[:2], ["modern", "poster", "banner"],
            None, sources, claims),
        _historic_scene(story_id, 4, f"Why {subject} Was Hidden",
            "Show the forces that obscured the story.",
            f"Power, politics, or prejudice kept {subject} out of the official record.",
            entities[:3], ["suppression", "omission", "document", "historic"],
            [f"{subject} suppressed history", f"{destination} overlooked story"],
            entities[:2], ["modern", "poster", "banner"],
            None, sources, claims),
        _historic_scene(story_id, 5, f"Rediscovering {subject}",
            "Show modern research, excavation, or documentation.",
            f"New research is bringing {subject} back into view.",
            entities[:3], ["research", "rediscovery", "documentation", "historic"],
            [f"{subject} rediscovered", f"{destination} new research"],
            entities[:2], ["modern", "poster", "banner"],
            1950, sources, claims),
        _modern_scene(story_id, 6, f"{subject} Today",
            "Show where the story can be encountered now.",
            f"Today, {subject} can be found in {destination} - if you look.",
            entities[:3], ["modern photograph", "site", "marker", "contemporary"],
            [f"{subject} today photograph", f"{destination} hidden site today"],
            entities[:2], ["painting", "engraving", "illustration"],
            sources, claims),
        _modern_scene(story_id, 7, f"What {subject} Teaches Us",
            "Close with the lesson for today's city.",
            f"{subject} reminds us that {destination}'s story has many layers.",
            entities[:3], ["lesson", "identity", "modern photograph", "heritage"],
            [f"{destination} heritage lesson", f"{subject} meaning today"],
            entities[:2], ["painting", "engraving", "illustration"],
            sources, claims),
    ]


def _generic_story_scenes(destination, dest_slug, story_id, category, entities, description, sources, claims):
    """Fallback generic story sequence when category is unknown."""
    subject = entities[0] if entities else destination
    return [
        _historic_scene(story_id, 1, f"{destination} and the Story of {subject}",
            "Introduce the destination and central subject.",
            f"{destination}'s history centers on {subject}.",
            entities[:3], [destination, "history", "story", "historic"],
            [f"{destination} {subject} history", f"{subject} story"],
            entities[:2], ["modern", "poster", "banner"],
            None, sources, claims, clip_fallback=True),
        _historic_scene(story_id, 2, f"Early Days of {subject}",
            "Show origins.",
            f"{subject} began in {destination}'s early period.",
            entities[:3], ["origin", "early", "historic"],
            [f"{subject} origin", f"{destination} early history"],
            entities[:2], ["modern", "poster", "banner"],
            1900, sources, claims),
        _historic_scene(story_id, 3, f"{subject} Grows",
            "Show development.",
            f"{subject} expanded and shaped the city.",
            entities[:3], ["growth", "expansion", "historic"],
            [f"{subject} growth", f"{destination} development"],
            entities[:2], ["modern", "poster", "banner"],
            1950, sources, claims),
        _historic_scene(story_id, 4, f"{subject} at a Turning Point",
            "Show a key change.",
            f"A turning point redefined {subject} in {destination}.",
            entities[:3], ["turning point", "change", "historic"],
            [f"{subject} turning point", f"{destination} change"],
            entities[:2], ["modern", "poster", "banner"],
            1950, sources, claims),
        _modern_scene(story_id, 5, f"{subject} in the 20th Century",
            "Show modern period.",
            f"{subject} continued through the 1900s.",
            entities[:3], ["20th century", "modern photograph"],
            [f"{subject} 20th century", f"{destination} modern"],
            entities[:2], ["painting", "engraving", "illustration"],
            sources, claims),
        _modern_scene(story_id, 6, f"{subject} Today",
            "Show present day.",
            f"{subject} remains part of {destination} today.",
            entities[:3], ["modern photograph", "today", "contemporary"],
            [f"{subject} today", f"{destination} contemporary"],
            entities[:2], ["painting", "engraving", "illustration"],
            sources, claims),
        _modern_scene(story_id, 7, f"The Legacy of {subject}",
            "Close with meaning.",
            f"{subject} is part of {destination}'s identity.",
            entities[:3], ["legacy", "identity", "modern photograph"],
            [f"{destination} identity", f"{subject} legacy"],
            entities[:2], ["painting", "engraving", "illustration"],
            sources, claims),
    ]


def _filler_scenes(destination, dest_slug, story_id, entities, current_count, sources, claims):
    """Add filler scenes to reach minimum 8."""
    subject = entities[0] if entities else destination
    fillers = []
    order = current_count + 1
    while len(fillers) + current_count < 8:
        fillers.append(_modern_scene(story_id, order, f"{destination}: {subject} in Context",
            "Show broader context.",
            f"{subject} sits within {destination}'s larger story.",
            entities[:2], ["context", "modern photograph"],
            [f"{destination} context", f"{subject} broader story"],
            entities[:1], ["painting", "engraving", "illustration"],
            sources, claims))
        order += 1
    return fillers


def save_plan(scenes: List[Dict], output_path: Path):
    """Save scene plan to JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(scenes, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    import argparse, yaml
    ap = argparse.ArgumentParser(description="Plan scenes from a selected story")
    ap.add_argument("--topic", required=True, help="Path to topic JSON")
    ap.add_argument("--config", required=True, help="Path to region YAML config")
    ap.add_argument("--output", required=True, help="Output scene plan JSON")
    args = ap.parse_args()

    with open(args.topic, encoding="utf-8") as f:
        topic = json.load(f)
    with open(args.config, encoding="utf-8") as f:
        dest_cfg = yaml.safe_load(f)

    scenes = plan_scenes(topic, dest_cfg)
    save_plan(scenes, Path(args.output))
    print(f"Planned {len(scenes)} scenes for {topic.get('destination_name', '')}")
    for s in scenes:
        print(f"  {s['scene_order']:2d}. {s['title']} ({s['image_type']}) queries={len(s.get('image_search_queries',[]))}")
