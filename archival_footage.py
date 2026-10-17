"""Official-API archival media resolver with conservative rights filtering.

Search order: Internet Archive, Library of Congress, US National Archives, Wikimedia
Commons video, NASA, then museum open-access still/document material. Results are
candidates only: every item remains unapproved pending human review. No social/video
platform scraping or user-uploaded-video downloads are used.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote
import json
import re

import requests

TIMEOUT = (10, 30)
IA_SEARCH = "https://archive.org/advancedsearch.php"
LOC_SEARCH = "https://www.loc.gov"
NARA_SEARCH = "https://catalog.archives.gov/api/v1"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
NASA_SEARCH = "https://images-api.nasa.gov"
SMITHSONIAN_SEARCH = "https://api.si.edu/openaccess/api/v1.0/search"
ALLOWED_RIGHTS = {"public domain", "public domain mark", "cc0", "cc by", "cc by-sa", "no known copyright restrictions", "no known restrictions"}
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "DestinationHistoryResearch/1.0 (official API archival research)"})

def _get_json(url: str, params: dict | None = None) -> dict:
    response = SESSION.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Official API returned non-object JSON")
    return data

def rights_allowed(statement: str) -> bool:
    """Only allow explicit, recognized rights statements; unknown is always rejected."""
    s = re.sub(r"\s+", " ", str(statement or "").strip().lower())
    if not s:
        return False
    # Reject restrictive/non-commercial terms even if other words appear nearby.
    if any(term in s for term in ("non-commercial", "noncommercial", "cc by-nc", "cc by nc", "cc-by-nc", "cc by-nd", "cc-by-nd", "no derivatives", "all rights reserved", "copyright protected", "permission required")):
        return False
    return any(term in s for term in ALLOWED_RIGHTS)

def _candidate(provider: str, item_id: str, title: str, page_url: str, media_url: str,
               rights: str, media_type: str, author: str = "Unknown", date_created: str = "",
               extra: dict | None = None) -> dict | None:
    if not rights_allowed(rights) or not page_url or not media_url:
        return None
    if not media_url.startswith("https://") or not page_url.startswith("https://"):
        return None
    return {
        "asset_id": f"archive-{provider}-{item_id}",
        "provider": provider,
        "title": title or "Untitled archival item",
        "source_url": page_url,
        "download_url": media_url,
        "author": author or "Unknown",
        "rights_statement": rights,
        "rights_url": (extra or {}).get("rights_url", page_url),
        "license": rights,
        "media_type": media_type,
        "date_created": date_created or "",
        "date_retrieved": date.today().isoformat(),
        "local_filename": "",
        "approved": False,
        "review_flags": ["check identifiable people", "check logos/trademarks", "check modern elements and rights ambiguity", "archival context requires human review"],
        "needs_human_review": True,
        "extra": extra or {},
    }

def search_internet_archive(query: str, limit: int = 10) -> list[dict]:
    data = _get_json(IA_SEARCH, {"q": f'({query}) AND mediatype:movies', "fl[]": ["identifier","title","creator","licenseurl","rights","date"], "rows": limit, "output": "json"})
    docs = data.get("response", {}).get("docs", [])
    out = []
    for d in docs:
        rights = d.get("rights") or d.get("licenseurl") or ""
        ident = d.get("identifier", "")
        # Only direct official item metadata endpoint; avoid guessing a media file URL.
        if not ident:
            continue
        metadata = _get_json(f"https://archive.org/metadata/{quote(ident)}")
        meta = metadata.get("metadata") or {}
        files = metadata.get("files") or []
        choices = [f for f in files if not f.get("private") and (f.get("format","").lower() in {"mpeg4","h.264","h.264 ia","mpeg4 ab","ogv","quicktime"} or str(f.get("name","")).lower().endswith((".mp4",".ogv",".mov")))]
        if not choices:
            continue
        choices.sort(key=lambda f: (not str(f.get("name","")).lower().endswith(".mp4"), int(f.get("size") or 0)))
        f = choices[0]
        statement = str(meta.get("rights") or meta.get("licenseurl") or rights)
        c = _candidate("internet_archive", ident, str(meta.get("title") or d.get("title") or ""), f"https://archive.org/details/{quote(ident)}",
                       f"https://archive.org/download/{quote(ident)}/{quote(str(f.get('name','')), safe='/')}",
                       statement, "video", str(meta.get("creator") or d.get("creator") or "Unknown"),
                       str(meta.get("date") or d.get("date") or ""), {"rights_url": str(meta.get("licenseurl") or f"https://archive.org/details/{quote(ident)}")})
        if c: out.append(c)
    return out

def search_library_of_congress(query: str, limit: int = 10) -> list[dict]:
    data = _get_json(f"{LOC_SEARCH}/item/", {"q": query, "fo": "json", "c": limit, "at": "results,pagination"})
    out = []
    for x in data.get("results", []):
        rights = x.get("rights") or x.get("rights_information") or ""
        resources = x.get("resources") or []
        media_url = ""
        for r in resources:
            files = r.get("files") or []
            if files:
                media_url = str(files[0].get("url") or "")
                break
        if not media_url:
            continue
        c = _candidate("library_of_congress", str(x.get("id") or x.get("url") or ""), str(x.get("title") or ""),
                       str(x.get("id") or x.get("url") or ""), media_url, str(rights), "video" if "moving" in str(x.get("original_format","")).lower() or "film" in str(x.get("original_format","")).lower() else "still",
                       str(x.get("contributor") or "Unknown"), str(x.get("date") or ""), {"rights_url": str(x.get("rights_url") or x.get("id") or x.get("url") or "")})
        if c: out.append(c)
    return out

def search_national_archives(query: str, limit: int = 10) -> list[dict]:
    data = _get_json(f"{NARA_SEARCH}/", {"q": query, "rows": limit, "description.itemType": "na"})
    rows = data.get("body", {}).get("hits", {}).get("hits", [])
    out = []
    for hit in rows:
        src = hit.get("_source") or {}
        rights = src.get("naId") and (src.get("useRestriction") or src.get("accessRestriction") or src.get("rightsStatement")) or ""
        naid = str(src.get("naId") or "")
        objects = src.get("objects") or []
        media_url = next((str(o.get("file") or o.get("url") or "") for o in objects if o.get("file") or o.get("url")), "")
        page = f"https://catalog.archives.gov/id/{quote(naid)}" if naid else ""
        c = _candidate("us_national_archives", naid, str(src.get("title") or ""), page, media_url, str(rights), "video" if "motion picture" in str(src.get("levelOfDescription","")).lower() else "still", "U.S. National Archives", str(src.get("productionDate") or ""), {"rights_url": page})
        if c: out.append(c)
    return out

def search_commons_video(query: str, limit: int = 10) -> list[dict]:
    data = _get_json(COMMONS_API, {"action":"query","format":"json","generator":"search","gsrsearch":f"filetype:video {query}","gsrnamespace":6,"gsrlimit":limit,"prop":"imageinfo","iiprop":"url|extmetadata","iiurlwidth":1280})
    pages = data.get("query", {}).get("pages", {})
    out = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        rights = str((meta.get("LicenseShortName") or {}).get("value") or (meta.get("Copyrighted") or {}).get("value") or "")
        title = str(page.get("title") or "")
        media = str(info.get("url") or "")
        page_url = str(info.get("descriptionurl") or "")
        author = str((meta.get("Artist") or {}).get("value") or "Unknown")
        rights_url = str((meta.get("LicenseUrl") or {}).get("value") or page_url)
        c = _candidate("wikimedia_commons", title.removeprefix("File:"), title, page_url, media, rights, "video", author, "", {"rights_url": rights_url})
        if c: out.append(c)
    return out

def search_nasa(query: str, limit: int = 10) -> list[dict]:
    data = _get_json(f"{NASA_SEARCH}/search", {"q": query, "media_type": "video", "page_size": limit})
    out = []
    for item in data.get("collection", {}).get("items", []):
        data0 = (item.get("data") or [{}])[0]
        links = item.get("links") or []
        media = next((str(x.get("href") or "") for x in links if x.get("rel") == "preview"), "")
        page = str(item.get("href") or "")
        # NASA material is not blanket public domain; only accept an explicit rights label in metadata.
        rights = str(data0.get("rights") or data0.get("license") or "")
        c = _candidate("nasa", str(data0.get("nasa_id") or ""), str(data0.get("title") or ""), page, media, rights, "video", "NASA", str(data0.get("date_created") or ""), {"rights_url": page})
        if c: out.append(c)
    return out

def search_museum_open_access(query: str, limit: int = 10) -> list[dict]:
    """Smithsonian Open Access is used as the museum fallback; API key optional for public endpoint."""
    data = _get_json(SMITHSONIAN_SEARCH, {"q": query, "rows": limit, "online_media_type":"Images", "api_key":""})
    rows = data.get("response", {}).get("rows", [])
    out = []
    for row in rows:
        content = row.get("content") or {}
        descriptive = content.get("descriptiveNonRepeating") or {}
        online = descriptive.get("online_media") or {}
        media = online.get("media") or []
        rights = str(content.get("indexedStructured", {}).get("online_media_rights", "") or content.get("freetext", {}).get("rights", [{}])[0].get("content", "") if content.get("freetext", {}).get("rights") else "")
        for item in media if isinstance(media, list) else []:
            media_url = str(item.get("content") or "")
            if not media_url:
                continue
            page = str(descriptive.get("record_link") or "")
            c = _candidate("museum_open_access", str(row.get("id") or ""), str(descriptive.get("title", {}).get("content") or ""), page, media_url, rights, "still", "Smithsonian Open Access", "", {"rights_url": page})
            if c: out.append(c)
    return out

RESOLVERS = [
    ("internet_archive", search_internet_archive),
    ("library_of_congress", search_library_of_congress),
    ("us_national_archives", search_national_archives),
    ("wikimedia_commons", search_commons_video),
    ("nasa", search_nasa),
    ("museum_open_access", search_museum_open_access),
]

def resolve_archival(query: str, limit_per_source: int = 10) -> list[dict]:
    """Return only explicitly rights-cleared candidates, in the requested source order."""
    results = []
    for name, resolver in RESOLVERS:
        try:
            results.extend(resolver(query, limit_per_source))
        except Exception as exc:
            # A provider outage/schema change must not block other official APIs.
            print(f"Archive source {name} unavailable or malformed: {type(exc).__name__}")
    return results

def choose_fallback(available: dict[str, bool]) -> str:
    """Choose the first available fallback in the required editorial order."""
    order = ("archival_still", "map", "document", "site_today", "ai_reconstruction")
    for key in order:
        if available.get(key):
            return key
    return "unresolved"


def resolve_plan(plan: dict, limit_per_source: int = 5) -> list[dict]:
    """Resolve candidate assets per scene and attach scene IDs for later review."""
    scenes = plan.get("scenes")
    if not isinstance(scenes, list):
        raise ValueError("Plan must contain a scenes array")
    all_candidates = []
    for scene in scenes:
        if not isinstance(scene, dict) or not scene.get("id"):
            continue
        query_parts = scene.get("asset_tags") or scene.get("search_terms") or [scene.get("title", ""), scene.get("narration", "")]
        query = " ".join(str(part) for part in query_parts if str(part).strip())[:240]
        if not query.strip():
            continue
        for candidate in resolve_archival(query, limit_per_source):
            candidate["scene_id"] = str(scene["id"])
            candidate["scene_query"] = query
            all_candidates.append(candidate)
    return all_candidates


def save_candidates(candidates: list[dict], manifest_path: Path, asset_root: Path) -> int:
    """Append metadata-only candidates. Downloads require separate explicit review."""
    existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
    if not isinstance(existing, list):
        raise ValueError("assets_manifest.json must be a JSON array")
    known = {x.get("asset_id") for x in existing if isinstance(x, dict)}
    asset_root.mkdir(parents=True, exist_ok=True)
    for candidate in candidates:
        if candidate["asset_id"] not in known:
            existing.append(candidate)
            known.add(candidate["asset_id"])
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(existing)

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", help="Historical event/place/topic")\n    parser.add_argument("--plan", type=Path, help="Optional scene plan; resolves each scene separately")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if not args.query and not args.plan:\n        parser.error("provide --query or --plan")\n    limit = max(1, min(args.limit, 25))\n    if args.plan:\n        plan = json.loads(args.plan.read_text(encoding="utf-8"))\n        candidates = resolve_plan(plan, limit)\n    else:\n        candidates = resolve_archival(args.query, limit)
    count = save_candidates(candidates, args.manifest, args.assets)
    print(f"Added {count} total manifest entries; {len(candidates)} archival candidates passed rights filters. All remain unapproved.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
