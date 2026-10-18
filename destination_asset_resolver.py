"""Resolve destination scene media from reviewed manifest entries.

This is deliberately deterministic: only explicitly approved, downloaded, validated assets
are selected. It records a fallback decision instead of inventing rights or auto-approving
AI reconstructions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

FALLBACK_ORDER = ("archival_still", "map", "document", "site_today", "ai_reconstruction")


def choose_scene_asset(scene: dict[str, Any], manifest: list[dict[str, Any]], asset_root: Path) -> dict[str, Any]:
    scene_id = str(scene.get("id", ""))
    by_id = [a for a in manifest if isinstance(a, dict) and str(a.get("scene_id", "")) == scene_id]
    # Explicit approved archival media wins. Still/document candidates must be validated downloads.
    usable = []
    for asset in by_id:
        if asset.get("approved") is not True or asset.get("download_validated") is not True:
            continue
        local = str(asset.get("local_filename") or "")
        path = (asset_root / local).resolve() if local else None
        root = asset_root.resolve()
        # Manifest paths are untrusted input; reject absolute paths and traversal outside root.
        if not path or (path != root and root not in path.parents):
            continue
        if not path.is_file() or asset.get("needs_human_review") is True:
            # Human review flag means unresolved review; never use until cleared.
            continue
        usable.append((asset, path))
    for wanted_type in ("video", "still", "image", "document", "photo"):
        for asset, path in usable:
            if str(asset.get("media_type", "")).lower() == wanted_type:
                return {"status": "selected", "scene_id": scene_id, "fallback": ("archival_clip" if wanted_type == "video" else "document" if wanted_type == "document" else "archival_still"),
                        "asset": asset, "path": path}
    # Don't silently use a fallback that has no concrete, reviewed asset.
    available = scene.get("fallback_assets") if isinstance(scene.get("fallback_assets"), dict) else {}
    for fallback in FALLBACK_ORDER:
        item = available.get(fallback)
        if not isinstance(item, dict):
            continue
        if item.get("approved") is not True or item.get("download_validated") is not True:
            continue
        local = str(item.get("local_filename") or "")
        path = (asset_root / local).resolve() if local else None
        root = asset_root.resolve()
        if path and (path == root or root in path.parents) and path.is_file() and item.get("needs_human_review") is not True:
            return {"status": "selected", "scene_id": scene_id, "fallback": fallback, "asset": item, "path": path}
    return {"status": "unresolved", "scene_id": scene_id, "fallback_order": list(FALLBACK_ORDER),
            "message": "No approved, validated asset available; scene must remain blocked from rendering."}
