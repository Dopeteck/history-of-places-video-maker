"""Download only explicitly approved archival media from institutional hosts.

The downloader never changes approval state. Still images must be at least 1920×1080;
video files must contain a usable video stream. Redirects are revalidated, downloads are
size-limited, and all successful downloads remain flagged for human review.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image

from archival_footage import rights_allowed

TIMEOUT = (10, 45)
MAX_BYTES = 150 * 1024 * 1024
ALLOWED_HOSTS = {
    "archive.org", "www.loc.gov", "loc.gov", "tile.loc.gov",
    "catalog.archives.gov", "images.nara.gov",
    "images-assets.nasa.gov", "images-api.nasa.gov",
    "commons.wikimedia.org", "upload.wikimedia.org",
    "api.si.edu", "ids.si.edu", "si.edu",
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".ogv", ".webm", ".m4v"}


def _safe_host(url: str) -> bool:
    """Accept HTTPS only on an allowlisted host or a true subdomain."""
    try:
        parsed = urlparse(str(url))
        host = (parsed.hostname or "").lower().rstrip(".")
        return parsed.scheme == "https" and any(
            host == allowed or host.endswith("." + allowed)
            for allowed in ALLOWED_HOSTS
        )
    except (TypeError, ValueError):
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_asset_id(value: object) -> str:
    asset_id = str(value or "")
    if not asset_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", asset_id):
        raise ValueError("Asset ID must contain only letters, numbers, hyphens or underscores")
    return asset_id


def download_approved_item(item: dict, asset_root: Path, session=requests) -> dict:
    """Download one explicitly approved item and return validated manifest data."""
    if not isinstance(item, dict) or item.get("approved") is not True:
        raise ValueError("Asset must be explicitly approved after human review")

    rights = str(item.get("rights_statement") or item.get("license") or "").strip()
    if not rights_allowed(rights):
        raise ValueError("Asset rights statement is missing or not on the commercial-use allowlist")
    if item.get("review_flags"):
        raise ValueError("Asset has unresolved review flags")

    url = str(item.get("download_url") or "")
    if not _safe_host(url):
        raise ValueError("Download URL must use HTTPS on an institutional host")
    for field in ("source_url", "rights_url"):
        value = str(item.get(field) or "")
        if not value.startswith("https://"):
            raise ValueError(f"{field} must be retained as an HTTPS provenance URL")

    media_type = str(item.get("media_type") or "").strip().lower()
    suffix = Path(urlparse(url).path).suffix.lower()
    if media_type in {"still", "image", "photo"}:
        if suffix not in IMAGE_SUFFIXES:
            raise ValueError("Still image URL must have a recognized image extension")
    elif media_type == "video":
        if suffix not in VIDEO_SUFFIXES:
            raise ValueError("Video URL must have a recognized video extension")
    else:
        raise ValueError("Only still images and video clips are supported; documents are rejected")

    asset_id = _safe_asset_id(item.get("asset_id"))
    root = Path(asset_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f".{asset_id}{suffix}.part"
    total = 0
    width = height = None
    image_format = None
    final_url = url

    try:
        with session.get(
            url,
            stream=True,
            timeout=TIMEOUT,
            allow_redirects=True,
            headers={"User-Agent": "DarkFairytalesArchivalDownloader/1.0"},
        ) as response:
            response.raise_for_status()
            final_url = str(getattr(response, "url", url) or url)
            if not _safe_host(final_url):
                raise ValueError("Download redirected outside the institutional host allowlist")
            with temporary.open("wb") as output:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError("Asset exceeds the 150 MiB download safety limit")
                    output.write(chunk)

        if total == 0:
            raise ValueError("Downloaded asset is empty")

        if media_type in {"still", "image", "photo"}:
            with Image.open(temporary) as image:
                image.verify()
            with Image.open(temporary) as image:
                width, height = image.size
                image_format = (image.format or "").lower()
            if max(width, height) < 1920 or min(width, height) < 1080:
                raise ValueError("Still image must be at least 1920×1080")
        else:
            if not shutil.which("ffprobe"):
                raise RuntimeError("ffprobe is required to validate archival video downloads")
            probe = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-show_entries",
                    "stream=codec_type,width,height", "-of", "json", str(temporary),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if probe.returncode:
                raise ValueError("ffprobe rejected downloaded video")
            streams = json.loads(probe.stdout).get("streams", [])
            video = next((s for s in streams if s.get("codec_type") == "video"), None)
            if not video:
                raise ValueError("Downloaded file contains no video stream")
            width, height = video.get("width"), video.get("height")
            if not width or not height or min(int(width), int(height)) < 360:
                raise ValueError("Archival video resolution is too small for review")

        digest = _sha256(temporary)
        destination = root / f"{asset_id}_{digest[:12]}{suffix}"
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    review_flags = list(dict.fromkeys(
        list(item.get("review_flags") or []) + [
            "verify identifiable people",
            "verify logos and trademarks",
            "verify historical context and modern rights",
        ]
    ))
    result = dict(item)
    result.update({
        "local_filename": destination.name,
        "byte_size": total,
        "download_bytes": total,
        "sha256": digest,
        "download_sha256": digest,
        "width": width,
        "height": height,
        "dimensions": [width, height] if width and height else None,
        "image_format": image_format,
        "downloaded_url": final_url,
        "download_validated": True,
        "needs_human_review": True,
        "review_flags": review_flags,
    })
    return result


def update_manifest(manifest_path: Path, asset_root: Path, session=requests) -> int:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("assets_manifest.json must contain a JSON array")

    updated = []
    count = 0
    for item in payload:
        if (
            not isinstance(item, dict)
            or item.get("approved") is not True
            or item.get("local_filename")
            or item.get("download_validated") is True
        ):
            updated.append(item)
            continue
        try:
            updated.append(download_approved_item(item, asset_root, session=session))
            count += 1
        except (ValueError, requests.RequestException, OSError, subprocess.SubprocessError, RuntimeError) as exc:
            copy = dict(item)
            copy["download_error"] = f"{type(exc).__name__}: {exc}"
            updated.append(copy)

    manifest_path.write_text(
        json.dumps(updated, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return count


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    args = parser.parse_args()
    print(f"Downloaded and validated {update_manifest(args.manifest, args.assets)} approved assets.")
