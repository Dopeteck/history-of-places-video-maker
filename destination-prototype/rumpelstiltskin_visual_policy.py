"""Strict visual-role policy for the Rumpelstiltskin review.

This module deliberately does not use CLIP scores to establish story identity.
The resolver still owns rights, format, size, OCR, duplicate, period and CLIP
threshold gates; this module only classifies the metadata of candidates that
have already passed those gates.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

STORY_ALIASES = (
    "Rumpelstiltskin",
    "Rumpelstilzchen",
    "Rumpel-Stilts-Kin",
    "Rumpel-Stiltskin",
    "Rumpelstiltzkin",
    "Rumpel-Stilts-Ken",
)

# Edition-specific Commons categories missed by the first review search.
# Commons page membership is useful provenance; pages still have to pass OCR,
# rights, dimensions, duplicate, CLIP and human-review gates.
STORY_COMMONS_CATEGORIES = (
    "Rumpelstilzchen (1812, Grimm)",
    "Grimm's Goblins/Rumpel-Stilts-Kin",
    "Blue Fairy Book/Rumpelstiltzkin",
    "Grimm's Household Tales (Edwardes, Bell)/Rumpel-Stilts-Ken",
)

# Curated placements for specific, visually inspected public-domain illustrations.
# These are source-to-beat hints, never approvals; the review board still shows
# the original image and requires a human to confirm the scene fit.
CURATED_STORY_BEAT_ASSIGNMENTS = {
    "394497": "beat-03-first-bargains",  # MET: Rumpelstiltskin at the spinning wheel with the miller's daughter
    "The Blue Fairy Book, p. 97.jpg": "beat-03-first-bargains",
    "Andrew Lang - The Blue Fairy Book p.116.png": "beat-03-first-bargains",
    "GrimmsGoblins-140-Rumpelstiltskin.jpg": "beat-09-foot-revelation",
    "Rumpelstiltskin at the Spinning-Wheel and the Dwarf MET DP827694": "beat-03-first-bargains",
}

UNRELATED_SUBJECT_MARKERS = (
    "king lear", "queen victoria", "mary queen of scots",
    "madonna", "virgin mary", "jesus", "christ child", "nativity",
    "holy family", "saint", "eagle", "peacock", "owl", "pigeon",
    "falcon", "parrot", "bird", "portrait",
    # Known unrelated Hawaiian royal portrait/photo surfaced by the review.
    "albert kunuiakea", "kamehameha iii", "queen kalama",
    "pacific commercial advertiser",
)
UNSUITABLE_PHOTO_MARKERS = (
    "photograph", "photographic", "photo", "newspaper",
    "albumen print", "gelatin silver print", "cabinet card",
    "daguerreotype", "ambrotype", "carte de visite",
)
UNSUITABLE_OBJECT_MARKERS = (
    "sculpture", "statuette", "statue", "terracotta", "cartouche",
    "museum object", "archaeological object", "artifact", "artefact",
    "brick", "bust", "figurine", "vessel",
)
SYMBOLIC_MIN_CLIP_SCORE = 0.28
SYMBOLIC_MIN_ILLUSTRATION_SCORE = 0.55

# Every group must have at least one metadata match. These are deliberately
# conservative; an empty result is preferable to using unrelated historical art.
EXACT_SCENE_REQUIREMENTS: dict[str, list[list[str]]] = {
    "beat-01-threat": [["king", "king's room", "king threatens"], ["daughter", "miller's daughter"]],
    "beat-02-boast": [["father", "miller"], ["boast", "brag", "claims his daughter can spin"]],
    "beat-03-first-bargains": [["spinning wheel", "spinning-wheel", "spinnrad"], ["dwarf", "little man", "imp"]],
    "beat-04-king-demands": [["king", "könig"], ["straw", "spinning wheel", "spinnrad"]],
    "beat-05-child-pact": [["first child", "child bargain", "promise of the child", "promises her child"]],
    "beat-06-claim": [["queen", "königin"], ["infant", "baby", "newborn", "child"]],
    "beat-07-three-days": [["name", "guess his name", "naming"], ["messenger", "herald", "courier", "bote"]],
    "beat-08-forest-fire": [["forest", "woods", "woodland", "wald"], ["fire", "bonfire", "flames", "feuer"], ["dancing", "dance", "dancing on one leg"]],
    "beat-09-foot-revelation": [["foot", "feet", "fuß"], ["floor", "floorboards", "wooden floor", "boden"]],
}


# Contextual visuals are mood-setting, not literal storyboard evidence.
# A single strong visual anchor is enough; the existing CLIP, rights, resolution,
# OCR and duplicate gates still apply. These terms deliberately include mood,
# setting and objects so a fairytale can use evocative public-domain artwork.
SYMBOLIC_REQUIREMENTS: dict[str, list[list[str]]] = {
    "beat-01-threat": [["straw", "spinning wheel", "spinnrad", "dark chamber", "candlelit room", "castle room", "tower", "king", "throne"]],
    "beat-02-boast": [["miller", "mill", "windmill", "watermill", "grain", "flour", "village", "peasant", "humble cottage"]],
    "beat-03-first-bargains": [["spinning wheel", "spinning-wheel", "spinnrad", "dwarf", "little man", "imp", "gold", "straw", "mysterious stranger"]],
    "beat-04-king-demands": [["king", "könig", "throne", "royal chamber", "castle", "straw", "gold", "spinning wheel"]],
    "beat-05-child-pact": [["cradle", "crib", "baby", "infant", "newborn", "bargain", "contract", "ring", "golden necklace"]],
    "beat-06-claim": [["queen", "königin", "crown", "royal nursery", "cradle", "infant", "baby", "castle chamber"]],
    "beat-07-three-days": [["messenger", "herald", "courier", "forest", "woods", "moonlit path", "castle gate", "lantern"]],
    "beat-08-forest-fire": [["forest", "woods", "woodland", "fire", "bonfire", "flames", "dancing", "moonlight", "night"]],
    "beat-09-foot-revelation": [["foot", "feet", "footprint", "floor", "floorboards", "wooden floor", "fireplace", "cottage", "shadow"]],
}

# Broader alternate phrasings deliberately target open-access storybook art.
# The resolver still enforces rights, image format, resolution, OCR, duplicate,
# fairytale-style and CLIP gates before an item can reach the review board.
SYMBOLIC_SEARCH_TERMS: dict[str, list[str]] = {
    "beat-01-threat": [
        "miller's daughter alone in a dark castle chamber antique fairy tale illustration",
        "king in a shadowy medieval throne room storybook woodcut",
        "girl beside straw and spinning wheel candlelit tower antique engraving",
        "dark castle room frightened maiden vintage fairytale illustration",
    ],
    "beat-02-boast": [
        "poor miller boasting about his daughter antique fairy tale woodcut",
        "old miller and daughter outside a village cottage storybook engraving",
        "medieval peasant miller grain sacks windmill illustrated folk tale",
        "humble village mill at dusk antique storybook illustration",
    ],
    "beat-03-first-bargains": [
        "miller's daughter at spinning wheel with little dwarf antique fairytale illustration",
        "mysterious little man appearing beside a spinning wheel old storybook engraving",
        "spinning wheel straw and gold candlelit cottage vintage woodcut",
        "small goblin in a dark cottage antique fairy tale illustration",
    ],
    "beat-04-king-demands": [
        "medieval king in a royal chamber antique fairy tale woodcut",
        "king seated on a throne in a stone castle storybook engraving",
        "royal chamber piled with straw golden spinning wheel vintage illustration",
        "dark medieval throne room king and courtiers antique print",
    ],
    "beat-05-child-pact": [
        "queen making a dark bargain beside a cradle antique fairy tale illustration",
        "royal cradle and golden necklace old storybook engraving",
        "mother holding a baby in a candlelit medieval chamber vintage illustration",
        "small mysterious figure bargaining with a queen storybook woodcut",
    ],
    "beat-06-claim": [
        "queen holding newborn child in a castle chamber antique fairy tale illustration",
        "medieval queen with baby and crown old storybook engraving",
        "royal nursery cradle candlelight antique illustrated tale",
        "small goblin demanding a child from a queen vintage woodcut",
    ],
    "beat-07-three-days": [
        "messenger riding through a moonlit forest antique storybook engraving",
        "herald with lantern on a dark woodland path vintage fairy tale illustration",
        "queen searching for a mysterious name medieval castle antique print",
        "horseman carrying a message through a night forest old woodcut",
    ],
    "beat-08-forest-fire": [
        "tiny goblin dancing around a bonfire in dark forest antique illustration",
        "small mysterious man dancing beside flames woodland storybook engraving",
        "bonfire in a moonlit forest vintage fairy tale woodcut",
        "dark woodland clearing with fire and dancing figure antique print",
    ],
    "beat-09-foot-revelation": [
        "angry goblin stamping foot through wooden floor antique fairy tale illustration",
        "little man caught in cottage floorboards vintage storybook woodcut",
        "dark cottage room wooden floor and strange shadowy figure antique engraving",
        "mysterious dwarf in a candlelit wooden chamber old fairy tale illustration",
    ],
}

EXACT_SEARCH_TERMS = [
    "Rumpelstiltskin historical storybook illustration",
    "Rumpelstilzchen historical fairy tale illustration",
    "Rumpel-Stilts-Kin Grimm illustration",
    "Rumpelstiltzkin Blue Fairy Book illustration",
    "Rumpel-Stilts-Ken Grimm household tales illustration",
]


def _normalise(value: Any) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _contains(text: str, term: str) -> bool:
    needle = _normalise(term)
    return bool(needle and f" {needle} " in f" {text} ")


def candidate_metadata_text(candidate: dict[str, Any]) -> str:
    visual = candidate.get("visual_metadata") or {}
    values = [
        candidate.get("title", ""),
        candidate.get("description", ""),
        *candidate.get("categories", []),
        *candidate.get("facets", []),
        *visual.get("categories", []),
        *visual.get("facets", []),
        visual.get("date_text", ""),
        visual.get("media_type", ""),
        visual.get("object_type", ""),
        visual.get("medium", ""),
        visual.get("credit", ""),
        visual.get("source", ""),
    ]
    return _normalise(" ".join(str(v) for v in values if v is not None))


def curated_beat_for_candidate(candidate: dict[str, Any]) -> str | None:
    """Return a curated destination for an inspected, story-specific source."""
    visual = candidate.get("visual_metadata") or {}
    source_evidence = " ".join(str(value or "") for value in (
        candidate.get("title"), candidate.get("source_url"),
        candidate.get("identifier"), candidate.get("seed_file_title"),
        visual.get("identifier"),
    ))
    normalised = _normalise(source_evidence)
    for source_hint, assigned_beat in CURATED_STORY_BEAT_ASSIGNMENTS.items():
        if _contains(normalised, source_hint):
            return assigned_beat
    return None


def candidate_primary_metadata_text(candidate: dict[str, Any]) -> str:
    """Use item titles/tags, not incidental words buried in long descriptions."""
    visual = candidate.get("visual_metadata") or {}
    values = [
        candidate.get("title", ""),
        *candidate.get("categories", []),
        *candidate.get("facets", []),
        *visual.get("categories", []),
        *visual.get("facets", []),
        visual.get("medium", ""),
        visual.get("object_type", ""),
    ]
    return _normalise(" ".join(str(v) for v in values if v is not None))


def exact_scene_match_status(candidate: dict[str, Any], beat_id: str) -> tuple[str, list[str]]:
    """Return metadata, curated-source, or CLIP-review placement evidence for this beat."""
    text = candidate_metadata_text(candidate)
    curated_target = curated_beat_for_candidate(candidate)
    if curated_target == beat_id:
        return "curated_story_beat_match", []
    groups = EXACT_SCENE_REQUIREMENTS.get(beat_id, [])
    missing = [
        " / ".join(group)
        for group in groups
        if not any(_contains(text, term) for term in group)
    ]
    return ("metadata_scene_match", []) if not missing else ("story_identity_only_unplaced", missing)


def classify_candidate(candidate: dict[str, Any], beat_id: str, visual_mode: str = "symbolic") -> str:
    """Classify story identity separately from contextual fairytale artwork.

    Exact-story items are grounded in a story title/category. Symbolic items
    must independently pass the resolver's illustration-style and CLIP gates,
    then have a relevant anchor in the item's title or catalogue tags. Long
    descriptions are deliberately excluded from symbolic keyword matching:
    incidental mentions such as "daughter of a king" must not validate a brick,
    photograph, or unrelated object.
    """
    text = candidate_metadata_text(candidate)
    if any(_contains(text, alias) for alias in STORY_ALIASES):
        return "exact_story_illustration"
    if visual_mode == "exact":
        return "unrelated_contextual_art"

    style_score = candidate.get("p_fairytale_illustration")
    if style_score is None:
        checks = candidate.get("automatic_checks") or {}
        if checks.get("fairytale_illustration") != "passed":
            return "unrelated_contextual_art"
    else:
        try:
            if float(style_score) < SYMBOLIC_MIN_ILLUSTRATION_SCORE:
                return "unrelated_contextual_art"
        except (TypeError, ValueError):
            return "unrelated_contextual_art"

    try:
        if float(candidate.get("clip_score") or 0.0) < SYMBOLIC_MIN_CLIP_SCORE:
            return "unrelated_contextual_art"
    except (TypeError, ValueError):
        return "unrelated_contextual_art"

    primary_text = candidate_primary_metadata_text(candidate)
    if any(_contains(primary_text, marker) for marker in UNRELATED_SUBJECT_MARKERS):
        return "unrelated_contextual_art"
    if any(_contains(primary_text, marker) for marker in UNSUITABLE_PHOTO_MARKERS):
        return "unrelated_contextual_art"
    if any(_contains(primary_text, marker) for marker in UNSUITABLE_OBJECT_MARKERS):
        return "unrelated_contextual_art"

    groups = SYMBOLIC_REQUIREMENTS.get(beat_id, [])
    if groups and all(any(_contains(primary_text, term) for term in group) for group in groups):
        return "symbolic_contextual_asset"
    return "unrelated_contextual_art"


def build_review_scene(raw: dict[str, Any], index: int, visual_mode: str, seed_files: list[str] | None = None) -> dict[str, Any]:
    sid = str(raw["id"])
    requirement = raw.get("asset_requirement") or {}
    visual = str(requirement.get("must_show", ""))
    if visual_mode not in ("exact", "symbolic"):
        raise ValueError(f"Unknown visual mode: {visual_mode}")

    if visual_mode == "exact":
        return {
            "id": f"{sid}-exact",
            "title": raw.get("title", sid),
            "image_type": "historic",
            "named_entities": ["Rumpelstiltskin"],
            "identity_aliases": list(STORY_ALIASES[1:]),
            "identity_fallback_to_clip": False,
            "require_fairytale_illustration": True,
            "min_fairytale_illustration_score": 0.5,
            "commons_categories": list(STORY_COMMONS_CATEGORIES),
            "image_search_terms": list(dict.fromkeys([*EXACT_SEARCH_TERMS, *requirement.get("search_terms", [])])),
            "visual_tags": [visual, "antique Rumpelstiltskin fairy-tale book engraving"],
            "visual_caption": f"A public-domain antique illustrated Rumpelstiltskin storybook scene showing {visual}",
            "narration_intent": f"{raw.get('evidence', '')} Tale identity is required in source metadata. Scene: {visual}",
            "required_keywords": [],
            "required_keyword_groups": [],
            "identity_keyword_groups": [],
            "seed_commons_files": list(seed_files or []),
            "search_terms_first": True,
            "max_queries": 8,
            "max_queries_per_provider": 4,
        }

    return {
        "id": f"{sid}-symbolic",
        "title": raw.get("title", sid),
        "image_type": "historic",
        "require_fairytale_illustration": True,
        "min_fairytale_illustration_score": SYMBOLIC_MIN_ILLUSTRATION_SCORE,
        "clip_min_score": SYMBOLIC_MIN_CLIP_SCORE,
        "image_search_terms": list(SYMBOLIC_SEARCH_TERMS.get(sid, [])),
        "search_terms_first": True,
        "visual_tags": [visual, "antique fairy-tale book engraving or woodcut"],
        "visual_caption": f"An antique fairy-tale book illustration supporting {visual}",
        "narration_intent": f"Symbolic contextual asset only; use atmospheric storybook imagery that supports: {visual}",
        "required_keywords": [],
        "required_keyword_groups": SYMBOLIC_REQUIREMENTS.get(sid, []),
        "identity_keyword_groups": [],
        "banned_keywords": [*UNRELATED_SUBJECT_MARKERS, *UNSUITABLE_PHOTO_MARKERS, *UNSUITABLE_OBJECT_MARKERS],
        "max_queries": 8,
        "max_queries_per_provider": 4,
    }


def summarize_visual_coverage(beats: list[dict[str, Any]]) -> dict[str, Any]:
    exact_ids: list[str] = []
    story_review_ids: list[str] = []
    symbolic_ids: list[str] = []
    any_ids: list[str] = []
    uncovered: list[str] = []
    for beat in beats:
        beat_id = str(beat.get("scene_id") or beat.get("id") or "")
        candidates = [
            c for c in beat.get("candidates", [])
            if c.get("review_status") != "rejected_duplicate" and c.get("approved") is False
        ]
        exact_usable = any(
            c.get("visual_role") == "exact_story_illustration"
            and c.get("scene_match_status") in ("metadata_scene_match", "curated_story_beat_match")
            for c in candidates
        )
        story_review = any(
            c.get("visual_role") == "exact_story_illustration"
            and c.get("scene_match_status") == "clip_ranked_human_review"
            for c in candidates
        )
        symbolic_usable = any(c.get("visual_role") == "symbolic_contextual_asset" for c in candidates)
        if exact_usable:
            exact_ids.append(beat_id)
        if story_review:
            story_review_ids.append(beat_id)
        if symbolic_usable:
            symbolic_ids.append(beat_id)
        if exact_usable or story_review or symbolic_usable:
            any_ids.append(beat_id)
        else:
            uncovered.append(beat_id)
    unplaced_exact = [
        c for beat in beats for c in beat.get("candidates", [])
        if c.get("visual_role") == "exact_story_illustration"
        and c.get("scene_match_status") == "story_identity_only_unplaced"
        and c.get("review_status") != "rejected_duplicate"
    ]
    return {
        "story_identified_illustrations_without_verified_beat_placement": len(unplaced_exact),
        "unplaced_story_illustration_titles": list(dict.fromkeys(
            str(c.get("title") or c.get("candidate_title") or "Untitled") for c in unplaced_exact
        )),
        "beats_with_exact_story_illustrations": len(exact_ids),
        "exact_story_illustration_beat_ids": exact_ids,
        "beats_with_story_illustration_candidates_for_human_placement": len(story_review_ids),
        "story_illustration_candidate_beat_ids": story_review_ids,
        "beats_with_suitable_symbolic_assets": len(symbolic_ids),
        "symbolic_asset_beat_ids": symbolic_ids,
        "beats_with_any_automatic_candidates": len(any_ids),
        "beats_lacking_usable_visual": len(uncovered),
        "uncovered_beat_ids": uncovered,
    }
