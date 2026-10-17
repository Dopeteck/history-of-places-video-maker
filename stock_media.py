"""Fetch curated Pexels photo/video candidates for destination scenes.

Requires PEXELS_API_KEY (free key from https://www.pexels.com/api/). Searches are conservative:
one query per scene, bounded results, no blind mass-download. Every downloaded file is recorded
with provider URL, photographer, API retrieval date, dimensions, media type, and license label.
A human still reviews subject relevance, people/logos, scene match, and final rights before render.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import requests

API = "https://api.pexels.com/v1"
VIDEO_API = "https://api.pexels.com/videos"
HEADERS_TIMEOUT = (10, 30)
MAX_BYTES = 100 * 1024 * 1024


def safe_name(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return name[:70] or "asset"


def _get(url: str, key: str, params: dict | None = None) -> dict:
    response = requests.get(url, headers={"Authorization": key}, params=params, timeout=HEADERS_TIMEOUT)
    if response.status_code == 429:
        raise RuntimeError("Pexels API rate limit reached; stop and retry later, do not loop.")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Pexels API returned a non-object response")
    return payload


def _download(url: str, destination: Path, session: requests.Session) -> int:
    with session.get(url, stream=True, timeout=HEADERS_TIMEOUT) as response:
        response.raise_for_status()
        length = int(response.headers.get("Content-Length", "0") or 0)
        if length > MAX_BYTES:
            raise ValueError(f"Asset exceeds {MAX_BYTES} byte safety cap")
        destination.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        tmp = destination.with_suffix(destination.suffix + ".part")
        try:
            with tmp.open("wb") as out:
                for chunk in response.iter_content(1024 * 256):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError("Asset exceeded download safety cap")
                    out.write(chunk)
            if total == 0:
                raise ValueError("Empty media download")
            tmp.replace(destination)
        finally:
            if tmp.exists():
                tmp.unlink()
    return total


def _query(scene: dict) -> str:
    tags = scene.get("asset_tags") or scene.get("search_terms") or [scene.get("title", ""), scene.get("narration", "")]
    text = " ".join(str(x) for x in tags if str(x).strip())
    text = re.sub(r"[^\w\s,-]", " ", text)
    return " ".join(text.split())[:180]


def collect(plan_path: Path, asset_root: Path, manifest_path: Path, mode: str = "both",
            per_scene: int = 1, pause: float = 1.0) -> list[dict]:
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Missing PEXELS_API_KEY. Create a free Pexels API key and add it as a GitHub Actions secret or local environment variable.")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    scenes = plan.get("scenes")
    if not isinstance(scenes, list):
        raise ValueError("Plan must contain a scenes array")
    asset_root.mkdir(parents=True, exist_ok=True)
    existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
    if not isinstance(existing, list):
        raise ValueError("Manifest must be a JSON array")
    existing_ids = {x.get("asset_id") for x in existing if isinstance(x, dict)}
    added: list[dict] = []
    with requests.Session() as session:
        for scene in scenes:
            sid = str(scene.get("id", ""))
            if not sid:
                continue
            kind = str(scene.get("type", ""))
            if kind in {"icon_card", "map"}:
                continue
            wanted = ["photo", "video"] if mode == "both" else [mode]
            for media_type in wanted:
                if media_type == "video" and kind not in {"clip", "still_kenburns", "split_compare"}:
                    continue
                query = _query(scene)
                if not query:
                    continue
                if media_type == "photo":
                    payload = _get(f"{API}/search", key, {"query": query, "per_page": min(5, max(1, per_scene)), "orientation": "portrait"})
                    results = payload.get("photos", [])
                else:
                    payload = _get(f"{VIDEO_API}/search", key, {"query": query, "per_page": min(5, max(1, per_scene)), "orientation": "portrait"})
                    results = payload.get("videos", [])
                for item in results[:per_scene]:
                    pid = str(item.get("id", ""))
                    asset_id = f"pexels-{media_type}-{pid}"
                    if not pid or asset_id in existing_ids:
                        continue
                    if media_type == "photo":
                        files = item.get("src") or {}
                        url = files.get("large2x") or files.get("large") or files.get("original")
                        author = str(item.get("photographer", "Unknown photographer"))
                        page_url = str(item.get("url", ""))
                        width, height = item.get("width"), item.get("height")
                        ext = "jpg"
                    else:
                        choices = item.get("video_files") or []
                        choices = [v for v in choices if v.get("link") and v.get("file_type") in {"video/mp4", "video/webm"}]
                        choices.sort(key=lambda v: (abs((v.get("width") or 0) - 1080), -(v.get("height") or 0)))
                        if not choices:
                            continue
                        choice = choices[0]
                        url = choice.get("link")
                        author = str(item.get("user", {}).get("name", "Unknown videographer"))
                        page_url = str(item.get("url", ""))
                        width, height = choice.get("width"), choice.get("height")
                        ext = "mp4" if choice.get("file_type") == "video/mp4" else "webm"
                    if not url or not page_url:
                        continue
                    filename = f"{safe_name(sid)}-{asset_id}.{ext}"
                    local_path = asset_root / filename
                    if local_path.exists():
                        continue
                    size = _download(str(url), local_path, session)
                    entry = {
                        "asset_id": asset_id, "scene_id": sid, "source_url": page_url,
                        "download_url": str(url), "provider": "Pexels", "author": author,
                        "license": "Pexels License", "date_retrieved": date.today().isoformat(),
                        "local_filename": filename, "media_type": media_type,
                        "width": width, "height": height, "bytes": size,
                        "review_flags": ["unreviewed_candidate", "check_people_logos_and_scene_match"],
                        "approved": False,
                        "query": query,
                    }
                    added.append(entry)
                    existing_ids.add(asset_id)
                    time.sleep(max(0, min(pause, 5)))
    all_items = existing + added
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(all_items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--mode", choices=["photo", "video", "both"], default="both")
    parser.add_argument("--per-scene", type=int, choices=[1, 2, 3], default=1)
    args = parser.parse_args()
    items = collect(args.plan, args.assets, args.manifest, args.mode, args.per_scene)
    print(f"Downloaded {len(items)} candidate asset(s). All are unapproved pending review.")
    for item in items:
        print(f"- {item['scene_id']}: {item['media_type']} {item['local_filename']} ({item['width']}x{item['height']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
