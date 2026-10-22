#!/usr/bin/env python3
"""Rights-first archival image discovery with CPU CLIP ranking.

Uses official collection APIs only. API-key providers are enabled when their
documented key environment variable is configured; no key is fabricated.

Every rejection is counted so a failed scene explains itself (see ResolverError).
"""
from __future__ import annotations
import hashlib, html as _html, io, json, os, re, time, datetime, unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path
import requests
from PIL import Image

UA = "RegionHistoryShort/2.2 (https://github.com/Dopeteck/history-of-places-video-maker)"
S = requests.Session()
S.headers.update({"User-Agent": UA})
ALLOWED = ("public domain", "public domain mark", "cc0", "cc by", "cc-by",
           "cc by-sa", "cc-by-sa", "creative commons attribution",
           "creative commons zero", "no known copyright restrictions",
           "no known restrictions on publication")
BANNED = re.compile(r"book cover|front cover|back cover|cover art|title page|frontispiece|scanned page|text page|page scan|catalogue page|exhibition poster|plate list|contents page|pdf|djvu|document scan|report cover", re.I)
# Zero-shot CLIP probes (two-way softmax, so absolute cosine scale does not matter).
DOC_PAIR = ("a scanned page of printed text, a book cover, or a document",
            "a painting, photograph, map, or illustration")
MODERN_PAIR = ("a modern colour photograph taken in recent years",
               "an old painting, woodblock print, drawing, or historical black and white photograph")
GENERIC_WORDS = {"historical", "historic", "history", "photograph", "photo", "illustration", "old",
                 "image", "images", "panorama", "period", "early", "modern", "contemporary", "of", "the", "and", "in", "before", "after", "during", "from", "with", "near"}
STATS = Counter()          # per-scene rejection counters, reset in resolve_scene_image
FOUND = Counter()          # candidates discovered per provider
TOP_REJECTED = []          # a few rejected candidates with reasons
PROVIDER_ERRORS = {}       # provider -> last error text (whole run)
_LAST_API_REQUEST = 0.0
_LAST_MET_REQUEST = 0.0
_FAILS = Counter()         # consecutive failures per provider (whole run)


class ResolverError(RuntimeError):
    """Raised when no candidate passes; message ends with `Diagnostics: {json}` for the approval board."""
    def __init__(self, message, diagnostics):
        self.diagnostics = diagnostics
        super().__init__(f"{message} Diagnostics: {json.dumps(diagnostics, ensure_ascii=False)}")


class ProviderSkipped(RuntimeError):
    """A provider cannot run (e.g. API key not configured). Reported as 'skipped', never as an empty result."""


def _https(url):
    """Providers return http:// ids and protocol-relative (//cdn...) image URLs; both are valid https resources."""
    u = str(url or "").strip()
    if u.startswith("//"): return "https:" + u
    if u.startswith("http://"): return "https://" + u[len("http://"):]
    return u


def _clean(value):
    """Strip HTML tags/entities from provider text (Commons descriptions and artists are HTML)."""
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()


def _norm(text):
    """Lower-case and strip diacritics so 'Koishikawa K\u014drakuen' matches 'korakuen'."""
    t = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in t if not unicodedata.combining(ch)).lower()


def _kw_hit(term, hay_norm):
    """Whole-word (plural-tolerant) match for Latin terms; substring match for CJK. `hay_norm` is already _norm()ed."""
    t = _norm(term).strip()
    if not t: return False
    if re.fullmatch(r"[a-z0-9' \-]+", t):
        return re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?:s|es)?(?![a-z0-9])", hay_norm) is not None
    return t in hay_norm


def _find_term(text, terms):
    """First banned term found as a whole word in text, else None (so the diagnostics can say which rule fired)."""
    t = _norm(text)
    for x in terms:
        x = str(x).strip()
        if x and re.search(r"(?<!\w)" + re.escape(_norm(x)) + r"(?!\w)", t):
            return x
    return None


def _years(text):
    """Four-digit years only; 3-digit numbers (plate 123, DSC 101) are not dates."""
    return [int(y) for y in re.findall(r"(?<!\d)(1[0-9]{3}|20[0-2][0-9])(?!\d)", str(text or ""))]


def _as_list(v):
    if v is None: return []
    if isinstance(v, (list, tuple)): return [str(i) for i in v if i not in (None, "")]
    return [str(v)] if str(v).strip() else []


def _needs_attribution(rights):
    x = str(rights or "").lower()
    return "cc by" in x or "cc-by" in x or "attribution" in x


def _has_author(author):
    return str(author or "").strip().lower() not in ("", "not stated", "unknown", "unknown author", "none", "n/a")


def _get(url, params=None, timeout=10):
    """Request with provider pacing and Retry-After-aware transient retries.

    The Met API has returned 403 bot-protection pages during bursts. Pace every
    Met request, including object lookups, and do not retry deterministic 403s.
    """
    global _LAST_API_REQUEST, _LAST_MET_REQUEST
    last = None
    for attempt in range(2):
        try:
            is_wikimedia = "wikimedia.org/w/api.php" in url or "wikidata.org/w/api.php" in url
            is_met = "collectionapi.metmuseum.org/public/collection/" in url
            if is_wikimedia:
                wait = 0.65 - (time.monotonic() - _LAST_API_REQUEST)
                if wait > 0: time.sleep(wait)
            elif is_met:
                wait = 1.0 - (time.monotonic() - _LAST_MET_REQUEST)
                if wait > 0: time.sleep(wait)
            r = S.get(url, params=params, timeout=timeout)
            if is_wikimedia:
                _LAST_API_REQUEST = time.monotonic()
            elif is_met:
                _LAST_MET_REQUEST = time.monotonic()
            if r.status_code == 429 or 500 <= r.status_code < 600:
                retry_after = r.headers.get("Retry-After", "")
                try: delay = min(8.0, max(1.0, float(retry_after)))
                except (TypeError, ValueError): delay = 2.0 * (attempt + 1)
                last = requests.HTTPError(f"HTTP {r.status_code}")
                if attempt == 0: time.sleep(delay); continue
                break
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last = e
            # 403 usually indicates access/bot protection, not a transient network
            # failure. Retrying immediately makes the block more likely to persist.
            if isinstance(e, requests.HTTPError) and getattr(e.response, "status_code", None) == 403:
                break
            if attempt == 1: break
            time.sleep(1.0)
    raise last


def _rights_ok(rights):
    x = str(rights or "").strip().lower()
    if not x or any(k in x for k in ("unknown", "copyright", "all rights reserved",
                                      "noncommercial", "non-commercial", "no derivatives",
                                      "cc by-nc", "cc-by-nc", "cc by-nd", "cc-by-nd")):
        # "no known copyright restrictions" is an explicit open statement; handle it before rejecting on "copyright".
        if "no known copyright restrictions" in x and not any(k in x for k in ("noncommercial", "non-commercial", "all rights reserved")):
            return True
        return False
    return any(k in x for k in ALLOWED) or "creativecommons.org/licenses/by/" in x or "creativecommons.org/licenses/by-sa/" in x or "creativecommons.org/publicdomain/" in x


def _has_term(text, terms):
    """Whole-word match so banned 'page' does not reject 'pageant' and 'book' does not reject 'bookshop'."""
    return _find_term(text, terms) is not None


def _candidate(provider, title, description, image_url, source_url, rights, rights_url, author="", identifier="", categories=None, date_text="", facets=None, year_range=None):
    # Provider metadata is not schema-stable: normalize values before string operations.
    if isinstance(author, (list, tuple)):
        author = "; ".join(str(a.get("description", a) if isinstance(a, dict) else a) for a in author)
    title, description, author, rights = _clean(title), _clean(description), _clean(author), _clean(rights)
    image_url, source_url = _https(image_url), _https(source_url)
    rights_url = _https(rights_url) if str(rights_url or "").startswith(("http://", "//")) else str(rights_url or "")
    identifier = str(identifier or "")
    if not title:
        STATS["no_title"] += 1; return None
    if not image_url:
        STATS["no_image_url"] += 1; return None
    if not source_url:
        STATS["no_source_url"] += 1; return None
    if not rights or not _rights_ok(rights):
        STATS["rights"] += 1; return None
    if BANNED.search(title + " " + description + " " + image_url):
        STATS["banned_metadata"] += 1; return None
    if not image_url.startswith("https://") or not source_url.startswith("https://"):
        STATS["non_https"] += 1; return None
    return {"provider": provider, "title": title, "description": description, "categories": [_clean(x) for x in (categories or [])],
            "facets": [_clean(x) for x in (facets or []) if _clean(x)], "year_range": year_range, "date_text": _clean(date_text),
            "image_url": image_url.split("#")[0], "source_url": source_url, "rights_statement": rights,
            "rights_url": rights_url, "author": author or "Not stated", "identifier": identifier}


def _note_reject(reason, c, extra=None):
    """Count every rejection; keep up to 3 example rows per reason so late-stage reasons (CLIP, dates) are never crowded out."""
    STATS[reason] += 1
    if sum(1 for r in TOP_REJECTED if r["reason"] == reason) < 3 and len(TOP_REJECTED) < 40:
        row = {"reason": reason, "provider": c.get("provider"), "title": c.get("title", "")[:90], "source_url": c.get("source_url")}
        if extra: row.update(extra)
        TOP_REJECTED.append(row)


def _display_scale(w, h):
    """Upscale factor the renderer (smart_fit) applies to this image on a 1080x1920 frame."""
    if w / h < .78:
        return max(1080 / w, 1920 / h) * 1.07
    return min(1000 / w, 1560 / h)


def _size_ok(im, cfg):
    w, h = im.size
    if min(w, h) < int(cfg.get("min_short_side", 600)): return False
    if w / h > 3.0 or h / w > 3.0: return False
    return _display_scale(w, h) <= float(cfg.get("max_upscale", 1.25))


def _best_loc_image(urls):
    """LoC image_url entries are protocol-relative (//cdn.loc.gov/...) and carry dimensions in the fragment
    (#h=..&w=..). Normalise to https and take the largest."""
    best, best_w = None, -1
    for u in urls or []:
        u = _https(u) if isinstance(u, str) else ""
        if not u.startswith("https://"): continue
        m = re.search(r"[#&]w=(\d+)", u)
        w = int(m.group(1)) if m else (0 if re.search(r"150px|thumb", u) else 1)
        if w > best_w: best, best_w = u, w
    return best.split("#")[0] if best else None


def build_queries(scene, config=None, limit=6):
    """Entities first, then planner tags, then search variants plus short forms; museum APIs prefer short queries."""
    raw = list(scene.get("named_entities", []) or scene.get("entities", [])) + list(scene.get("visual_tags", []) or scene.get("asset_tags", [])) + list(scene.get("image_search_terms", []))
    out = []
    for q in raw:
        q = str(q).strip()
        if q and q not in out: out.append(q)
    if not out and scene.get("title"):
        out = [str(scene["title"])]          # a scene with no planner terms must still be searchable
    for q in list(out):
        words = [w for w in re.findall(r"[\w'-]+", q) if w.lower() not in GENERIC_WORDS]
        short = " ".join(words[:3])
        if short and short not in out and len(words) >= 2: out.append(short)
    # interleave: keep entities/tags in front, but do not let long variants crowd out short ones
    long_q = [q for q in out if len(q.split()) > 3]
    short_q = [q for q in out if len(q.split()) <= 3]
    merged = []
    for a, b in zip(short_q + [None] * len(long_q), long_q + [None] * len(short_q)):
        for x in (a, b):
            if x and x not in merged: merged.append(x)
    merged = [q for q in merged if q.split() and q.split()[-1].lower() not in GENERIC_WORDS]
    # Collapse near-identical variants without discarding the longer, entity-specific query.
    unique = []
    for q in merged:
        norm = _norm(q)
        if norm and not any(norm == _norm(x) or (len(norm.split()) >= 2 and norm in _norm(x)) for x in unique): unique.append(q)
    return unique[:limit]


def _commons_members(cat, limit=50):
    """Files of a Commons category as candidates (rights and metadata gates applied)."""
    payload=_get("https://commons.wikimedia.org/w/api.php", {"action":"query","format":"json",
      "generator":"categorymembers","gcmtitle":"Category:"+cat.replace("Category:",""),"gcmtype":"file","gcmlimit":limit,
      "prop":"imageinfo|categories","cllimit":30,"iiprop":"url|extmetadata|size","iiurlwidth":1920})
    out=[]
    for p in payload.get("query",{}).get("pages",{}).values():
        ii=(p.get("imageinfo") or [{}])[0]; m=ii.get("extmetadata",{})
        cats=[x.get("title","").removeprefix("Category:") for x in p.get("categories",[]) if x.get("title")]
        date_text=" ".join(str(m.get(k,{}).get("value","")) for k in ("DateTimeOriginal","DateTime","ObjectDate"))
        c=_candidate("Wikimedia Commons",p.get("title",""),m.get("ImageDescription",{}).get("value",""),
            ii.get("thumburl") or ii.get("url"),ii.get("descriptionurl"),
            m.get("LicenseShortName",{}).get("value",""),m.get("LicenseUrl",{}).get("value",""),
            m.get("Artist",{}).get("value",""),p.get("pageid",""),cats,date_text)
        if c: c["width"]=ii.get("width",0); c["height"]=ii.get("height",0); out.append(c)
    return out

@lru_cache(maxsize=64)
def _commons_category_cached(place):
    """Resolve a name through Wikidata, then query its Commons category."""
    q = _get("https://www.wikidata.org/w/api.php", {"action":"wbsearchentities","search":place,
          "language":"en","format":"json","limit":5})
    entity = next((x for x in q.get("search",[]) if x.get("id")), None)
    if not entity: return []
    data = _get("https://www.wikidata.org/w/api.php", {"action":"wbgetentities","ids":entity["id"],
                  "props":"claims|labels|sitelinks","languages":"en","format":"json"})
    ent = data.get("entities",{}).get(entity["id"],{})
    cats = []
    for cl in ent.get("claims",{}).get("P373",[]):
        v=cl.get("mainsnak",{}).get("datavalue",{}).get("value")
        if v: cats.append(v)
    commons = ent.get("sitelinks",{}).get("commonswiki",{}).get("title","")
    if commons.startswith("Category:"): cats.insert(0,commons.split(":",1)[1])
    out=[]
    for cat in list(dict.fromkeys(cats))[:3]:
        out.extend(_commons_members(cat))
    return out

def _commons_category(place):
    return [dict(c) for c in _commons_category_cached(str(place))]

def _commons_search(query):
    payload=_get("https://commons.wikimedia.org/w/api.php", {"action":"query","format":"json",
       "generator":"search","gsrsearch":query,"gsrnamespace":6,"gsrlimit":25,
       "prop":"imageinfo|categories","cllimit":30,"iiprop":"url|extmetadata|size","iiurlwidth":2400})
    out=[]
    for p in payload.get("query",{}).get("pages",{}).values():
        ii=(p.get("imageinfo") or [{}])[0]; m=ii.get("extmetadata",{})
        cats=[x.get("title","").removeprefix("Category:") for x in p.get("categories",[]) if x.get("title")]
        date_text=" ".join(str(m.get(k,{}).get("value","")) for k in ("DateTimeOriginal","DateTime","ObjectDate"))
        c=_candidate("Wikimedia Commons",p.get("title",""),m.get("ImageDescription",{}).get("value",""),
          ii.get("thumburl") or ii.get("url"),ii.get("descriptionurl"),
          m.get("LicenseShortName",{}).get("value",""),m.get("LicenseUrl",{}).get("value",""),
          m.get("Artist",{}).get("value",""),p.get("pageid",""),cats,date_text)
        if c: c["width"]=ii.get("width",0); c["height"]=ii.get("height",0); c["_from_search"]=True; out.append(c)
    return out

def _loc_rights(x):
    item = x.get("item") if isinstance(x.get("item"), dict) else {}
    for src in (x, item):
        for k in ("rights", "rights_advisory", "rights_information"):
            v = " ".join(_as_list(src.get(k)))
            if v.strip(): return v
    return ""

def _loc(query):
    try:
        payload = _get("https://www.loc.gov/search/", {"q": query, "fo": "json", "c": 50, "sp": 1}, timeout=25)
    except requests.HTTPError as e:
        # loc.gov answers a search with no results with HTTP 404 (and a JSON body); that is "no results", not an outage.
        if getattr(getattr(e, "response", None), "status_code", None) == 404: return []
        raise
    out = []
    for x in payload.get("results", []):
        image = x.get("image_url") or []
        resources = x.get("resources") or []
        if isinstance(image, str): image = [image]
        if not image and resources: image = resources[0].get("image") or []
        if isinstance(image, str): image = [image]
        # `id` is an http:// URL "for historical reasons"; `url` is the https page. Prefer `url`.
        c = _candidate("Library of Congress", x.get("title", ""), " ".join(_as_list(x.get("description"))),
                       _best_loc_image(image), x.get("url") or x.get("id"), _loc_rights(x),
                       "https://www.loc.gov/rr/print/res/rights.html", "; ".join(_as_list(x.get("contributor"))), x.get("id"),
                       date_text=" ".join(_as_list(x.get("date"))),
                       facets=_as_list(x.get("subject")) + _as_list(x.get("contributor")) + _as_list(x.get("original_format")) + _as_list(x.get("location")))
        if c: out.append(c)
    return out

def _met(query):
    # The Met retired /v1/search on 2026-10-01. Use the paginated v1.1 API.
    ids = _get("https://collectionapi.metmuseum.org/public/collection/v1.1/search",
               {"q": query, "hasImages": "true", "limit": 100, "offset": 0}).get("objectIDs") or []
    out = []
    # Each object lookup is a separate API request; keep the per-query budget small
    # and paced to avoid bursts that trigger upstream bot protection.
    for oid in ids[:8]:
        try:
            x = _get(f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}")
        except Exception as e:
            print(f"Met object lookup failed for {oid}: {type(e).__name__}: {e}")
            continue
        if x.get("isPublicDomain") is not True: continue
        b, e_ = x.get("objectBeginDate"), x.get("objectEndDate")
        yr = (int(b), int(e_)) if isinstance(b, int) and isinstance(e_, int) and b > 0 else None
        tags = [t.get("term") for t in (x.get("tags") or []) if isinstance(t, dict)]
        c = _candidate("The Metropolitan Museum of Art", x.get("title", ""), x.get("objectDate", ""),
                       x.get("primaryImage") or x.get("primaryImageSmall"), x.get("objectURL"),
                       "Public Domain (CC0)", "https://www.metmuseum.org/about-the-met/policies-and-documents/open-access",
                       x.get("artistDisplayName", ""), oid, date_text=x.get("objectDate", ""), year_range=yr,
                       facets=[x.get("artistDisplayName"), x.get("culture"), x.get("period"), x.get("classification"),
                               x.get("objectName"), x.get("medium"), x.get("department")] + tags)
        if c: out.append(c)
    return out

def _aic(query):
    # image_id is a TOP-LEVEL artwork field and must be requested; `thumbnail` only holds lqip/width/height/alt_text.
    payload = _get("https://api.artic.edu/api/v1/artworks/search",
                   {"q": query, "limit": 50, "query[term][is_public_domain]": "true",
                    "fields": "id,title,artist_title,date_display,date_start,date_end,is_public_domain,image_id,classification_title,medium_display,place_of_origin,style_title,subject_titles"})
    out = []
    for x in payload.get("data", []):
        if x.get("is_public_domain") is not True: continue
        iid = x.get("image_id")
        # AIC's IIIF server documents /full/843,/0/default.jpg for downloads; ask for twice that so tall prints pass the size gate.
        image = f"https://www.artic.edu/iiif/2/{iid}/full/1686,/0/default.jpg" if iid else None
        ds, de = x.get("date_start"), x.get("date_end")
        yr = (int(ds), int(de)) if isinstance(ds, int) and isinstance(de, int) and ds > 0 else None
        c = _candidate("Art Institute of Chicago", x.get("title", ""), x.get("date_display", ""),
                       image, "https://www.artic.edu/artworks/" + str(x.get("id")),
                       "Public Domain (CC0)", "https://www.artic.edu/open-access/open-access-images",
                       x.get("artist_title", ""), x.get("id"), date_text=x.get("date_display", ""), year_range=yr,
                       facets=[x.get("artist_title"), x.get("classification_title"), x.get("medium_display"), x.get("place_of_origin"),
                               x.get("style_title")] + _as_list(x.get("subject_titles")))
        if c: out.append(c)
    return out

def _cleveland(query):
    payload = _get("https://openaccess-api.clevelandart.org/api/artworks/", {"q": query, "limit": 50})
    out = []
    for x in payload.get("data", []):
        rights = x.get("share_license_status", "")
        if str(rights).lower() not in ("cc0", "public domain", "cc0 public domain"): continue
        im = x.get("images") or {}
        # `web` is the small derivative, `print` the large one; tall prints need the large file to pass the size gate.
        url = (im.get("print") or {}).get("url") or (im.get("web") or {}).get("url") or (im.get("full") or {}).get("url")
        b, e_ = x.get("creation_date_earliest"), x.get("creation_date_latest")
        yr = (int(b), int(e_)) if isinstance(b, int) and isinstance(e_, int) and b > 0 else None
        c = _candidate("Cleveland Museum of Art", x.get("title", ""), x.get("creation_date", ""), url,
                       x.get("url"), rights, "https://www.clevelandart.org/open-access", x.get("creators") or "", x.get("id"),
                       date_text=x.get("creation_date", ""), year_range=yr,
                       facets=_as_list(x.get("culture")) + [x.get("type"), x.get("department"), x.get("technique")])
        if c: out.append(c)
    return out

def _smithsonian(query):
    key = os.getenv("SMITHSONIAN_API_KEY")
    if not key: raise ProviderSkipped("SMITHSONIAN_API_KEY is not set (add it as an Actions secret and expose it to the job)")
    payload = _get("https://api.si.edu/openaccess/api/v1.0/search",
                   {"api_key": key, "q": query, "rows": 50, "online_media_type": "Images"}, timeout=25)
    out = []
    for x in payload.get("response", {}).get("rows", []):
        cdata = x.get("content", {}); desc = cdata.get("descriptiveNonRepeating", {})
        title = (desc.get("title") or {}).get("content") or x.get("title", "")
        for m in (desc.get("online_media") or {}).get("media", []):
            usage = m.get("usage", {}); rights_text = usage.get("access", "")
            if str(m.get("type", "")).lower() not in ("images", "image"): continue
            if not _rights_ok(rights_text): continue
            c = _candidate("Smithsonian", title, x.get("title", ""), m.get("content"), desc.get("record_link"),
                           rights_text, "https://www.si.edu/openaccess", "", x.get("id"))
            if c: out.append(c); break
    return out

def _europeana(query):
    """Search Europeana for openly reusable, directly viewable, high-resolution collection images.

    Keep the key in the query params (never logs); exclude closed/restricted rights at
    the API and independently re-check the item rights locally before accepting it.
    """
    key = os.getenv("EUROPEANA_API_KEY")
    if not key: raise ProviderSkipped("EUROPEANA_API_KEY is not set (add it as an Actions secret and expose it to the job)")
    payload = _get("https://api.europeana.eu/record/v2/search.json",
                   {"wskey": key, "query": query, "media": "true", "reusability": "open",
                    "qf": "IMAGE_SIZE:extra_large", "rows": 50, "profile": "rich"}, timeout=25)
    out = []
    for x in payload.get("items", []):
        rights = _as_list(x.get("rights"))
        rights = next((r for r in rights if _rights_ok(r)), "")
        # Prefer the original item media URL; never promote a tiny Europeana preview to production quality.
        # Many content providers still return http:// URLs; upgrade the scheme and let the download gate decide.
        media = _as_list(x.get("edmIsShownBy"))
        image = _https(media[0]) if media else None
        title = _as_list(x.get("title")) or [""]
        desc = _as_list(x.get("dcDescription")); creators = _as_list(x.get("dcCreator")); subjects = _as_list(x.get("dcSubject"))
        types = _as_list(x.get("type")); providers = _as_list(x.get("dataProvider") or x.get("edmDataProvider"))
        dates = _as_list(x.get("year") or x.get("dcDate"))
        shown_at = _as_list(x.get("edmIsShownAt"))
        source = x.get("guid") or (shown_at[0] if shown_at else None)
        c = _candidate("Europeana", title[0], " ".join(desc + subjects + types + providers), image, source,
                       rights, rights, creators[0] if creators else "", x.get("id"),
                       categories=subjects + types + providers, date_text=" ".join(dates), facets=creators + subjects + types)
        if c: out.append(c)
    return out

def _dpla(query):
    key = os.getenv("DPLA_API_KEY")
    if not key: raise ProviderSkipped("DPLA_API_KEY is not set")
    payload = _get("https://api.dp.la/v2/items", {"q": query, "page_size": 50, "api_key": key})
    out = []
    for x in payload.get("docs", []):
        obj = x.get("object", ""); rights = x.get("rights", "")
        c = _candidate("DPLA", x.get("sourceResource", {}).get("title", ""),
                       str(x.get("sourceResource", {}).get("description", "")), obj, x.get("isShownAt"), rights,
                       x.get("rights"), "", x.get("id"))
        if c: out.append(c)
    return out

def _nypl(query):
    key = os.getenv("NYPL_API_KEY")
    if not key: raise ProviderSkipped("NYPL_API_KEY is not set")
    payload = _get("https://api.repo.nypl.org/api/v2/items/search", {"q": query, "per_page": 50}, timeout=25)
    out = []
    for x in payload.get("nyplAPI", {}).get("response", {}).get("result", []):
        rights = x.get("rightsStatement") or x.get("rights")
        image = x.get("imageID")
        c = _candidate("NYPL", x.get("title", ""), x.get("description", ""),
                       ("https://iiif.nypl.org/iiif/2/" + str(image) + "/full/2000,/0/default.jpg") if image else None,
                       x.get("itemLink"), rights, x.get("rightsStatementURI"), x.get("creator", ""), x.get("uuid"))
        if c: out.append(c)
    return out

def _nga(query):
    payload = _get("https://www.nga.gov/api/v1/objects", {"q": query, "limit": 50})
    out = []
    for x in payload.get("data", payload if isinstance(payload, list) else []):
        if not (x.get("isPublicDomain") or x.get("is_public_domain")): continue
        c = _candidate("National Gallery of Art", x.get("title", ""), x.get("displaydate", ""),
                       x.get("image_url") or x.get("iiif_url"), x.get("url") or x.get("object_url"),
                       "Public Domain", "https://www.nga.gov/open-access-policy", x.get("artist", ""), x.get("id"))
        if c: out.append(c)
    return out

def _rijksmuseum(query):
    key = os.getenv("RIJKSMUSEUM_API_KEY")
    if not key: raise ProviderSkipped("RIJKSMUSEUM_API_KEY is not set")
    payload = _get("https://www.rijksmuseum.nl/api/en/collection", {"key": key, "q": query, "imgonly": "true", "ps": 50})
    out = []
    for x in payload.get("artObjects", []):
        rights = x.get("copyrightHolder") or x.get("rights")
        c = _candidate("Rijksmuseum", x.get("title", ""), x.get("longTitle", ""),
                       (x.get("webImage") or {}).get("url"), x.get("links", {}).get("web"),
                       rights, x.get("rightsUrl"), x.get("principalOrFirstMaker", ""), x.get("objectNumber"))
        if c: out.append(c)
    return out

def _getty(query):
    key = os.getenv("GETTY_API_KEY")
    if not key: raise ProviderSkipped("GETTY_API_KEY is not set")
    payload = _get("https://api.getty.edu/opencontent/v1/objects", {"q": query, "limit": 50, "api_key": key})
    out = []
    for x in payload.get("data", []):
        rights = x.get("rights") or x.get("rightsStatement")
        c = _candidate("Getty", x.get("title", ""), x.get("description", ""), x.get("image_url"),
                       x.get("url"), rights, x.get("rights_url"), x.get("creator", ""), x.get("id"))
        if c: out.append(c)
    return out

PROVIDERS = [_loc, _met, _aic, _cleveland, _nga, _smithsonian, _europeana, _dpla, _nypl, _rijksmuseum, _getty]
# Endpoints I could not verify offline (NGA, NYPL, Rijksmuseum, Getty) are opt-in via config asset_resolver.providers.
DEFAULT_PROVIDERS = ["_met", "_aic", "_cleveland", "_smithsonian", "_europeana"]

@lru_cache(maxsize=1)
def _load_clip(model_id):
    import torch
    from transformers import CLIPModel, CLIPProcessor
    model=CLIPModel.from_pretrained(model_id).to("cpu").eval()
    return model, CLIPProcessor.from_pretrained(model_id)

def build_clip_prompts(scene, config=None):
    """Short natural-language captions (CLIP truncates text at 77 tokens, so a keyword list is mostly cut off).
    The subject comes first; the image's score is the best cosine over these prompts."""
    place = str((config or {}).get("name", ""))
    modern = scene.get("image_type") == "modern"
    kind = "a photograph of" if modern else "a historical illustration or photograph of"
    ents = [str(e) for e in (scene.get("named_entities") or scene.get("entities") or []) if str(e).strip()]
    tags = [str(t) for t in (scene.get("visual_tags") or scene.get("asset_tags") or []) if str(t).strip()]
    prompts = []
    if scene.get("visual_caption"): prompts.append(str(scene["visual_caption"]))
    if ents: prompts.append(f"{kind} {ents[0]}")
    prompts += [f"{kind} {t}" for t in tags[:2]]
    intent = " ".join(str(scene.get("narration_intent", "")).split()[:24])
    if intent: prompts.append(intent)
    if not prompts: prompts.append(f"{kind} {scene.get('title', '')} {place}".strip())
    out = []
    for p in prompts:
        if p not in out: out.append(p)
    return out[:4]

def _clip_analyze(prompts, image_bytes):
    """Return per-image dicts: best cosine over the scene prompts, P(document-like), P(modern photo)."""
    import torch
    if isinstance(prompts, str): prompts = [prompts]
    prompts = [str(p) for p in prompts]; k = len(prompts)
    model_id = os.getenv("ASSET_CLIP_MODEL", "openai/clip-vit-base-patch32")
    model, processor = _load_clip(model_id)
    images = [Image.open(io.BytesIO(b)).convert("RGB") for b in image_bytes]
    texts = prompts + [DOC_PAIR[0], DOC_PAIR[1], MODERN_PAIR[0], MODERN_PAIR[1]]
    with torch.inference_mode():
        text = processor(text=texts, return_tensors="pt", padding=True, truncation=True)
        ims = processor(images=images, return_tensors="pt")
        # Use CLIPModel.forward outputs for stable projected embeddings (get_*_features changed return types across versions).
        outputs = model(**text, **ims)
        ti, ii = outputs.text_embeds, outputs.image_embeds
        ti = ti / ti.norm(p=2, dim=-1, keepdim=True).clamp(min=1e-12)
        ii = ii / ii.norm(p=2, dim=-1, keepdim=True).clamp(min=1e-12)
        cos = ii @ ti.T                                     # (N, k + 4)
        scale = float(model.logit_scale.exp())
        best = cos[:, :k].max(dim=1).values
        p_doc = torch.softmax(scale * cos[:, k:k + 2], dim=-1)[:, 0]
        p_mod = torch.softmax(scale * cos[:, k + 2:k + 4], dim=-1)[:, 0]
    return [{"score": float(best[i]), "p_document": float(p_doc[i]), "p_modern": float(p_mod[i])} for i in range(len(images))]

def _clip_scores(query, candidates, image_bytes):
    """Backward-compatible: cosine similarity between the query and each image."""
    return [r["score"] for r in _clip_analyze(query, image_bytes)]

def _ocr_available():
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False

def _text_heavy(im):
    import pytesseract
    probe=im.copy(); probe.thumbnail((900,900))
    ocr=pytesseract.image_to_data(probe,output_type=pytesseract.Output.DICT)
    words=[str(w).strip() for w in ocr.get("text",[]) if str(w).strip()]
    conf=[float(v) for v in ocr.get("conf",[]) if str(v).replace(".","",1).isdigit() and float(v)>=35]
    return len(words)>=14 and len(conf)>=10

def _round_robin(cands, cap):
    """Interleave providers so one noisy provider cannot use the whole download budget."""
    by={}
    for c in cands: by.setdefault(c["provider"],[]).append(c)
    out=[]
    while len(out)<cap and any(by.values()):
        for k in list(by):
            if by[k]: out.append(by[k].pop(0))
            if len(out)>=cap: break
    return out

def _download(c):
    last = None
    for attempt in range(2):
        try:
            r=S.get(c["image_url"],timeout=(8,18),stream=True)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                last = requests.HTTPError(f"HTTP {r.status_code} downloading image")
                if attempt == 0:
                    try: delay = min(8.0, max(1.0, float(r.headers.get("Retry-After", "2"))))
                    except ValueError: delay = 2.0
                    time.sleep(delay); continue
                break
            r.raise_for_status()
            if int(r.headers.get("Content-Length") or 0)>35_000_000: raise ValueError("file larger than 35 MB")
            return r.content
        except requests.RequestException as e:
            last=e
            if attempt == 0: time.sleep(1.0); continue
            break
    raise last

def resolve_scene_image(scene, idx, config, asset_dir, used, output):
    """Discover across providers, filter rights/format/size, rank with CLIP, save best."""
    STATS.clear(); FOUND.clear(); TOP_REJECTED.clear(); PROVIDER_ERRORS.clear()
    _FAILS.clear()                       # the breaker protects one scene's request budget; it must not outlive the scene
    status = {}
    def st(name): return status.setdefault(name, {"queries": 0, "returned": 0, "errors": [], "skipped": None})
    rcfg = config.get("asset_resolver", {}) or {}
    queries = build_queries(scene, config, int(scene.get("max_queries", rcfg.get("max_queries", 6))))
    enabled = set(rcfg.get("providers") or DEFAULT_PROVIDERS)
    providers = [p for p in PROVIDERS if p.__name__ in enabled]
    place = config.get("name", "")
    entities = [str(e) for e in (scene.get("named_entities", []) or scene.get("entities", []))]
    per_provider = int(scene.get("max_queries_per_provider", rcfg.get("max_queries_per_provider", 3)))
    candidates = []
    clip_top = []
    # Commons categories: place-aware first, then entity categories (e.g. "Edo Castle").
    for name in dict.fromkeys(([place] if scene.get("image_type") == "modern" else []) + entities[:2]):
        if not name: continue
        s_ = st("commons_category:" + name)
        try:
            got = _commons_category(name); FOUND["commons_category:" + name] += len(got); candidates.extend(got)
            s_["queries"] += 1; s_["returned"] += len(got)
        except Exception as e:
            PROVIDER_ERRORS["commons_category"] = f"{type(e).__name__}: {e}"[:200]; s_["errors"].append(PROVIDER_ERRORS["commons_category"])
            print(f"Commons category lookup unavailable for '{name}': {e}")
    for cat in scene.get("commons_categories", []) or []:
        s_ = st("commons_category:" + str(cat))
        try:
            got = _commons_members(str(cat)); FOUND["commons_category:" + str(cat)] += len(got); candidates.extend(got)
            s_["queries"] += 1; s_["returned"] += len(got)
        except Exception as e:
            PROVIDER_ERRORS["commons_category"] = f"{type(e).__name__}: {e}"[:200]; s_["errors"].append(PROVIDER_ERRORS["commons_category"])
            print(f"Commons category '{cat}' unavailable: {e}")
    for provider in providers:
        name = provider.__name__; s_ = st(name)
        for query in queries[:per_provider]:
            if _FAILS[name] >= 2:                         # two consecutive errors in THIS scene: stop hammering it
                s_["skipped"] = s_["skipped"] or "stopped after 2 consecutive errors in this scene"; break
            try:
                started = time.perf_counter()
                got = provider(query); FOUND[name] += len(got); candidates.extend(got); _FAILS[name] = 0
                s_["queries"] += 1; s_["returned"] += len(got)
                print(f"TIMING provider_search scene={scene.get('title')} provider={name} query={query!r} seconds={time.perf_counter()-started:.2f} found={len(got)}")
            except ProviderSkipped as e:
                s_["skipped"] = str(e); print(f"Provider {name} skipped: {e}"); break
            except Exception as e:
                _FAILS[name] += 1; PROVIDER_ERRORS[name] = f"{type(e).__name__}: {e}"[:200]; s_["errors"].append(PROVIDER_ERRORS[name])
                print(f"Provider {name} unavailable for '{query}': {type(e).__name__}: {e}")
    # Always add targeted Commons text searches. Category results alone are often generic.
    s_ = st("commons_search")
    for query in queries[:per_provider]:
        try:
            started = time.perf_counter()
            got = _commons_search(query)
            FOUND["commons_search"] += len(got); candidates.extend(got); s_["queries"] += 1; s_["returned"] += len(got)
            print(f"TIMING provider_search scene={scene.get('title')} provider=commons_search query={query!r} seconds={time.perf_counter()-started:.2f} found={len(got)}")
        except Exception as e:
            PROVIDER_ERRORS["commons_search"] = f"{type(e).__name__}: {e}"[:200]; s_["errors"].append(PROVIDER_ERRORS["commons_search"])
            print(f"Commons text search unavailable for {query!r}: {e}")

    def diagnostics():
        return {"queries": queries, "providers_found": dict(FOUND), "provider_status": status, "rejected": dict(STATS),
                "top_rejected": list(TOP_REJECTED), "clip_top": clip_top, "provider_errors": dict(PROVIDER_ERRORS),
                "ocr_available": _ocr_available()}
    pinned = scene.get("pinned_image")
    if pinned:
        candidates = [c for c in candidates if c.get("source_url") == pinned or c.get("image_url") == pinned]
        if not candidates:
            STATS["pinned_image_not_found"] += 1
            raise ResolverError(f"Pinned image failed: exact source URL was not returned by configured provider searches: {pinned}", diagnostics())

    # Metadata gates. Text is normalised (HTML stripped, diacritics folded) and matched on whole words; artist, culture,
    # medium and tag metadata count as evidence for the narrated subject.
    required = [str(k) for k in scene.get("required_keywords", [])]
    banned_terms = list(config.get("image_banned_terms", [])) + list(scene.get("banned_keywords", []))
    groups = scene.get("required_keyword_groups", []) or []
    # Identity groups are hard source-metadata evidence requirements. Every
    # group must match; CLIP similarity cannot compensate for a wrong subject.
    identity_groups = scene.get("identity_keyword_groups", []) or []
    soft = str(scene.get("keyword_gate", "hard")).lower() == "soft"
    unique = {}; seen = set()
    for c in candidates:
        key = c["source_url"]
        if key in seen: continue                      # an item returned by several queries is judged (and counted) once
        seen.add(key)
        if not _rights_ok(c["rights_statement"]): _note_reject("rights", c, {"rights": c["rights_statement"][:100]}); continue
        if _needs_attribution(c["rights_statement"]) and not _has_author(c.get("author", "")):
            _note_reject("attribution", c, {"rights": c["rights_statement"][:60]}); continue
        text = _clean(" ".join([c.get("title", ""), c.get("description", ""), " ".join(_as_list(c.get("categories")))]))   # strip any HTML a provider left in
        m = BANNED.search(text)
        matched = m.group(0) if m else _find_term(text, banned_terms)
        if matched: _note_reject("banned_term", c, {"matched": matched}); continue
        hay = _norm(text + " " + " ".join(_as_list(c.get("facets"))))
        hits = sum(1 for k in required if _kw_hit(k, hay))
        c["_hits"] = hits
        if required and hits == 0 and not soft:
            _note_reject("keywords", c, {"required_keywords": required[:8], "checked_text": hay[:160]}); continue
        missing = [gi for gi, group in enumerate(groups) if not any(_kw_hit(t, hay) for t in (group if isinstance(group, list) else [group]))]
        if missing:
            _note_reject("subject_groups", c, {"missing_group_indexes": missing, "checked_text": hay[:160]}); continue
        unique[key] = c
    candidates = sorted(unique.values(), key=lambda c: -c["_hits"])
    if not candidates:
        raise ResolverError(f"No licensed candidates discovered for scene '{scene['title']}'.", diagnostics())
    candidates = _round_robin(candidates, int(rcfg.get("max_downloads", 20)))
    use_ocr = _ocr_available(); ranked = []
    for c in candidates:
        try:
            started = time.perf_counter()
            data = _download(c)
            print(f"TIMING image_download scene={scene.get('title')} provider={c.get('provider')} seconds={time.perf_counter()-started:.2f}")
            if len(data) < 30000 or data[:4] in (b"%PDF", b"AT&TD", b"FORM"): _note_reject("format", c); continue
            im = Image.open(io.BytesIO(data))
            if im.format not in ("JPEG", "PNG", "WEBP", "TIFF"): _note_reject("format", c); continue
            if not _size_ok(im, rcfg): _note_reject("size", c, {"pixels": list(im.size)}); continue
            if use_ocr and _text_heavy(im): _note_reject("ocr_text_heavy", c); continue
            # Normalize by decoded RGB pixels, not compressed file bytes. The same image
            # can arrive from two providers with different JPEG encodings.
            digest = hashlib.sha256(im.convert("RGB").tobytes()).hexdigest()
            if digest in used or c["source_url"] in used: _note_reject("duplicate", c); continue
            c["_bytes"] = data; c["_digest"] = digest; c["_size"] = im.size
            ranked.append(c)
        except Exception as e:
            _note_reject("download", c, {"error": f"{type(e).__name__}: {e}"[:120]})
    if not ranked:
        raise ResolverError(f"No candidates passed explicit-rights, image-format, size and duplicate gates for '{scene['title']}'.", diagnostics())
    # Rank all surviving candidates with a compact CLIP model on CPU, using short natural prompts.
    prompts = build_clip_prompts(scene, config)
    try:
        started = time.perf_counter()
        results = _clip_analyze(prompts, [x["_bytes"] for x in ranked])
        print(f"TIMING clip_analysis scene={scene.get('title')} candidates={len(ranked)} seconds={time.perf_counter()-started:.2f}")
    except Exception as e:
        raise ResolverError(f"CLIP relevance gate unavailable; refusing keyword-only image selection: {type(e).__name__}: {e}", diagnostics()) from e
    for c, r in zip(ranked, results):
        c["_score"] = r["score"]; c["_p_doc"] = r["p_document"]; c["_p_modern"] = r["p_modern"]
    # Record every scored candidate (best 5) so the threshold can be calibrated from real data, even when nothing passes.
    clip_top.extend({"provider": c["provider"], "title": c["title"][:80], "clip_score": round(c["_score"], 3),
                     "p_document": round(c["_p_doc"], 2), "p_modern": round(c["_p_modern"], 2)}
                    for c in sorted(ranked, key=lambda c: -c["_score"])[:5])
    lo, hi = scene.get("min_year"), scene.get("max_year")
    survivors = []
    for c in ranked:
        if c["_p_doc"] > 0.5: _note_reject("document_like", c, {"p_document": round(c["_p_doc"], 2)}); continue
        if scene.get("image_type") == "modern" and c["_p_modern"] < 0.5:
            _note_reject("present_historical", c, {"p_modern": round(c["_p_modern"], 2)}); continue
        if scene.get("image_type") == "historic" and not scene.get("allow_modern_photo", False) and c["_p_modern"] >= 0.5:
            _note_reject("modern_in_historic", c, {"p_modern": round(c["_p_modern"], 2)}); continue
        if scene.get("image_type") == "historic" and c["_p_modern"] < 0.5 and (lo is not None or hi is not None):
            yr = c.get("year_range")
            if not yr:
                ys = _years(" ".join([c.get("date_text", ""), c.get("description", ""), c.get("title", "")]))
                yr = (min(ys), max(ys)) if ys else None
            if yr and ((hi is not None and yr[0] > int(hi)) or (lo is not None and yr[1] < int(lo))):
                _note_reject("date_window", c, {"years": list(yr), "min_year": lo, "max_year": hi}); continue
            if not yr: c.setdefault("_warnings", []).append("date unverified")
        survivors.append(c)
    threshold = float(scene.get("clip_min_score", rcfg.get("clip_min_score", 0.22)))
    # Semantic image relevance must dominate metadata keyword frequency. Keyword hits are a discovery filter, not a score bonus.
    survivors.sort(key=lambda c: (c["_p_modern"] < 0.5, c["_score"]), reverse=True) if scene.get("image_type") == "historic" else survivors.sort(key=lambda c: c["_score"], reverse=True)
    top = survivors[:int(rcfg.get("top_candidates", 5))]
    print(f"CLIP candidates for {scene['title']}: " + ", ".join(f"{x['provider']}={x['_score']:.3f}" for x in top))
    winner = next((c for c in top if c["_score"] >= threshold), None)
    if winner is None:
        for c in top[:3]: _note_reject("clip_below_threshold", c, {"clip_score": round(c["_score"], 3)})
        if not survivors:
            raise ResolverError(f"Every downloaded candidate was rejected by the document/period/date gates for '{scene['title']}'.", diagnostics())
        raise ResolverError(f"No image scored >= {threshold:.2f} with CLIP for '{scene['title']}'. Use configured map/text-card fallback; do not substitute a weak image.", diagnostics())
    data = winner["_bytes"]; im = Image.open(io.BytesIO(data)).convert("RGB")
    filename = f"scene_{idx:02d}.jpg"; path = Path(asset_dir) / filename; im.save(path, quality=94)
    used.add(winner["_digest"]); used.add(winner["source_url"])
    warnings = [] if use_ocr else ["OCR gate unavailable in this environment; relied on CLIP document check only"]
    warnings += winner.get("_warnings", [])
    return im, {"content_sha256": winner["_digest"], "scene": scene["title"], "file": str(path),
      "title": winner["title"], "author": winner["author"], "license": winner["rights_statement"],
      "rights_statement": winner["rights_statement"], "license_url": winner["rights_url"],
      "source_url": winner["source_url"], "provider": winner["provider"], "match_score": winner["_score"],
      "clip_score": winner["_score"], "period_ok": (winner["_p_modern"] >= 0.5) if scene.get("image_type") == "modern" else None,
      "search_variant": "CLIP ranked: " + " | ".join(prompts), "warnings": warnings, "dimensions": list(winner["_size"]),
      "top_candidates": [{"provider": x["provider"], "title": x["title"], "source_url": x["source_url"],
                          "rights_statement": x["rights_statement"], "clip_score": x["_score"]} for x in top]}