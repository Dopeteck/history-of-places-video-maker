"""Additive validation primitives for the destination-history video mode.

The legacy stickman/story renderer remains untouched. This module validates the
reviewable, source-aware scene manifest before any destination render is allowed.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
import json

LAYOUTS = {"framed", "full-bleed"}
SCENE_TYPES = {"still_kenburns", "map", "icon_card", "split_compare", "clip"}
COMMERCIAL_LICENSES = {
    "CC0 1.0 Universal",
    "CC BY 2.0",
    "CC BY 3.0",
    "CC BY 4.0",
    "CC BY-SA 2.0",
    "CC BY-SA 3.0",
    "CC BY-SA 4.0",
    "Public Domain",
    "Public Domain Mark 1.0",
    "NASA Media Usage Guidelines",
    "Pexels License",
}
ATTRIBUTION_REQUIRED = {x for x in COMMERCIAL_LICENSES if x.startswith(("CC BY ", "CC BY-SA "))}


def validate_asset(item: dict[str, Any]) -> dict[str, Any]:
    """Reject assets without traceable provenance, commercial rights, or usable size."""
    required = ("source_url", "author", "license", "date_retrieved", "local_filename", "scene_id")
    missing = [key for key in required if not str(item.get(key, "")).strip()]
    if missing:
        raise ValueError("Asset missing required manifest fields: " + ", ".join(missing))
    if item["license"] not in COMMERCIAL_LICENSES:
        raise ValueError(f"Unapproved or unknown commercial license: {item['license']}")
    try:
        date.fromisoformat(item["date_retrieved"])
    except (TypeError, ValueError) as exc:
        raise ValueError("date_retrieved must be an ISO date (YYYY-MM-DD)") from exc
    if not str(item["source_url"]).startswith(("https://", "http://")):
        raise ValueError("source_url must be an HTTP(S) URL")
    if not str(item["local_filename"]).strip():
        raise ValueError("local_filename cannot be empty")
    flags = item.get("review_flags", [])
    if not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags):
        raise ValueError("review_flags must be a list of strings")
    result = dict(item)
    result["needs_human_review"] = bool(flags)
    result["credit_required"] = item["license"] in ATTRIBUTION_REQUIRED
    return result


def validate_destination_plan(data: dict[str, Any]) -> dict[str, Any]:
    """Validate the destination short's plan without changing legacy story schemas."""
    if not isinstance(data, dict):
        raise ValueError("Destination plan must be an object")
    if data.get("layout", "framed") not in LAYOUTS:
        raise ValueError("layout must be framed or full-bleed")
    if data.get("aspect_ratio", "9:16") != "9:16":
        raise ValueError("Destination videos must use 9:16")
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not 8 <= len(scenes) <= 24:
        raise ValueError("A 45–60 second plan needs 8–24 scene beats")
    ids: set[str] = set()
    normalized = []
    for index, raw in enumerate(scenes, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"scene {index} must be an object")
        sid, kind = raw.get("id"), raw.get("type")
        if not isinstance(sid, str) or not sid or sid in ids:
            raise ValueError(f"scene {index} needs a unique nonempty id")
        ids.add(sid)
        if kind not in SCENE_TYPES:
            raise ValueError(f"scene {sid} has an unsupported type")
        narration = raw.get("narration", "")
        if not isinstance(narration, str) or not narration.strip():
            raise ValueError(f"scene {sid} needs narration")
        sources = raw.get("source_ids")
        if not isinstance(sources, list) or not sources:
            raise ValueError(f"scene {sid} needs at least one source id")
        duration = raw.get("duration_seconds")
        if not isinstance(duration, (int, float)) or not 2 <= duration <= 4:
            raise ValueError(f"scene {sid} duration must be 2–4 seconds")
        normalized.append({**raw, "duration_seconds": float(duration)})
    total = sum(x["duration_seconds"] for x in normalized)
    if not 45 <= total <= 60:
        raise ValueError(f"Scene durations total {total:.1f}s; expected 45–60s")
    return {**data, "layout": data.get("layout", "framed"), "scenes": normalized,
            "duration_seconds": total, "status": "needs_human_review"}


def load_asset_manifest(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("assets_manifest.json must contain a JSON array")
    return [validate_asset(item) for item in payload]


def credits_text(assets: list[dict[str, Any]]) -> str:
    """Build an attribution block from reviewed manifest entries only."""
    lines = []
    for asset in assets:
        item = validate_asset(asset)
        if item["credit_required"]:
            lines.append(
                f'{item["local_filename"]} — {item["author"]}; '
                f'{item["license"]}; source: {item["source_url"]}'
            )
    return "\n".join(lines) if lines else "No attribution-required assets are currently listed."
