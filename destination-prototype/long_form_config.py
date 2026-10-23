"""Validation helpers for the isolated long-form history pilot.

This module deliberately does not import or modify the short renderer.
"""
from __future__ import annotations

import re
from typing import Any


PILOT_MIN_WORDS = 350
PILOT_MAX_WORDS = 500
PILOT_MIN_SCENES = 18
PILOT_MAX_SCENES = 24
PILOT_MIN_SECONDS = 165
PILOT_MAX_SECONDS = 195


class LongFormConfigError(ValueError):
    """Raised when a long-form config is unsafe or structurally incomplete."""


def _words(text: str) -> list[str]:
    return re.findall(r"\b[\w’'-]+\b", text or "", flags=re.UNICODE)


def validate_long_form_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate the 3-minute pilot's story, chapters, sources, and visual beats."""
    if not isinstance(cfg, dict):
        raise LongFormConfigError("Config must be a mapping")
    if not cfg.get("name") or not cfg.get("country"):
        raise LongFormConfigError("Config needs name and country")

    story = cfg.get("story")
    if not isinstance(story, dict):
        raise LongFormConfigError("Config needs a story mapping")
    narration = str(story.get("narration", "")).strip()
    words = _words(narration)
    if not PILOT_MIN_WORDS <= len(words) <= PILOT_MAX_WORDS:
        raise LongFormConfigError(
            f"Pilot narration must contain {PILOT_MIN_WORDS}–{PILOT_MAX_WORDS} words; got {len(words)}"
        )
    lowered = narration.lower()
    if any(token in lowered for token in ("[review]", "[draft", "placeholder", "archival asset pending")):
        raise LongFormConfigError("Narration contains a draft or placeholder marker")

    chapters = cfg.get("chapters")
    if not isinstance(chapters, list) or not 4 <= len(chapters) <= 5:
        raise LongFormConfigError("Pilot needs 4–5 ordered chapters")
    chapter_titles = [str(c.get("title", "")).strip() for c in chapters if isinstance(c, dict)]
    if len(chapter_titles) != len(chapters) or any(not title for title in chapter_titles):
        raise LongFormConfigError("Every chapter needs a title")
    if len(set(chapter_titles)) != len(chapter_titles):
        raise LongFormConfigError("Chapter titles must be unique")
    if any(not str(c.get("narration", "")).strip() for c in chapters):
        raise LongFormConfigError("Every chapter needs narration")
    joined = " ".join(str(c["narration"]).strip() for c in chapters)
    if re.sub(r"\s+", " ", joined) != re.sub(r"\s+", " ", narration):
        raise LongFormConfigError("Chapter narration must concatenate to the full story in order")

    claims = cfg.get("claim_sources")
    if not isinstance(claims, dict) or len(claims) < 5:
        raise LongFormConfigError("At least five factual claims need source mappings")
    for claim, urls in claims.items():
        if not str(claim).strip() or not isinstance(urls, list) or not urls:
            raise LongFormConfigError(f"Claim has no source mapping: {claim}")
        if any(not isinstance(url, str) or not url.startswith("https://") for url in urls):
            raise LongFormConfigError(f"Claim sources must all be HTTPS URLs: {claim}")

    scenes = cfg.get("scenes")
    if not isinstance(scenes, list) or not PILOT_MIN_SCENES <= len(scenes) <= PILOT_MAX_SCENES:
        raise LongFormConfigError(
            f"Pilot needs {PILOT_MIN_SCENES}–{PILOT_MAX_SCENES} scene beats"
        )
    titles = []
    for scene in scenes:
        if not isinstance(scene, dict):
            raise LongFormConfigError("Every scene must be a mapping")
        title = str(scene.get("title", "")).strip()
        if not title:
            raise LongFormConfigError("Every scene needs a title")
        titles.append(title)
        if len(scene.get("image_search_terms", [])) < 3:
            raise LongFormConfigError(f"Scene '{title}' needs at least three image search variants")
        if not scene.get("required_keywords"):
            raise LongFormConfigError(f"Scene '{title}' needs required_keywords")
        if scene.get("image_type") not in ("historic", "modern", "map"):
            raise LongFormConfigError(f"Scene '{title}' has invalid image_type")
        if not scene.get("narration_intent"):
            raise LongFormConfigError(f"Scene '{title}' needs narration_intent")
        if not scene.get("chapter") or scene["chapter"] not in chapter_titles:
            raise LongFormConfigError(f"Scene '{title}' must reference a known chapter")
    if len(set(titles)) != len(titles):
        raise LongFormConfigError("Scene titles must be unique")

    sources = cfg.get("sources")
    if not isinstance(sources, list) or not sources or any(
        not isinstance(url, str) or not url.startswith("https://") for url in sources
    ):
        raise LongFormConfigError("Config needs an HTTPS source list")

    return {
        "name": str(cfg["name"]),
        "chapter_count": len(chapters),
        "scene_count": len(scenes),
        "word_count": len(words),
        "pilot_duration_bounds_seconds": [PILOT_MIN_SECONDS, PILOT_MAX_SECONDS],
    }
