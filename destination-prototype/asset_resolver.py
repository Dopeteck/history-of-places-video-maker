#!/usr/bin/env python3
"""Rights-first archival image discovery with CPU CLIP ranking.

Uses official collection APIs only. API-key providers are enabled when their
documented key environment variable is configured; no key is fabricated.

Every rejection is counted so a failed scene explains itself (see ResolverError).
"""
from __future__ import annotations
import hashlib, io, json, os, re, time, datetime
from collections import Counter
from functools import lru_cache
from pathlib import Path
import requests
from PIL import Image

UA = "RegionHistoryShort/2.1 (educational historical video; contact via GitHub)"
S = requests.Session()
S.headers.update({"User-Agent": UA})
ALLOWED = ("public domain", "public domain mark", "cc0", "cc by", "cc-by",
           "cc by-sa", "cc-by-sa", "creative commons attribution",
           "creative commons zero", "no known copyright restrictions",
           "no known restrictions on publication")
BANNED = re.compile(r"book.?cover|cover.?art|title.?page|frontispiece|text.?scan|scanned text|manuscript page|page scan|catalogue|catalog|plate list|contents page|pdf|djvu|document scan|report cover", re.I)
# Zero-shot CLIP probes (two-way softmax, so absolute cosine scale does not matter).
DOC_PAIR = ("a scanned page of printed text, a book cover, or a document",
            "a painting, photograph, map, or illustration")
MODERN_PAIR = ("a modern colour photograph taken in recent years",
               "an old painting, woodblock print, drawing, or historical black and white photograph")
GENERIC_WORDS = {"historical", "historic", "history", "photograph", "photo", "illustration", "old",
                 "image", "images", "panorama", "period", "early", "modern", "contemporary", "of", "the", "and", "in"}
STATS = Counter()          # per-scene rejection counters, reset in resolve_scene_image
FOUND = Counter()          # candidates discovered per provider
TOP_REJECTED = []          # a few rejected candidates with reasons
PROVIDER_ERRORS = {}       # provider -> last error text (whole run)
_FAILS = Counter()         # consecutive failures per provider (whole run)


class ResolverError(RuntimeError):
    """Raised when no candidate passes; message ends with `Diagnostics: {json}` for the approval board."""
    def __init__(self, message, diagnostics):
        self.diagnostics = diagnostics
        super().__init__(f"{message} Diagnostics: {json.dumps(diagnostics, ensure_ascii=False)}")


def _get(url, params=None, timeout=25):
    last = None
    for attempt in range(3):
        try:
            r = S.get(url, params=params, timeout=timeout)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                time.sleep(2 * (attempt + 1)); last = requests.HTTPError(f"HTTP {r.status_code}"); continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last = e
            if attempt == 2: break
            time.sleep(1.5 * (attempt + 1))
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
    t = str(text or "").lower()
    return any(re.search(r"(?<!\w)" + re.escape(str(x).lower()) + r"(?!\w)", t) for x in terms if str(x).strip())


def _candidate(provider, title, description, image_url, source_url, rights, rights_url, author="", identifier="", categories=None, date_text=""):
    # Provider metadata is not schema-stable: normalize values before string operations.
    if isinstance(author, (list, tuple)):
        author = "; ".join(str(a.get("description", a) if isinstance(a, dict) else a) for a in author)
    title, description = str(title or ""), str(description or "")
    image_url, source_url = str(image_url or "") if image_url else "", str(source_url or "")
    rights, rights_url = str(rights or ""), str(rights_url or "")
    author, identifier = str(author or ""), str(identifier or "")
    if not title or not image_url or not source_url:
        STATS["metadata"] += 1; return None
    if not rights or not _rights_ok(rights):
        STATS["rights"] += 1; return None
    if BANNED.search(title + " " + description + " " + image_url):
        STATS["banned_metadata"] += 1; return None
    if not image_url.startswith("https://") or not source_url.startswith("https://"):
        STATS["metadata"] += 1; return None
    return {"provider": provider, "title": title, "description": description, "categories": [str(x) for x in (categories or [])], "date_text": str(date_text or ""),
            "image_url": image_url.split("#")[0], "source_url": source_url, "rights_statement": rights,
            "rights_url": rights_url, "author": author or "Not stated", "identifier": identifier}


def _note_reject(reason, c, extra=None):
    STATS[reason] += 1
    if len(TOP_REJECTED) < 8:
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
    """LoC lists several thumbnails; the fragment (#h=..&w=..) carries dimensions. Take the largest."""
    best, best_w = None, -1
    for u in urls or []:
        if not isinstance(u, str) or not u.startswith("https://"): continue
        m = re.search(r"[#&]w=(\d+)", u)
        w = int(m.group(1)) if m else 0
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
    return merged[:limit]


def _commons_members(cat, limit=50):
    """Files of a Commons category as candidates (rights and metadata gates applied)."""
    payload=_get("https://commons.wikimedia.org/w/api.php", {"action":"query","format":"json",
      "generator":"categorymembers","gcmtitle":"Category:"+cat.replace("Category:",""),"gcmtype":"file","gcmlimit":limit,
      "prop":"imageinfo|categories","cllimit":30,"iiprop":"url|extmetadata|size","iiurlwidth":2400})
    out=[]
    for p in payload.get("query",{}).get("pages",{}).values():
        ii=(p.get("imageinfo") or [{}])[0]; m=ii.get("extmetadata",{})
        c=_candidate("Wikimedia Commons",p.get("title",""),m.get("ImageDescription",{}).get("value",""),
            ii.get("thumburl") or ii.get("url"),ii.get("descriptionurl"),
            m.get("LicenseShortName",{}).get("value",""),m.get("LicenseUrl",{}).get("value",""),
            m.get("Artist",{}).get("value",""),p.get("pageid",""))
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
        c=_candidate("Wikimedia Commons",p.get("title",""),m.get("ImageDescription",{}).get("value",""),
          ii.get("thumburl") or ii.get("url"),ii.get("descriptionurl"),
          m.get("LicenseShortName",{}).get("value",""),m.get("LicenseUrl",{}).get("value",""),
          m.get("Artist",{}).get("value",""),p.get("pageid",""))
        if c: c["width"]=ii.get("width",0); c["height"]=ii.get("height",0); c["_from_search"]=True; out.append(c)
    return out

def _loc(query):
    payload=_get("https://www.loc.gov/search/", {"q":query,"fo":"json","c":50,"sp":1})
    out=[]
    for x in payload.get("results",[]):
        rights=x.get("rights","")
        resources=x.get("resources") or []
        image=(x.get("image_url") or [])
        if isinstance(image,str): image=[image]
        if not image and resources: image=resources[0].get("image") or []
        if isinstance(image,str): image=[image]
        c=_candidate("Library of Congress",x.get("title",""),x.get("description",""),
          _best_loc_image(image),
          x.get("id"),rights,x.get("rights_url") or x.get("rights"),x.get("contributor",""),x.get("id"))
        if c: out.append(c)
    return out

def _met(query):
    ids=_get("https://collectionapi.metmuseum.org/public/collection/v1/search",
             {"q":query,"hasImages":"true"}).get("objectIDs") or []
    out=[]
    for oid in ids[:15]:
        x=_get(f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}")
        if x.get("isPublicDomain") is not True: continue
        c=_candidate("The Metropolitan Museum of Art",x.get("title",""),x.get("objectDate",""),
          x.get("primaryImage") or x.get("primaryImageSmall"),x.get("objectURL"),
          "Public Domain", "https://www.metmuseum.org/about-the-met/policies-and-documents/open-access",
          x.get("artistDisplayName",""),oid)
        if c: out.append(c)
    return out

def _aic(query):
    payload=_get("https://api.artic.edu/api/v1/artworks/search",
       {"q":query,"limit":50,"fields":"id,title,artist_title,date_display,is_public_domain,thumbnail,api_link"})
    out=[]
    for x in payload.get("data",[]):
        if x.get("is_public_domain") is not True: continue
        thumb=x.get("thumbnail") or {}
        image=thumb.get("lqip")
        # API thumbnail is a tiny preview; use IIIF endpoint at full resolution.
        image=f"https://www.artic.edu/iiif/2/{thumb.get('image_id')}/full/2000,/0/default.jpg" if thumb.get("image_id") else None
        c=_candidate("Art Institute of Chicago",x.get("title",""),x.get("date_display",""),
          image,"https://www.artic.edu/artworks/"+str(x.get("id")),
          "Public Domain (CC0)", "https://www.artic.edu/open-access/open-access-images",
          x.get("artist_title",""),x.get("id"))
        if c: out.append(c)
    return out

def _cleveland(query):
    payload=_get("https://openaccess-api.clevelandart.org/api/artworks/",{"q":query,"limit":50})
    out=[]
    for x in payload.get("data",[]):
        rights=x.get("share_license_status","")
        if str(rights).lower() not in ("cc0","public domain","cc0 public domain"): continue
        im=x.get("images") or {}; url=(im.get("web") or {}).get("url") or (im.get("print") or {}).get("url")
        c=_candidate("Cleveland Museum of Art",x.get("title",""),x.get("creation_date",""),url,
          x.get("url"),rights,"https://www.clevelandart.org/open-access",x.get("creators") or "",x.get("id"))
        if c: out.append(c)
    return out

def _smithsonian(query):
    key=os.getenv("SMITHSONIAN_API_KEY")
    if not key: return []
    payload=_get("https://api.si.edu/openaccess/api/v1.0/search",
      {"api_key":key,"q":query,"rows":50,"online_media_type":"Images"})
    out=[]
    for x in payload.get("response",{}).get("rows",[]):
        cdata=x.get("content",{}); desc=cdata.get("descriptiveNonRepeating",{})
        title=(desc.get("title") or {}).get("content") or x.get("title","")
        for m in (desc.get("online_media") or {}).get("media",[]):
            usage=m.get("usage",{}); rights_text=usage.get("access","")
            if str(m.get("type","")).lower() not in ("images","image"): continue
            if not _rights_ok(rights_text): continue
            c=_candidate("Smithsonian",title,x.get("title",""),m.get("content"),desc.get("record_link"),
              rights_text,"https://www.si.edu/openaccess","",x.get("id"))
            if c: out.append(c); break
    return out

def _europeana(query):
    key=os.getenv("EUROPEANA_API_KEY")
    if not key: return []
    payload=_get("https://api.europeana.eu/record/v2/search.json",
       {"wskey":key,"query":query,"media":"true","rows":50,"profile":"rich"})
    out=[]
    for x in payload.get("items",[]):
        rights=x.get("rights",[])
        rights=rights[0] if rights else ""
        c=_candidate("Europeana", (x.get("title") or [""])[0],
          " ".join(x.get("dcDescription") or []), (x.get("edmIsShownBy") or [None])[0],
          (x.get("guid") or ""),rights,rights,(x.get("dcCreator") or [""])[0],x.get("id"))
        if c: out.append(c)
    return out

def _dpla(query):
    key=os.getenv("DPLA_API_KEY")
    if not key: return []
    payload=_get("https://api.dp.la/v2/items",{"q":query,"page_size":50,"api_key":key})
    out=[]
    for x in payload.get("docs",[]):
        obj=x.get("object",""); rights=x.get("rights","")
        c=_candidate("DPLA",x.get("sourceResource",{}).get("title",""),
          str(x.get("sourceResource",{}).get("description","")),obj,x.get("isShownAt"),rights,
          x.get("rights"),"",x.get("id"))
        if c: out.append(c)
    return out

def _nypl(query):
    key=os.getenv("NYPL_API_KEY")
    if not key: return []
    payload=_get("https://api.repo.nypl.org/api/v2/items/search",{"q":query,"per_page":50},timeout=25)
    out=[]
    # NYPL API's documented response may wrap results in nyplAPI; only records
    # with explicit rights are accepted; image URL is the public IIIF endpoint.
    for x in payload.get("nyplAPI",{}).get("response",{}).get("result",[]):
        rights=x.get("rightsStatement") or x.get("rights")
        image=x.get("imageID")
        c=_candidate("NYPL",x.get("title",""),x.get("description",""),
          ("https://iiif.nypl.org/iiif/2/"+str(image)+"/full/2000,/0/default.jpg") if image else None,
          x.get("itemLink"),rights,x.get("rightsStatementURI"),x.get("creator",""),x.get("uuid"))
        if c: out.append(c)
    return out

def _nga(query):
    payload=_get("https://www.nga.gov/api/v1/objects",{"q":query,"limit":50})
    out=[]
    for x in payload.get("data",payload if isinstance(payload,list) else []):
        if not (x.get("isPublicDomain") or x.get("is_public_domain")): continue
        c=_candidate("National Gallery of Art",x.get("title",""),x.get("displaydate",""),
          x.get("image_url") or x.get("iiif_url"),x.get("url") or x.get("object_url"),
          "Public Domain","https://www.nga.gov/open-access-policy",x.get("artist",""),x.get("id"))
        if c: out.append(c)
    return out

def _rijksmuseum(query):
    key=os.getenv("RIJKSMUSEUM_API_KEY")
    if not key: return []
    payload=_get("https://www.rijksmuseum.nl/api/en/collection",{"key":key,"q":query,"imgonly":"true","ps":50})
    out=[]
    for x in payload.get("artObjects",[]):
        rights=x.get("copyrightHolder") or x.get("rights")
        c=_candidate("Rijksmuseum",x.get("title",""),x.get("longTitle",""),
          (x.get("webImage") or {}).get("url"),x.get("links",{}).get("web"),
          rights,x.get("rightsUrl"),x.get("principalOrFirstMaker",""),x.get("objectNumber"))
        if c: out.append(c)
    return out

def _getty(query):
    key=os.getenv("GETTY_API_KEY")
    if not key: return []
    payload=_get("https://api.getty.edu/opencontent/v1/objects",{"q":query,"limit":50,"api_key":key})
    out=[]
    for x in payload.get("data",[]):
        rights=x.get("rights") or x.get("rightsStatement")
        c=_candidate("Getty",x.get("title",""),x.get("description",""),x.get("image_url"),
          x.get("url"),rights,x.get("rights_url"),x.get("creator",""),x.get("id"))
        if c: out.append(c)
    return out

PROVIDERS = [_loc, _met, _aic, _cleveland, _nga, _smithsonian, _europeana, _dpla, _nypl, _rijksmuseum, _getty]
# Endpoints I could not verify offline (NGA, NYPL, Rijksmuseum, Getty) are opt-in via config asset_resolver.providers.
DEFAULT_PROVIDERS = ["_met", "_aic", "_cleveland", "_loc", "_smithsonian", "_europeana", "_dpla"]

@lru_cache(maxsize=1)
def _load_clip(model_id):
    import torch
    from transformers import CLIPModel, CLIPProcessor
    model=CLIPModel.from_pretrained(model_id).to("cpu").eval()
    return model, CLIPProcessor.from_pretrained(model_id)

def _clip_analyze(query, image_bytes):
    """Return per-image dicts: cosine to the scene query, P(document-like), P(modern photo)."""
    import torch
    model_id=os.getenv("ASSET_CLIP_MODEL","openai/clip-vit-base-patch32")
    model, processor=_load_clip(model_id)
    images=[Image.open(io.BytesIO(b)).convert("RGB") for b in image_bytes]
    texts=[str(query), DOC_PAIR[0], DOC_PAIR[1], MODERN_PAIR[0], MODERN_PAIR[1]]
    with torch.inference_mode():
        text=processor(text=texts,return_tensors="pt",padding=True,truncation=True)
        ims=processor(images=images,return_tensors="pt")
        # Use CLIPModel.forward outputs for stable projected embeddings (get_*_features changed return types across versions).
        outputs=model(**text, **ims)
        ti, ii = outputs.text_embeds, outputs.image_embeds
        ti=ti/ti.norm(p=2,dim=-1,keepdim=True).clamp(min=1e-12)
        ii=ii/ii.norm(p=2,dim=-1,keepdim=True).clamp(min=1e-12)
        cos=ii @ ti.T                                   # (N, 5)
        scale=float(model.logit_scale.exp())
        p_doc=torch.softmax(scale*cos[:,1:3],dim=-1)[:,0]
        p_mod=torch.softmax(scale*cos[:,3:5],dim=-1)[:,0]
    return [{"score":float(cos[i,0]),"p_document":float(p_doc[i]),"p_modern":float(p_mod[i])} for i in range(len(images))]

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
    r=S.get(c["image_url"],timeout=(10,40),stream=True); r.raise_for_status()
    if int(r.headers.get("Content-Length") or 0)>35_000_000: raise ValueError("file larger than 35 MB")
    return r.content

def resolve_scene_image(scene, idx, config, asset_dir, used, output):
    """Discover across providers, filter rights/format/size, rank with CLIP, save best."""
    STATS.clear(); FOUND.clear(); TOP_REJECTED.clear()
    rcfg=config.get("asset_resolver",{}) or {}
    queries=build_queries(scene,config,int(rcfg.get("max_queries",6)))
    enabled=set(rcfg.get("providers") or DEFAULT_PROVIDERS)
    providers=[p for p in PROVIDERS if p.__name__ in enabled]
    place=config.get("name","")
    entities=[str(e) for e in (scene.get("named_entities",[]) or scene.get("entities",[]))]
    candidates=[]
    # Commons categories: place-aware first, then entity categories (e.g. "Edo Castle").
    for name in dict.fromkeys([place]+entities[:2]):
        if not name: continue
        try:
            got=_commons_category(name); FOUND["commons_category:"+name]+=len(got); candidates.extend(got)
        except Exception as e:
            PROVIDER_ERRORS["commons_category"]=f"{type(e).__name__}: {e}"; print(f"Commons category lookup unavailable for '{name}': {e}")
    for cat in scene.get("commons_categories",[]) or []:
        try:
            got=_commons_members(str(cat)); FOUND["commons_category:"+str(cat)]+=len(got); candidates.extend(got)
        except Exception as e:
            PROVIDER_ERRORS["commons_category"]=f"{type(e).__name__}: {e}"[:200]; print(f"Commons category '{cat}' unavailable: {e}")
    for provider in providers:
        name=provider.__name__
        for query in queries:
            if _FAILS[name]>=2: break                      # stop hammering a provider that keeps failing
            try:
                got=provider(query); FOUND[name]+=len(got); candidates.extend(got); _FAILS[name]=0
            except Exception as e:
                _FAILS[name]+=1; PROVIDER_ERRORS[name]=f"{type(e).__name__}: {e}"[:200]
                print(f"Provider {name} unavailable for '{query}': {type(e).__name__}: {e}")
    if not [c for c in candidates]:
        for query in queries[:3]:
            try: got=_commons_search(query); FOUND["commons_search"]+=len(got); candidates.extend(got)
            except Exception as e: PROVIDER_ERRORS["commons_search"]=f"{type(e).__name__}: {e}"[:200]; print(f"Commons text search unavailable: {e}")

    def diagnostics():
        return {"queries":queries,"providers_found":dict(FOUND),"rejected":dict(STATS),
                "top_rejected":list(TOP_REJECTED),"provider_errors":dict(PROVIDER_ERRORS),
                "ocr_available":_ocr_available()}

    # Deduplicate before counting rejection reasons; keyword gate applies uniformly across providers.
    required=[str(k).lower() for k in scene.get("required_keywords",[])]
    banned_terms=list(config.get("image_banned_terms",[]))+list(scene.get("banned_keywords",[]))
    unique={}; rejected_seen=set()
    for c in candidates:
        key=c["source_url"]
        if key in unique or key in rejected_seen: continue
        if not _rights_ok(c["rights_statement"]): _note_reject("rights",c); rejected_seen.add(key); continue
        text=" ".join([c.get("title",""),c.get("description","")," ".join(c.get("categories",[]))])
        if BANNED.search(text) or _has_term(text,banned_terms): _note_reject("banned_term",c); rejected_seen.add(key); continue
        hits=sum(1 for k in required if k in text.lower())
        c["_hits"]=hits
        if required and hits==0 and str(scene.get("keyword_gate","hard")).lower()!="soft":
            _note_reject("keywords",c,{"required_keywords":required,"category_names":c.get("categories",[])}); rejected_seen.add(key); continue
        unique[key]=c
    candidates=sorted(unique.values(),key=lambda c:-c["_hits"])
    if not candidates:
        raise ResolverError(f"No licensed candidates discovered for scene '{scene['title']}'.",diagnostics())
    candidates=_round_robin(candidates,int(rcfg.get("max_downloads",20)))
    use_ocr=_ocr_available(); ranked=[]
    for c in candidates:
        try:
            data=_download(c)
            if len(data)<30000 or data[:4] in (b"%PDF",b"AT&TD",b"FORM"): _note_reject("format",c); continue
            im=Image.open(io.BytesIO(data))
            if im.format not in ("JPEG","PNG","WEBP","TIFF"): _note_reject("format",c); continue
            if not _size_ok(im,rcfg): _note_reject("size",c,{"pixels":list(im.size)}); continue
            if use_ocr and _text_heavy(im): _note_reject("ocr_text_heavy",c); continue
            digest=hashlib.sha256(data).hexdigest()
            if digest in used or c["source_url"] in used: _note_reject("duplicate",c); continue
            c["_bytes"]=data; c["_digest"]=digest; c["_size"]=im.size
            ranked.append(c)
        except Exception as e:
            _note_reject("download",c,{"error":f"{type(e).__name__}: {e}"[:120]})
    if not ranked:
        raise ResolverError(f"No candidates passed explicit-rights, image-format, size and duplicate gates for '{scene['title']}'.",diagnostics())
    # Rank all surviving candidates with a compact CLIP model on CPU.
    text_query=scene.get("narration_intent","")+"; visual: "+scene["title"]+"; place: "+place
    try:
        results=_clip_analyze(text_query,[x["_bytes"] for x in ranked])
    except Exception as e:
        raise ResolverError(f"CLIP relevance gate unavailable; refusing keyword-only image selection: {type(e).__name__}: {e}",diagnostics()) from e
    survivors=[]
    for c,r in zip(ranked,results):
        c["_score"]=r["score"]; c["_p_doc"]=r["p_document"]; c["_p_modern"]=r["p_modern"]
        if r["p_document"]>0.5: _note_reject("document_like",c,{"p_document":round(r["p_document"],2)}); continue
        if scene.get("image_type")=="modern" and r["p_modern"]<0.5:
            _note_reject("present_historical",c,{"p_modern":round(r["p_modern"],2)}); continue
        if scene.get("image_type")=="historic" and not scene.get("allow_modern_photo",False) and r["p_modern"]>=0.5:
            _note_reject("modern_in_historic",c,{"p_modern":round(r["p_modern"],2)}); continue
        date_text=str(c.get("date_text","")+" "+c.get("description","")+" "+c.get("title",""))
        lo=scene.get("min_year"); hi=scene.get("max_year")
        if scene.get("image_type")=="historic" and (lo is not None or hi is not None):
            years=[int(y) for y in re.findall(r"(?<!\\d)(1[0-9]{3}|20[0-2][0-9]|[0-9]{3})(?!\\d)",date_text)]
            if years and ((lo is not None and max(years)<int(lo)) or (hi is not None and min(years)>int(hi))):
                _note_reject("date_window",c,{"date_text":date_text[:180],"years":years,"min_year":lo,"max_year":hi}); continue
            if not years: c.setdefault("_warnings",[]).append("date unverified")
        survivors.append(c)
    threshold=float(scene.get("clip_min_score",rcfg.get("clip_min_score",0.22)))
    survivors.sort(key=lambda c:c["_score"]+0.01*min(c["_hits"],3),reverse=True)
    top=survivors[:int(rcfg.get("top_candidates",5))]
    print(f"CLIP candidates for {scene['title']}: "+", ".join(f"{x['provider']}={x['_score']:.3f}" for x in top))
    winner=next((c for c in top if c["_score"]>=threshold),None)
    if winner is None:
        for c in top[:3]: _note_reject("clip_below_threshold",c,{"clip_score":round(c["_score"],3)})
        raise ResolverError(f"No image scored >= {threshold:.2f} with CLIP for '{scene['title']}'. Use configured map/text-card fallback; do not substitute a weak image.",diagnostics())
    data=winner["_bytes"]; im=Image.open(io.BytesIO(data)).convert("RGB")
    filename=f"scene_{idx:02d}.jpg"; path=Path(asset_dir)/filename; im.save(path,quality=94)
    used.add(winner["_digest"]); used.add(winner["source_url"])
    warnings=[] if use_ocr else ["OCR gate unavailable in this environment; relied on CLIP document check only"]
    return im, {"content_sha256":winner["_digest"],"scene":scene["title"],"file":str(path),
      "title":winner["title"],"author":winner["author"],"license":winner["rights_statement"],
      "rights_statement":winner["rights_statement"],"license_url":winner["rights_url"],
      "source_url":winner["source_url"],"provider":winner["provider"],"match_score":winner["_score"],
      "clip_score":winner["_score"],"period_ok":(winner["_p_modern"]>=0.5) if scene.get("image_type")=="modern" else None,
      "search_variant":"CLIP ranked: "+text_query,"warnings":warnings,"dimensions":list(winner["_size"]),
      "top_candidates":[{"provider":x["provider"],"title":x["title"],"source_url":x["source_url"],
                         "rights_statement":x["rights_statement"],"clip_score":x["_score"]} for x in top]}
