"""Daily public-domain fairy-tale selection and source-bound episode planning.

Plans are original, clearly disclosed retellings of a named public-domain text edition.
Model output is validated before any paid/quota-limited image generation begins.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SYSTEM = """You are a skilled spoken-story performer and visual episode planner.
Tell ONE supplied public-domain fairy tale as though a gifted human storyteller is speaking
to one listener, not reading an essay, synopsis, school report or list of events.
Open inside the trouble, mystery or decision that makes this particular tale worth hearing.
Make every beat lead naturally to the next: show what a character wants, what they do,
what goes wrong, what choice they make, and what that choice causes.
Write for the ear. Prefer active verbs, concrete actions and everyday spoken phrasing.
Use contractions where they sound natural. Mix short punchy lines with longer flowing ones.
Let suspense breathe with purposeful questions, pauses and occasional direct dialogue.
Give important discoveries and reversals room to land; do not announce them before they happen.
Vary sentence openings and rhythm. Do not march through the plot with repeated "then",
"after that", "suddenly" or "meanwhile". Avoid generic introductions, filler, moralising,
stiff transitions, repetitive explanations and phrases that sound like a written summary.
The narration must be easy to say aloud in one continuous performance.
Dialogue is dramatized retelling, never claim it is a verbatim quotation from the edition.
Follow only the supplied synopsis and ordered key beats. Do not import film adaptations,
modern franchises, invented subplots, or unrelated characters. Do not omit the actual ending.
Use the cited edition as the literary source. Make clear that this is a retelling, not a reading
or reproduction of the source text. Avoid graphic violence, sexual content, and frightening
detail inappropriate for a general audience.
Return one valid JSON object only. No markdown, explanations, or code fences.
"""

def _text(value: Any, field: str, maximum: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f"{field} must be nonempty text of at most {maximum} characters")
    return value.strip()


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not value or len(value) > 72:
        raise ValueError("Story ID must be a short lowercase slug")
    return value


def load_queue(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Fairy-tale queue must use schema_version 1")
    stories = value.get("stories")
    if not isinstance(stories, list) or len(stories) < 8:
        raise ValueError("Fairy-tale queue needs at least eight curated stories")
    seen = set()
    for item in stories:
        if not isinstance(item, dict):
            raise ValueError("Every queue entry must be an object")
        sid = _slug(_text(item.get("id"), "story id", 72))
        if sid in seen:
            raise ValueError(f"Duplicate story ID: {sid}")
        seen.add(sid)
        _text(item.get("title"), "story title", 120)
        source = item.get("source")
        if not isinstance(source, dict):
            raise ValueError(f"{sid}: source edition is required")
        _text(source.get("title"), "source title", 180)
        _text(source.get("author"), "source author", 120)
        url = _text(source.get("url"), "source URL", 500)
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc not in {"www.gutenberg.org", "gutenberg.org"}:
            raise ValueError(f"{sid}: source must be a secure Project Gutenberg URL")
        _text(item.get("synopsis"), "story synopsis", 1800)
        beats = item.get("key_beats")
        if not isinstance(beats, list) or not 5 <= len(beats) <= 10:
            raise ValueError(f"{sid}: expected 5–10 ordered key beats")
        for beat in beats:
            _text(beat, "key beat", 300)
    return stories


def initial_state() -> dict:
    return {"schema_version": 1, "cycle": 1, "completed_story_ids": [],
            "last_story_id": None, "completed": []}


def next_story(stories: list[dict], state: dict) -> tuple[dict, dict]:
    """Select the next unused enabled tale; never silently repeat within a cycle."""
    state = dict(state or initial_state())
    if state.get("schema_version") != 1:
        raise ValueError("Unsupported fairy-tale rotation state")
    completed = set(state.get("completed_story_ids", []))
    enabled = [x for x in stories if x.get("enabled", True)]
    if not enabled:
        raise ValueError("No enabled fairy tales in the queue")
    candidates = [x for x in enabled if x["id"] not in completed]
    if not candidates:
        state["cycle"] = int(state.get("cycle", 1)) + 1
        state["completed_story_ids"] = []
        candidates = list(enabled)
        last = state.get("last_story_id")
        if len(candidates) > 1:
            candidates = [x for x in candidates if x["id"] != last] or candidates
    selected = candidates[0]
    return selected, state


def validate_episode_plan(raw: dict, story: dict | None = None) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Episode plan must be a JSON object")
    title = _text(raw.get("title"), "episode title", 100)
    logline = _text(raw.get("logline"), "episode logline", 360)
    if story is None:
        basis = raw.get("retelling_basis", {})
        source = raw.get("source", {})
        story = {
            "id": raw.get("story_id"),
            "title": basis.get("queue_title", title) if isinstance(basis, dict) else title,
            "synopsis": basis.get("synopsis", logline) if isinstance(basis, dict) else logline,
            "key_beats": basis.get("key_beats", []) if isinstance(basis, dict) else [],
            "source": source,
        }
    story_id = _slug(_text(story.get("id"), "story ID", 72))
    source = story.get("source")
    if not isinstance(source, dict):
        raise ValueError("Episode must retain its named source edition")
    source_title = _text(source.get("title"), "source title", 180)
    source_author = _text(source.get("author"), "source author", 120)
    source_url = _text(source.get("url"), "source URL", 500)
    parsed_source = urlparse(source_url)
    if parsed_source.scheme != "https" or parsed_source.netloc not in {"www.gutenberg.org", "gutenberg.org"}:
        raise ValueError("Episode source must be a secure Project Gutenberg URL")
    edition_note = _text(source.get("edition_note"), "source edition note", 500)
    characters = raw.get("characters")
    if not isinstance(characters, list) or not 2 <= len(characters) <= 5:
        raise ValueError("Episode must define 2–5 recurring characters")
    character_map = {}
    clean_characters = []
    for item in characters:
        cid = _slug(_text(item.get("id"), "character id", 48))
        if cid in character_map:
            raise ValueError("Duplicate character ID")
        name = _text(item.get("name"), "character name", 80)
        identity = _text(item.get("visual_identity"), "character visual identity", 260)
        role = _text(item.get("role"), "character role", 120)
        character_map[cid] = name
        clean_characters.append({"id": cid, "name": name, "role": role,
                                 "visual_identity": identity})
    scenes = raw.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != 8:
        raise ValueError("Daily fairy-tale episodes must contain exactly eight visual scenes")
    clean_scenes = []
    total_words = 0
    for index, item in enumerate(scenes, 1):
        sid = f"scene-{index:02d}"
        scene_title = _text(item.get("title"), f"{sid} title", 90)
        narration = _text(item.get("narration"), f"{sid} narration", 900)
        count = len(narration.split())
        if not 16 <= count <= 42:
            raise ValueError(f"{sid} narration has {count} words; expected 16–42")
        visual = _text(item.get("visual_prompt"), f"{sid} visual prompt", 900)
        ids = item.get("character_ids")
        if not isinstance(ids, list) or not ids or len(ids) > 4 or any(x not in character_map for x in ids):
            raise ValueError(f"{sid} must reference 1–4 defined characters")
        if len(set(ids)) != len(ids):
            raise ValueError(f"{sid} has duplicate character references")
        clean_scenes.append({
            "id": sid, "title": scene_title, "narration": narration,
            "visual_prompt": visual, "character_ids": ids,
            "duration_seconds": 10.0, "type": "still_kenburns",
        })
        total_words += count
    if not 180 <= total_words <= 235:
        raise ValueError(f"Episode narration has {total_words} words; expected 180–235")
    narration = "\n\n".join(x["narration"] for x in clean_scenes)
    return {
        "format": "dark_fairytale_episode_v1",
        "story_id": story_id,
        "title": title,
        "series": "Dark Fairytales",
        "logline": logline,
        "source": {
            "title": source_title, "author": source_author,
            "url": source_url, "edition_note": edition_note,
        },
        "retelling_basis": {
            "queue_title": _text(story.get("title"), "queue story title", 120),
            "synopsis": _text(story.get("synopsis"), "queue synopsis", 1800),
            "key_beats": story.get("key_beats", []),
            "scope": "A new, abridged dramatic retelling based on the named edition; not a verbatim reading.",
        },
        "characters": clean_characters,
        "scenes": clean_scenes,
        "narration": narration,
        "narration_word_count": total_words,
        "target_duration_seconds": 92,
        "visual_style": "Dark European fairy-tale storybook, painterly cinematic realism, dramatic candlelight, rich brush texture, coherent palette, believable anatomy.",
        "narration_style": "One continuous, conversational oral performance; active verbs, natural spoken phrasing, varied rhythm, purposeful suspense, direct dramatized dialogue, and clear cause-and-effect transitions.",
        "disclosure": "A synthetic-voice, AI-illustrated dramatic retelling based on the cited public-domain edition. Visuals are original dramatizations; dialogue is not presented as verbatim source text.",
        "status": "draft_needs_human_review",
        "approved": False, "published": False,
    }


def generate_episode(story: dict, llm) -> dict:
    prompt = {
        "task": "dark_fairytale_episode",
        "story": {
            "title": story["title"], "synopsis": story["synopsis"],
            "ordered_key_beats": story["key_beats"], "source": story["source"],
        },
        "requirements": {
            "scene_count": 8, "total_spoken_words": "180–235",
            "per_scene_words": "16–42",
            "style": "natural spoken oral storytelling; not an encyclopedia summary",
            "order": "chronological, cause-and-effect; include the full resolution",
            "visuals": "one coherent, cinematic 9:16 story illustration for each scene",
            "characters": "2–5 recurring character descriptions, visually distinctive and consistent",
            "guardrails": [
                "Use only the supplied story synopsis and beats; do not add a film adaptation or unrelated subplot.",
                "Do not omit the ending or solve a mystery before the discovery.",
                "Do not claim invented dialogue is a verbatim quotation.",
                "Every visual prompt must depict the action narrated in that same scene.",
                "No words, titles, captions, watermarks or lettering in the image.",
            ],
        },
        "schema": {
            "title": "episode title",
            "logline": "one-sentence premise",
            "characters": [{"id": "short_id", "name": "character name",
                            "role": "story role", "visual_identity": "consistent visual appearance"}],
            "scenes": [{"title": "scene label for metadata only",
                        "narration": "spoken storyteller text",
                        "visual_prompt": "specific visible action, setting, composition, characters",
                        "character_ids": ["character_id"]}],
        },
    }
    return llm.generate(SYSTEM, prompt, lambda data: validate_episode_plan(data, story))


def record_success(state: dict, story: dict, episode_date: str, run_id: str) -> dict:
    state = dict(state or initial_state())
    ids = list(state.get("completed_story_ids", []))
    if story["id"] not in ids:
        ids.append(story["id"])
    completed = list(state.get("completed", []))
    completed.append({"story_id": story["id"], "title": story["title"],
                      "episode_date": episode_date, "run_id": run_id,
                      "cycle": state.get("cycle", 1),
                      "completed_at": datetime.now(timezone.utc).isoformat()})
    return {"schema_version": 1, "cycle": state.get("cycle", 1),
            "completed_story_ids": ids, "last_story_id": story["id"],
            "completed": completed[-120:]}
