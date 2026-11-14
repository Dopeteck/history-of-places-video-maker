"""Validation for source-bound dark-folklore episode plans."""
from __future__ import annotations

from typing import Any


def validate_folklore_plan(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("format") != "dark_folklore_documentary":
        raise ValueError("format must be dark_folklore_documentary")
    if data.get("status") != "draft_needs_human_fact_check_and_asset_review":
        raise ValueError("folklore plans must remain an explicitly unapproved draft")
    if data.get("aspect_ratio") != "9:16" or data.get("target_fps") != 30:
        raise ValueError("folklore shorts require 9:16 at 30 FPS")
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("at least one identified source edition is required")
    source_ids = {s.get("id") for s in sources if isinstance(s, dict)}
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not 8 <= len(scenes) <= 10:
        raise ValueError("a folklore pilot must contain 8–10 visual beats")
    ids: set[str] = set()
    for scene in scenes:
        sid = scene.get("id") if isinstance(scene, dict) else None
        if not isinstance(sid, str) or not sid or sid in ids:
            raise ValueError("each beat needs a unique id")
        ids.add(sid)
        if scene.get("type") != "still_kenburns":
            raise ValueError(f"{sid} must use a consistent story-illustration scene type")
        if not isinstance(scene.get("narration"), str) or not scene["narration"].strip():
            raise ValueError(f"{sid} needs narration")
        refs = scene.get("source_ids")
        if not isinstance(refs, list) or not refs or any(ref not in source_ids for ref in refs):
            raise ValueError(f"{sid} must cite a listed source")
        if not scene.get("evidence", "").strip():
            raise ValueError(f"{sid} needs a claim-level evidence note")
        requirement = scene.get("asset_requirement")
        if not isinstance(requirement, dict) or len(requirement.get("search_terms", [])) < 2:
            raise ValueError(f"{sid} needs story-specific asset search terms")
        if requirement.get("status") not in {"missing", "candidate", "approved"}:
            raise ValueError("asset status must be missing, candidate, or reviewed approved")
        if scene.get("duration_seconds", 0) <= 0:
            raise ValueError(f"{sid} needs a positive target duration")
    words = len(str(data.get("narration", "")).split())
    if not 150 <= words <= 235:
        raise ValueError(f"narration has {words} words; expected 150–235 for a complete story")
    if " ".join(scene["narration"].strip() for scene in scenes) != data["narration"].strip():
        raise ValueError("beat narration must join exactly to the master script")
    total = sum(float(scene["duration_seconds"]) for scene in scenes)
    if not 70 <= total <= 110:
        raise ValueError(f"beat target durations total {total:.1f}s; expected 70–110s for a complete story")
    return {**data, "narration_word_count": words, "target_duration_seconds": total}
