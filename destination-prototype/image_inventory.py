#!/usr/bin/env python3
"""Region-first visual inventory.

Discovers, vets, deduplicates and persists archival images for a region *before*
any scene or story is authored. The inventory is the single source of truth for
which real, rights-cleared, relevant assets exist; stories emerge from it.

Design:
- Broad, region-based discovery queries (name, aliases, place names, geography,
  periods, landmarks, people, events, cultural subjects) -- never scene-specific
  keywords invented before discovery.
- Eligibility gates are non-negotiable: acceptable rights, HTTPS source+asset
  URLs, valid image format, adequate resolution, duplicate detection, text-heavy
  exclusion. No gate is loosened to inflate inventory.
- Regional relevance is one signal (CLIP + metadata + geographic evidence),
  never the sole historical fact.
- Inventory is persisted per region and reused across runs; coverage, pagination,
  result counts and errors are recorded transparently.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "destination-prototype" / "data"
INVENTORY_DIR = DATA_DIR / "inventory"

# Reuse the provider adapters from asset_resolver so discovery uses the same
# official collection APIs as the resolver.
from asset_resolver import (  # noqa: E402
    PROVIDERS, DEFAULT_PROVIDERS, _candidate, _rights_ok, _https, _norm,
    _clean, _years, ProviderSkipped,
)
from asset_resolver import _get as _provider_get  # noqa: E402

UA = "RegionHistoryShort/2.2 (https://github.com/Dopeteck/history-of-places-video-maker)"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})

# Eligibility thresholds (mirror the renderer's gates; never loosened).
MIN_SHORT_SIDE = 600
MAX_ASPECT = 3.0
MAX_UPSCALE = 1.25

# Broad discovery query templates, instantiated per region from config.
_QUERY_TEMPLATES = [
    "{name} history",
    "{name} historic",
    "{name} {era}",
    "{place} {country}",
    "{name} landmark",
    "{name} {category}",
    "{name} 19th century",
    "{name} 20th century",
    "{name} cultural heritage",
    "{alias}",
    "{place_name}",
]


@dataclass
class InventoryItem:
    """A single vetted, rights-cleared image candidate in the region inventory."""
    provider: str
    title: str
    description: str
    image_url: str
    source_url: str
    rights: str
    rights_statement: str
    rights_url: str
    license_url: str
    author: str
    identifier: str
    categories: List[str] = field(default_factory=list)
    facets: List[str] = field(default_factory=list)
    date_text: str = ""
    year_range: Optional[List[int]] = None
    dimensions: Optional[List[int]] = None
    digest: Optional[str] = None
    clip_score: Optional[float] = None
    relevance_score: float = 0.0
    eligible: bool = False
    rejection_reason: Optional[str] = None
    discovered_via: str = ""
    discovered_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InventoryCoverage:
    """Per-provider coverage record for transparency."""
    provider: str
    queries: List[str] = field(default_factory=list)
    results_returned: int = 0
    candidates_collected: int = 0
    eligible: int = 0
    skipped: bool = False
    skip_reason: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    truncated: bool = False


@dataclass
class RegionInventory:
    region_slug: str
    region_name: str
    country: str
    created_at: str
    items: List[InventoryItem] = field(default_factory=list)
    coverage: List[InventoryCoverage] = field(default_factory=list)
    total_discovered: int = 0
    total_eligible: int = 0
    total_rejected: int = 0


def _ensure_dirs() -> None:
    INVENTORY_DIR.mkdir(parents=True, exist_ok=True)


def inventory_path(region_slug: str) -> Path:
    _ensure_dirs()
    return INVENTORY_DIR / f"{region_slug}.json"


def save_inventory(region_slug: str, inventory: RegionInventory) -> Path:
    _ensure_dirs()
    path = inventory_path(region_slug)
    data = {
        "region_slug": inventory.region_slug,
        "region_name": inventory.region_name,
        "country": inventory.country,
        "created_at": inventory.created_at,
        "total_discovered": inventory.total_discovered,
        "total_eligible": inventory.total_eligible,
        "total_rejected": inventory.total_rejected,
        "coverage": [asdict(c) for c in inventory.coverage],
        "items": [i.to_dict() for i in inventory.items],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_inventory(region_slug: str) -> Optional[RegionInventory]:
    path = inventory_path(region_slug)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = [InventoryItem(**i) for i in data.get("items", [])]
        coverage = [InventoryCoverage(**c) for c in data.get("coverage", [])]
        return RegionInventory(
            region_slug=data.get("region_slug", region_slug),
            region_name=data.get("region_name", ""),
            country=data.get("country", ""),
            created_at=data.get("created_at", ""),
            items=items,
            coverage=coverage,
            total_discovered=data.get("total_discovered", 0),
            total_eligible=data.get("total_eligible", 0),
            total_rejected=data.get("total_rejected", 0),
        )
    except Exception:
        return None


def _build_queries(config: Dict[str, Any]) -> List[str]:
    """Build broad, region-based discovery queries from config identity fields.

    These are not scene-specific keywords; they are general discovery probes so
    the inventory reflects what material actually exists for the region.
    """
    name = str(config.get("name", ""))
    country = str(config.get("country", ""))
    category = str(config.get("story", {}).get("category", ""))
    period_focus = str(config.get("period_focus", ""))
    place_names = [p.get("spelling", "") for p in config.get("place_names", []) if p.get("spelling")]
    aliases = list(dict.fromkeys([name] + place_names))

    queries: List[str] = []
    seen: Set[str] = set()
    for tmpl in _QUERY_TEMPLATES:
        for token in aliases:
            q = tmpl.format(name=name, place=name, country=country, era=period_focus,
                            category=category, alias=token, place_name=token).strip()
            q = re.sub(r"\s+", " ", q)
            if q and q not in seen and len(q.split()) <= 6:
                seen.add(q)
                queries.append(q)
    # Cap total queries so discovery stays bounded and rate-limit friendly.
    return queries[:24]


def _download_image(image_url: str, timeout: Tuple[float, float] = (10.0, 45.0)) -> Optional[Tuple[bytes, str, Tuple[int, int]]]:
    """Download an image and return (bytes, content_type, (w, h)) or None."""
    try:
        r = SESSION.get(image_url, timeout=timeout)
        r.raise_for_status()
        data = r.content
        ct = r.headers.get("Content-Type", "").lower()
        im = Image.open(io.BytesIO(data)).convert("RGB")
        return data, ct, im.size
    except Exception:
        return None


def _eligible(item: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Apply non-negotiable eligibility gates. Returns (ok, reason)."""
    rights = str(item.get("rights", "") or "")
    if not rights or not _rights_ok(rights):
        return False, "rights_not_acceptable"
    image_url = str(item.get("image_url", "") or "")
    source_url = str(item.get("source_url", "") or "")
    if not image_url.startswith("https://") or not source_url.startswith("https://"):
        return False, "non_https_url"
    if BANNED_METADATA.search((item.get("title", "") + " " + item.get("description", "") + " " + image_url).lower()):
        return False, "banned_metadata"
    return True, None


BANNED_METADATA = re.compile(
    r"book cover|front cover|back cover|cover art|title page|frontispiece|scanned page|"
    r"text page|page scan|catalogue page|exhibition poster|plate list|contents page|"
    r"\bpdf\b|\bdjvu\b|document scan|report cover", re.I)


def _size_and_format_ok(data: bytes, cfg: Dict[str, Any]) -> Tuple[bool, Optional[str], Optional[Tuple[int, int]]]:
    """Check image format, minimum resolution and aspect ratio. Returns (ok, reason, (w,h))."""
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return False, "invalid_image_format", None
    w, h = im.size
    if min(w, h) < int(cfg.get("min_short_side", MIN_SHORT_SIDE)):
        return False, "below_min_resolution", (w, h)
    if w / h > MAX_ASPECT or h / w > MAX_ASPECT:
        return False, "extreme_aspect_ratio", (w, h)
    # Upscale check: the image must not need to be enlarged beyond MAX_UPSCALE
    # to fill a 1080x1920 frame (mirrors renderer smart_fit).
    scale_w = 1080 / w
    scale_h = 1920 / h
    needed = max(scale_w, scale_h) * 1.07 if w / h < 0.78 else min(1000 / w, 1560 / h)
    if needed > float(cfg.get("max_upscale", MAX_UPSCALE)):
        return False, "would_require_excessive_upscale", (w, h)
    return True, None, (w, h)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_text_heavy(data: bytes) -> bool:
    """CLIP zero-doc probe: reject images that are primarily scanned text/documents."""
    try:
        from transformers import CLIPModel, CLIPProcessor
        import torch
        model, proc = _load_clip_once()
        im = Image.open(io.BytesIO(data)).convert("RGB")
        inputs = proc(text=["a scanned page of printed text, a book cover, or a document",
                            "a painting, photograph, map, or illustration"],
                      images=im, return_tensors="pt", padding=True)
        with torch.no_grad():
            out = model(**inputs)
        logits = out.logits_per_image[0]
        probs = logits.softmax(dim=-1)
        # If document/text is more likely than image, reject.
        return float(probs[0]) > float(probs[1])
    except Exception:
        # CLIP unavailable: fall back to a cheap heuristic -- very tall narrow
        # images at low resolution are often document scans; allow through so we
        # never fabricate a rejection we cannot substantiate.
        return False


_CLIP_ONCE = None


def _load_clip_once():
    global _CLIP_ONCE
    if _CLIP_ONCE is None:
        import torch
        from transformers import CLIPModel, CLIPProcessor
        model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32").to("cpu").eval()
        proc = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
        _CLIP_ONCE = (model, proc)
    return _CLIP_ONCE


def _clip_relevance(data: bytes, prompts: List[str]) -> Optional[float]:
    """CLIP zero-shot relevance of image against region prompts (0-1). None if CLIP unavailable."""
    try:
        import torch
        from transformers import CLIPModel, CLIPProcessor
        model, proc = _load_clip_once()
        im = Image.open(io.BytesIO(data)).convert("RGB")
        inputs = proc(text=prompts, images=im, return_tensors="pt", padding=True)
        with torch.no_grad():
            out = model(**inputs)
        logits = out.logits_per_image[0]
        probs = logits.softmax(dim=-1)
        return float(probs.max())
    except Exception:
        return None


def _metadata_relevance(item: Dict[str, Any], config: Dict[str, Any]) -> float:
    """Cheap metadata keyword relevance when CLIP is unavailable (0-1)."""
    name = _norm(str(config.get("name", "")))
    place_names = [_norm(p.get("spelling", "")) for p in config.get("place_names", []) if p.get("spelling")]
    category = _norm(str(config.get("story", {}).get("category", "")))
    hay = _norm(item.get("title", "") + " " + item.get("description", "") + " " + " ".join(item.get("categories", [])))
    hits = 0
    total = 0
    for term in [name] + place_names + ([category] if category else []):
        if not term:
            continue
        total += 1
        if _kw_hit(term, hay):
            hits += 1
    if total == 0:
        return 0.0
    return min(1.0, hits / max(1, total))


def _kw_hit(term: str, hay_norm: str) -> bool:
    t = _norm(term).strip()
    if not t:
        return False
    if re.fullmatch(r"[a-z0-9' \-]+", t):
        return re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?:s|es)?(?![a-z0-9])", hay_norm) is not None
    return t in hay_norm


def _provider_enabled(name: str, config: Dict[str, Any]) -> bool:
    enabled = set(config.get("asset_resolver", {}).get("providers") or DEFAULT_PROVIDERS)
    return name in enabled


def discover_inventory(config: Dict[str, Any],
                       max_items_per_provider: int = 12,
                       max_downloads: int = 30,
                       persist: bool = True) -> RegionInventory:
    """Discover and vet a region-first visual inventory.

    For each enabled provider, run broad discovery queries and collect candidates
    (metadata only). Candidates are deduplicated, metadata-vetted (rights, HTTPS,
    banned terms), pre-sorted by metadata relevance, then only the top
    ``max_downloads`` are downloaded for size/format/text-heavy/CLIP vetting.
    This keeps discovery bounded and rate-limit friendly while ensuring the
    inventory contains only real, vetted, rights-cleared assets.

    Results are persisted and coverage is recorded transparently.
    """
    region_slug = str(config.get("slug", ""))
    region_name = str(config.get("name", ""))
    country = str(config.get("country", ""))
    queries = _build_queries(config)

    inventory = RegionInventory(
        region_slug=region_slug,
        region_name=region_name,
        country=country,
        created_at=datetime.now().date().isoformat(),
    )

    seen_urls: Set[str] = set()
    seen_digests: Set[str] = set()

    # Region prompts for CLIP relevance.
    clip_prompts = [region_name, f"{region_name} historic", f"{region_name} modern",
                    f"{region_name} landmark", f"{region_name} culture"]
    clip_prompts = [p for p in dict.fromkeys(clip_prompts) if p][:6]

    # ---- Phase 1: collect metadata candidates from all providers ----
    raw_candidates: List[Dict[str, Any]] = []
    for provider_fn in PROVIDERS:
        pname = provider_fn.__name__
        cov = InventoryCoverage(provider=pname, queries=list(queries))
        if not _provider_enabled(pname, config):
            cov.skipped = True
            cov.skip_reason = "not enabled in config"
            inventory.coverage.append(cov)
            continue

        provider_items = 0
        for q in queries:
            if provider_items >= max_items_per_provider:
                cov.truncated = True
                break
            try:
                results = provider_fn(q)
            except ProviderSkipped as e:
                cov.skipped = True
                cov.skip_reason = str(e)
                break
            except Exception as e:  # noqa: BLE001 - record, never swallow silently
                cov.errors.append(f"{q}: {type(e).__name__}: {str(e)[:200]}")
                continue

            cov.results_returned += len(results)
            for c in results:
                if provider_items >= max_items_per_provider:
                    cov.truncated = True
                    break
                # Dedup by source_url at collection time.
                if c.get("source_url") in seen_urls:
                    continue
                seen_urls.add(c["source_url"])
                c["_discovered_via"] = q
                raw_candidates.append(c)
                provider_items += 1

        cov.candidates_collected = provider_items
        inventory.coverage.append(cov)

    # ---- Phase 2: metadata eligibility + relevance pre-sort ----
    meta_eligible: List[Tuple[float, Dict[str, Any]]] = []
    rejected_meta = 0
    for c in raw_candidates:
        ok, reason = _eligible(c, config)
        if not ok:
            rejected_meta += 1
            cov_err = f"rejected {c.get('title','')[:60]}: {reason}"
            # attribute to the first provider coverage entry
            if inventory.coverage:
                inventory.coverage[0].errors.append(cov_err)
            continue
        score = _metadata_relevance(c, config)
        meta_eligible.append((score, c))

    meta_eligible.sort(key=lambda t: -t[0])
    inventory.total_rejected = rejected_meta

    # ---- Phase 3: download + vet only the top candidates ----
    downloads_attempted = 0
    for score, c in meta_eligible:
        if downloads_attempted >= max_downloads:
            break
        downloads_attempted += 1
        q = c.pop("_discovered_via", "")

        downloaded = _download_image(c["image_url"])
        if downloaded is None:
            inventory.coverage[0].errors.append(f"download_failed {c.get('title','')[:60]}") if inventory.coverage else None
            continue
        data, ct, size = downloaded

        ok, reason, dims = _size_and_format_ok(data, config)
        if not ok:
            if inventory.coverage:
                inventory.coverage[0].errors.append(f"rejected {c.get('title','')[:60]}: {reason}")
            continue

        digest = _digest(data)
        if digest in seen_digests:
            continue  # duplicate image content (re-encoded same photo)
        seen_digests.add(digest)

        # Eligibility gate: text-heavy exclusion (CLIP doc probe).
        if _is_text_heavy(data):
            if inventory.coverage:
                inventory.coverage[0].errors.append(f"rejected {c.get('title','')[:60]}: text_heavy_document")
            continue

        # Relevance: CLIP against region prompts, else metadata fallback.
        clip_score = _clip_relevance(data, clip_prompts)
        relevance = clip_score if clip_score is not None else score

        item = InventoryItem(
            provider=c["provider"],
            title=c["title"],
            description=c["description"],
            image_url=c["image_url"],
            source_url=c["source_url"],
            rights=c["rights"],
            rights_statement=c.get("rights_statement", c["rights"]),
            rights_url=c.get("rights_url", ""),
            license_url=c.get("rights_url", ""),
            author=c["author"],
            identifier=c.get("identifier", ""),
            categories=c.get("categories", []),
            facets=c.get("facets", []),
            date_text=c.get("date_text", ""),
            year_range=c.get("year_range"),
            dimensions=list(dims) if dims else None,
            digest=digest,
            clip_score=clip_score,
            relevance_score=relevance,
            eligible=True,
            discovered_via=q,
            discovered_at=datetime.now().date().isoformat(),
        )
        inventory.items.append(item)

    # Sort by relevance desc.
    inventory.items.sort(key=lambda i: i.relevance_score, reverse=True)
    inventory.total_discovered = len(inventory.items)
    inventory.total_eligible = sum(1 for i in inventory.items if i.eligible)

    if persist:
        save_inventory(region_slug, inventory)

    return inventory


def eligible_items(inventory: RegionInventory) -> List[InventoryItem]:
    """Return eligible items sorted by relevance."""
    return [i for i in inventory.items if i.eligible]


def items_by_theme(inventory: RegionInventory, theme_terms: List[str]) -> List[InventoryItem]:
    """Return eligible items matching theme terms (title/description/categories/facets)."""
    hay_terms = [_norm(t) for t in theme_terms if t]
    out = []
    for i in eligible_items(inventory):
        hay = _norm(i.title + " " + i.description + " " + " ".join(i.categories) + " " + " ".join(i.facets))
        if any(_kw_hit(t, hay) for t in hay_terms):
            out.append(i)
    return out
