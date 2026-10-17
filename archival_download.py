"""Download and validate only explicitly approved archival manifest items.

This module never changes approval state. Reviewers must set approved=true after checking
the exact rights statement and source page. Downloads are limited to institutional hosts.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image

from archival_footage import rights_allowed

TIMEOUT = (10, 45)
MAX_BYTES = 150 * 1024 * 1024
ALLOWED_HOSTS = {
    "archive.org", "www.loc.gov", "loc.gov", "catalog.archives.gov",
    "images-assets.nasa.gov", "images-api.nasa.gov", "commons.wikimedia.org",
    "upload.wikimedia.org", "api.si.edu", "ids.si.edu", "si.edu",
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".ogv", ".webm", ".m4v"}


def _safe_host(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_approved_item(item: dict, asset_root: Path, session=requests) -> dict:
    """Download one approved, rights-qualified item and return enriched manifest data."""
    if item.get("approved") is not True:
        raise ValueError("Asset must be explicitly approved by a human before download")
    rights = str(item.get("rights_statement") or item.get("license") or "")
    if not rights_allowed(rights):
        raise ValueError("Asset rights statement is not on the commercial-use allowlist")
    url = str(item.get("download_url") or "")
    if not _safe_host(url):
        raise ValueError("Download URL is not on the institutional host allowlist")
    if not str(item.get("source_url", "")).startswith("https://") or not str(item.get("rights_url", "")).startswith("https://"):
        raise ValueError("Source and rights URLs must be retained as HTTPS links")
    asset_id = str(item.get("asset_id") or "")
    if not asset_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in asset_id):
        raise ValueError("Unsafe or missing asset_id")
    suffix = Path(urlparse(url).path).suffix.lower()
    media_type = str(item.get("media_type") or "").lower()
    if suffix not in IMAGE_SUFFIXES | VIDEO_SUFFIXES:
        raise ValueError("Unknown media extension; refusing to guess file type")
    if media_type == "video" and suffix not in VIDEO_SUFFIXES:
        raise ValueError("Video candidate URL does not have a recognized video extension")
    if media_type in {"still", "image", "document"} and suffix not in IMAGE_SUFFIXES:
        raise ValueError("Still/document candidate URL does not have a recognized image extension")
    asset_root.mkdir(parents=True, exist_ok=True)
    target = asset_root / f"{asset_id}{suffix}"
    temp = target.with_suffix(target.suffix + ".part")
    total = 0
    try:
        with session.get(url, stream=True, timeout=TIMEOUT, allow_redirects=True) as response:
            response.raise_for_status()
            final_url = response.url
            if not _safe_host(final_url):
                raise ValueError("Download redirected outside the institutional host allowlist")
            with temp.open("wb") as out:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError("Asset exceeds 150 MiB safety limit")
                    out.write(chunk)
        if total == 0:
            raise ValueError("Downloaded asset is empty")
        if suffix in IMAGE_SUFFIXES:
            with Image.open(temp) as image:
                image.verify()
            with Image.open(temp) as image:
                width, height = image.size
                if width < 640 or height < 360:
                    raise ValueError("Image is too small for editorial review")
        else:
            proc = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                 "-of", "json", str(temp)], capture_output=True, text=True, timeout=30
            )
            if proc.returncode:
                raise ValueError("ffprobe rejected downloaded video")
            probe = json.loads(proc.stdout)
            streams = probe.get("streams", [])
            if not any(s.get("codec_type") == "video" for s in streams):
                raise ValueError("Downloaded file contains no video stream")
            width = next((s.get("width") for s in streams if s.get("codec_type") == "video"), None)
            height = next((s.get("height") for s in streams if s.get("codec_type") == "video"), None)
        temp.replace(target)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    result = dict(item)
    result.update({
        "local_filename": target.name,
        "byte_size": total,
        "sha256": _sha256(target),
        "width": width,
        "height": height,
        "downloaded_url": url,
        "download_validated": True,
        "needs_human_review": True,
        "review_flags": list(dict.fromkeys(list(item.get("review_flags", [])) + [
            "verify identifiable people", "verify logos/trademarks", "verify context and modern rights"
        ])),
    })
    return result


def update_manifest(manifest_path: Path, asset_root: Path) -> int:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("assets_manifest.json must contain an array")
    updated, count = [], 0
    for item in payload:
        if not isinstance(item, dict) or item.get("approved") is not True or item.get("local_filename"):
            updated.append(item)
            continue
        try:
            updated.append(download_approved_item(item, asset_root))
            count += 1
        except (ValueError, requests.RequestException, OSError, subprocess.SubprocessError) as exc:
            copy = dict(item)
            copy["download_error"] = f"{type(exc).__name__}: {exc}"
            updated.append(copy)
    manifest_path.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return count


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    args = parser.parse_args()
    print(f"Downloaded and validated {update_manifest(args.manifest, args.assets)} approved assets.")
