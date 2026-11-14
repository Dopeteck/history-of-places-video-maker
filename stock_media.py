"""Fetch small, review-only stock-media batches from Pexels, Pixabay, and Unsplash.

Provider API keys are read only from environment variables. Downloads are never approved
automatically; no provider asset is rendered or published without human review.
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

TIMEOUT = (10, 30)
MAX_BYTES = 100 * 1024 * 1024
PEXELS_API = "https://api.pexels.com/v1"
PIXABAY_API = "https://pixabay.com/api/"
UNSPLASH_API = "https://api.unsplash.com"


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()[:70] or "asset"


def _get(url: str, *, headers: dict | None = None, params: dict | None = None) -> dict:
    response = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
    if response.status_code == 429:
        raise RuntimeError("Stock provider rate limit reached; stop and retry later.")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Stock provider returned a non-object response")
    return payload


def _download(url: str, destination: Path, session: requests.Session) -> int:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Media URL must use HTTPS")
    # Only known provider/CDN host families are accepted; never fetch arbitrary manifest URLs.
    host = parsed.hostname.lower()
    allowed = (
        host == "images.pexels.com" or host.endswith(".pexels.com")
        or host == "videos.pexels.com" or host.endswith(".pixabay.com")
        or host == "cdn.pixabay.com" or host.endswith(".unsplash.com")
        or host.endswith(".imgix.net")
    )
    if not allowed:
        raise ValueError(f"Untrusted stock media download host: {host}")
    with session.get(url, stream=True, timeout=TIMEOUT) as response:
        response.raise_for_status()
        length = int(response.headers.get("Content-Length", "0") or 0)
        if length > MAX_BYTES:
            raise ValueError("Asset exceeds 100 MiB safety cap")
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(destination.suffix + ".part")
        total = 0
        try:
            with tmp.open("wb") as out:
                for chunk in response.iter_content(256 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError("Asset exceeded 100 MiB safety cap")
                    out.write(chunk)
            if not total:
                raise ValueError("Empty media download")
            tmp.replace(destination)
        finally:
            if tmp.exists():
                tmp.unlink()
    return total


def _query(scene: dict) -> str:
    tags = scene.get("asset_tags") or scene.get("search_terms") or [scene.get("title", ""), scene.get("narration", "")]
    text = " ".join(str(x).replace("-", " ") for x in tags if str(x).strip())
    text = re.sub(r"[^\w\s,']", " ", text)
    return " ".join(text.split())[:180]


def _candidates(provider: str, media_type: str, query: str) -> list[dict]:
    if provider == "pexels":
        key = os.environ.get("PEXELS_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Missing PEXELS_API_KEY")
        headers = {"Authorization": key}
        if media_type == "photo":
            payload = _get(f"{PEXELS_API}/search", headers=headers,
                           params={"query": query, "per_page": 3, "orientation": "portrait"})
            out = []
            for x in payload.get("photos", []):
                src = x.get("src") or {}
                out.append({"id": x.get("id"), "url": src.get("large2x") or src.get("large") or src.get("original"),
                            "page": x.get("url"), "author": x.get("photographer", "Unknown photographer"),
                            "width": x.get("width"), "height": x.get("height"), "ext": "jpg"})
            return out
        payload = _get("https://api.pexels.com/videos/search", headers=headers,
                       params={"query": query, "per_page": 3, "orientation": "portrait"})
        out = []
        for x in payload.get("videos", []):
            choices = [v for v in x.get("video_files", []) if v.get("link") and v.get("file_type") == "video/mp4"]
            choices.sort(key=lambda v: (abs((v.get("width") or 0) - 1080), -(v.get("height") or 0)))
            if choices:
                v = choices[0]
                out.append({"id": x.get("id"), "url": v["link"], "page": x.get("url"),
                            "author": (x.get("user") or {}).get("name", "Unknown videographer"),
                            "width": v.get("width"), "height": v.get("height"), "ext": "mp4"})
        return out
    if provider == "pixabay":
        key = os.environ.get("PIXABAY_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Missing PIXABAY_API_KEY")
        if media_type == "photo":
            payload = _get(PIXABAY_API, params={"key": key, "q": query, "image_type": "photo",
                                                 "orientation": "vertical", "safesearch": "true", "per_page": 3})
            return [{"id": x.get("id"), "url": x.get("largeImageURL") or x.get("webformatURL"),
                     "page": x.get("pageURL"), "author": x.get("user", "Unknown creator"),
                     "width": x.get("imageWidth"), "height": x.get("imageHeight"), "ext": "jpg"}
                    for x in payload.get("hits", [])]
        payload = _get("https://pixabay.com/api/videos/", params={"key": key, "q": query,
                         "safesearch": "true", "per_page": 3})
        out = []
        for x in payload.get("hits", []):
            variants = x.get("videos") or {}
            choices = [v for v in variants.values() if isinstance(v, dict) and v.get("url")]
            choices.sort(key=lambda v: abs((v.get("width") or 0) - 1080))
            if choices:
                v = choices[0]
                out.append({"id": x.get("id"), "url": v["url"], "page": x.get("pageURL"),
                            "author": x.get("user", "Unknown creator"), "width": v.get("width"),
                            "height": v.get("height"), "ext": "mp4"})
        return out
    if provider == "unsplash":
        if media_type != "photo":
            return []
        key = os.environ.get("UNSPLASH_ACCESS_KEY", "").strip()
        if not key:
            raise RuntimeError("Missing UNSPLASH_ACCESS_KEY")
        payload = _get(f"{UNSPLASH_API}/search/photos", headers={"Authorization": f"Client-ID {key}"},
                       params={"query": query, "per_page": 3, "orientation": "portrait", "content_filter": "high"})
        out = []
        for x in payload.get("results", []):
            urls = x.get("urls") or {}
            user = x.get("user") or {}
            out.append({"id": x.get("id"), "url": urls.get("regular") or urls.get("full"),
                        "page": (x.get("links") or {}).get("html"), "author": user.get("name", "Unknown photographer"),
                        "width": x.get("width"), "height": x.get("height"), "ext": "jpg"})
        return out
    raise ValueError(f"Unknown provider: {provider}")


LICENSES = {"pexels": "Pexels License", "pixabay": "Pixabay Content License", "unsplash": "Unsplash License"}


def collect(plan_path: Path, asset_root: Path, manifest_path: Path, mode: str = "both",
            per_scene: int = 1, pause: float = 1.0, providers: list[str] | None = None) -> list[dict]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    scenes = plan.get("scenes")
    if not isinstance(scenes, list):
        raise ValueError("Plan must contain a scenes array")
    providers = providers or ["pexels", "pixabay", "unsplash"]
    key_names = {"pexels": "PEXELS_API_KEY", "pixabay": "PIXABAY_API_KEY", "unsplash": "UNSPLASH_ACCESS_KEY"}
    active = []
    for provider in providers:
        if os.environ.get(key_names[provider], "").strip():
            active.append(provider)
        else:
            print(f"Skipping {provider}: {key_names[provider]} is not configured.")
    if not active:
        raise RuntimeError("No stock providers configured. Add at least one provider API key.")
    providers = active
    wanted = ["photo", "video"] if mode == "both" else [mode]
    existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
    if not isinstance(existing, list):
        raise ValueError("Manifest must be a JSON array")
    existing_ids = {x.get("asset_id") for x in existing if isinstance(x, dict)}
    added: list[dict] = []
    asset_root.mkdir(parents=True, exist_ok=True)
    with requests.Session() as session:
        for scene in scenes:
            sid = str(scene.get("id", ""))
            if not sid or scene.get("type") in {"icon_card", "map"}:
                continue
            query = _query(scene)
            if not query:
                continue
            for provider in providers:
                media_types = [m for m in wanted if provider != "unsplash" or m == "photo"]
                for media_type in media_types:
                    if media_type == "video" and scene.get("type") not in {"clip", "still_kenburns", "split_compare"}:
                        continue
                    for item in _candidates(provider, media_type, query)[:max(1, min(3, per_scene))]:
                        pid = str(item.get("id") or "")
                        asset_id = f"{provider}-{media_type}-{pid}"
                        if not pid or asset_id in existing_ids or not item.get("url") or not item.get("page"):
                            continue
                        filename = f"{safe_name(sid)}-{asset_id}.{item['ext']}"
                        local_path = asset_root / filename
                        if local_path.exists():
                            continue
                        size = _download(str(item["url"]), local_path, session)
                        flags = ["unreviewed_candidate", "check_people_logos_and_scene_match"]
                        if provider == "pixabay":
                            flags.append("verify_exact_pixabay_license_and_ai_generated_status")
                        if provider == "unsplash":
                            flags.append("verify_unsplash_license_and_api_attribution_guidelines")
                        entry = {
                            "asset_id": asset_id, "scene_id": sid, "source_url": str(item["page"]),
                            "download_url": str(item["url"]), "provider": provider.title(),
                            "author": str(item["author"]), "license": LICENSES[provider],
                            "date_retrieved": date.today().isoformat(), "local_filename": filename,
                            "media_type": media_type, "width": item.get("width"), "height": item.get("height"),
                            "bytes": size, "review_flags": flags, "approved": False, "query": query,
                        }
                        added.append(entry)
                        existing_ids.add(asset_id)
                        time.sleep(max(0, min(pause, 5)))
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(existing + added, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--mode", choices=["photo", "video", "both"], default="both")
    parser.add_argument("--provider", choices=["pexels", "pixabay", "unsplash", "all"], default="all")
    parser.add_argument("--per-scene", type=int, choices=[1, 2, 3], default=1)
    args = parser.parse_args()
    providers = ["pexels", "pixabay", "unsplash"] if args.provider == "all" else [args.provider]
    items = collect(args.plan, args.assets, args.manifest, args.mode, args.per_scene, providers=providers)
    print(f"Downloaded {len(items)} candidate asset(s). All are unapproved pending review.")
    for item in items:
        print(f"- {item['provider']} / {item['scene_id']}: {item['media_type']} {item['local_filename']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
