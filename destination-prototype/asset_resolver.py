#!/usr/bin/env python3
"""Rights-first archival image discovery with CPU CLIP ranking.

Uses official collection APIs only. API-key providers are enabled when their
documented key environment variable is configured; no key is fabricated.
"""
from __future__ import annotations
import hashlib, io, os, re, time
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
import requests
from PIL import Image

UA = "RegionHistoryShort/2.0 (educational historical video; contact via GitHub)"
S = requests.Session()
S.headers.update({"User-Agent": UA})
ALLOWED = ("public domain", "public domain mark", "cc0", "cc by", "cc-by",
           "cc by-sa", "cc-by-sa", "creative commons attribution",
           "creative commons zero", "no known copyright restrictions")
BANNED = re.compile(r"book.?cover|cover.?art|title.?page|frontispiece|text.?scan|scanned text|manuscript page|page scan|catalogue|catalog|plate list|contents page|pdf|djvu|document scan|report cover", re.I)
MIN_LONG_SIDE = 1920

def _get(url, params=None, timeout=25):
    r = S.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()

def _rights_ok(rights):
    x = str(rights or "").strip().lower()
    if not x or any(k in x for k in ("unknown", "copyright", "all rights reserved",
                                      "noncommercial", "non-commercial", "no derivatives",
                                      "cc by-nc", "cc-by-nc", "cc by-nd", "cc-by-nd")):
        return False
    return any(k in x for k in ALLOWED) or "creativecommons.org/licenses/by/" in x or "creativecommons.org/licenses/by-sa/" in x or "creativecommons.org/publicdomain/" in x

def _candidate(provider, title, description, image_url, source_url, rights, rights_url, author="", identifier=""):
    if not title or not image_url or not source_url or not rights or not _rights_ok(rights):
        return None
    if BANNED.search(title + " " + description + " " + image_url):
        return None
    if not str(image_url).startswith("https://") or not str(source_url).startswith("https://"):
        return None
    return {"provider":provider, "title":str(title), "description":str(description or ""),
            "image_url":image_url, "source_url":source_url, "rights_statement":str(rights),
            "rights_url":str(rights_url or ""), "author":str(author or "Not stated"),
            "identifier":str(identifier or "")}

def _commons_category(place):
    """Resolve place through Wikidata, then query its Commons category."""
    q = _get("https://www.wikidata.org/w/api.php", {"action":"wbsearchentities","search":place,
          "language":"en","format":"json","limit":5})
    entity = next((x for x in q.get("search",[]) if x.get("id")), None)
    if not entity: return []
    data = _get("https://www.wikidata.org/w/api.php", {"action":"wbgetentities","ids":entity["id"],
                  "props":"claims|labels","languages":"en","format":"json"})
    claims = data.get("entities",{}).get(entity["id"],{}).get("claims",{})
    cats = []
    for cl in claims.get("P373",[]):
        v=cl.get("mainsnak",{}).get("datavalue",{}).get("value")
        if v: cats.append(v)
    # Wikidata Commons sitelink is a useful additional route.
    sitelinks = data.get("entities",{}).get(entity["id"],{}).get("sitelinks",{})
    commons = sitelinks.get("commonswiki",{}).get("title","")
    if commons.startswith("Category:"): cats.insert(0,commons.split(":",1)[1])
    out=[]
    for cat in cats[:3]:
        payload=_get("https://commons.wikimedia.org/w/api.php", {"action":"query","format":"json",
          "generator":"categorymembers","gcmtitle":"Category:"+cat,"gcmtype":"file","gcmlimit":30,
          "prop":"imageinfo|categories","cllimit":30,"iiprop":"url|extmetadata|size"})
        for p in payload.get("query",{}).get("pages",{}).values():
            ii=(p.get("imageinfo") or [{}])[0]; m=ii.get("extmetadata",{})
            rights=m.get("LicenseShortName",{}).get("value","")
            licurl=m.get("LicenseUrl",{}).get("value","")
            c=_candidate("Wikimedia Commons",p.get("title",""),m.get("ImageDescription",{}).get("value",""),
                ii.get("thumburl") or ii.get("url"),ii.get("descriptionurl"),rights,licurl,
                m.get("Artist",{}).get("value",""),p.get("pageid",""))
            if c: c["width"]=ii.get("width",0); c["height"]=ii.get("height",0); out.append(c)
    return out

def _commons_search(query):
    payload=_get("https://commons.wikimedia.org/w/api.php", {"action":"query","format":"json",
       "generator":"search","gsrsearch":query,"gsrnamespace":6,"gsrlimit":25,
       "prop":"imageinfo|categories","cllimit":30,"iiprop":"url|extmetadata|size"})
    out=[]
    for p in payload.get("query",{}).get("pages",{}).values():
        ii=(p.get("imageinfo") or [{}])[0]; m=ii.get("extmetadata",{})
        c=_candidate("Wikimedia Commons",p.get("title",""),m.get("ImageDescription",{}).get("value",""),
          ii.get("thumburl") or ii.get("url"),ii.get("descriptionurl"),
          m.get("LicenseShortName",{}).get("value",""),m.get("LicenseUrl",{}).get("value",""),
          m.get("Artist",{}).get("value",""),p.get("pageid",""))
        if c: c["width"]=ii.get("width",0); c["height"]=ii.get("height",0); out.append(c)
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
          next((u for u in image if isinstance(u,str) and u.startswith("https://")),None),
          x.get("id"),rights,x.get("rights_url") or x.get("rights"),x.get("contributor",""),x.get("id"))
        if c: out.append(c)
    return out

def _met(query):
    ids=_get("https://collectionapi.metmuseum.org/public/collection/v1/search",
             {"q":query,"hasImages":"true"}).get("objectIDs") or []
    out=[]
    for oid in ids[:30]:
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
          x.get("url"),rights,"https://www.clevelandart.org/open-access",x.get("creators",[]),x.get("id"))
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
        rights=cdata.get("online_media",{}).get("media",[])
        for m in rights:
            usage=m.get("usage",{}); rights_text=usage.get("access","")
            if not _rights_ok(rights_text): continue
            c=_candidate("Smithsonian",cdata.get("freetext",{}).get("title",[{}])[0].get("content",x.get("title","")),
              x.get("title",""),m.get("content"),desc.get("record_link"),rights_text,
              usage.get("guidelines"),"",x.get("id"))
            if c: out.append(c)
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

@lru_cache(maxsize=1)
def _load_clip(model_id):
    import torch
    from transformers import CLIPModel, CLIPProcessor
    model=CLIPModel.from_pretrained(model_id).to("cpu").eval()
    return model, CLIPProcessor.from_pretrained(model_id)

def _clip_scores(query, candidates, image_bytes):
    """Small CPU-only CLIP model; return cosine similarity on text/image embeddings."""
    import torch
    from transformers import CLIPModel, CLIPProcessor
    model_id=os.getenv("ASSET_CLIP_MODEL","openai/clip-vit-base-patch32")
    model=CLIPModel.from_pretrained(model_id).to("cpu").eval()
    processor=CLIPProcessor.from_pretrained(model_id)
    images=[Image.open(io.BytesIO(b)).convert("RGB") for b in image_bytes]
    with torch.inference_mode():
        text=processor(text=[query],return_tensors="pt",padding=True,truncation=True)
        ti=model.get_text_features(**text)
        ims=processor(images=images,return_tensors="pt")
        ii=model.get_image_features(**ims)
        ti=ti/ti.norm(dim=-1,keepdim=True); ii=ii/ii.norm(dim=-1,keepdim=True)
        scores=(ii @ ti.T).squeeze(-1).tolist()
    return [float(s) for s in scores]

def resolve_scene_image(scene, idx, config, asset_dir, used, output):
    """Discover across providers, filter rights/format/size, rank with CLIP, save best."""
    queries=list(dict.fromkeys([config.get("name","")+" "+q for q in scene.get("image_search_terms",[])]))
    place=config.get("name","")
    candidates=[]
    # Place-aware Commons category first; ordinary text search is fallback.
    try: candidates.extend(_commons_category(place))
    except Exception as e: print(f"Commons category lookup unavailable: {e}")
    for provider in PROVIDERS:
        for query in queries[:3]:
            try: candidates.extend(provider(query))
            except Exception as e: print(f"Provider {provider.__name__} unavailable for '{query}': {type(e).__name__}: {e}")
    if not candidates:
        for query in queries:
            try: candidates.extend(_commons_search(query))
            except Exception as e: print(f"Commons text search unavailable: {e}")
    # Deduplicate records and cheaply remove obviously irrelevant metadata before downloading.
    unique={}
    required=scene.get("required_keywords",[])
    banned_terms=config.get("image_banned_terms",[])+scene.get("banned_keywords",[])
    for c in candidates:
        key=c["source_url"]
        if key in unique or not _rights_ok(c["rights_statement"]): continue
        text=c["title"]+" "+c["description"]
        if BANNED.search(text) or any(t.lower() in text.lower() for t in banned_terms): continue
        if not any(k.lower() in text.lower() for k in required): continue
        unique[key]=c
    candidates=list(unique.values())
    if not candidates: raise RuntimeError(f"No licensed candidates discovered for scene '{scene['title']}'")
    ranked=[]
    for c in candidates:
        try:
            resp=S.get(c["image_url"],timeout=35); resp.raise_for_status()
            data=resp.content
            if len(data)<50000 or data[:4] in (b"%PDF",b"AT&TD",b"FORM"): continue
            im=Image.open(io.BytesIO(data))
            if im.format not in ("JPEG","PNG","WEBP","TIFF"): continue
            if max(im.size)<MIN_LONG_SIDE or min(im.size)<600: continue
            if im.width/im.height>3.0 or im.height/im.width>3.0: continue
            digest=hashlib.sha256(data).hexdigest()
            if digest in used or c["source_url"] in used: continue
            c["_bytes"]=data; c["_digest"]=digest; c["_size"]=im.size
            ranked.append(c)
        except Exception as e: print(f"Candidate download rejected ({c['provider']}): {type(e).__name__}: {e}")
    if not ranked: raise RuntimeError(f"No candidates passed explicit-rights, image-format, 1920px long-side, and duplicate gates for '{scene['title']}'")
    # Rank all surviving candidates with a compact CLIP model on CPU.
    text_query=scene.get("narration_intent","")+"; visual: "+scene["title"]+"; place: "+place
    try:
        scores=_clip_scores(text_query,ranked,[x["_bytes"] for x in ranked])
    except Exception as e:
        raise RuntimeError(f"CLIP relevance gate unavailable; refusing keyword-only image selection: {type(e).__name__}: {e}") from e
    for c,score in zip(ranked,scores): c["_score"]=score
    ranked.sort(key=lambda c:c["_score"],reverse=True)
    threshold=float(config.get("asset_resolver",{}).get("clip_min_score",0.22))
    top=ranked[:int(config.get("asset_resolver",{}).get("top_candidates",5))]
    print(f"CLIP candidates for {scene['title']}: "+", ".join(f"{x['provider']}={x['_score']:.3f}" for x in top))
    winner=next((c for c in top if c["_score"]>=threshold),None)
    if winner is None:
        raise RuntimeError(f"No image scored >= {threshold:.2f} with CLIP for '{scene['title']}'. Use configured map/text-card fallback; do not substitute a weak image.")
    data=winner["_bytes"]; im=Image.open(io.BytesIO(data)).convert("RGB")
    filename=f"scene_{idx:02d}.jpg"; path=Path(asset_dir)/filename; im.save(path,quality=94)
    used.add(winner["_digest"]); used.add(winner["source_url"])
    return im, {"content_sha256":winner["_digest"],"scene":scene["title"],"file":str(path),
      "title":winner["title"],"author":winner["author"],"license":winner["rights_statement"],
      "rights_statement":winner["rights_statement"],"license_url":winner["rights_url"],
      "source_url":winner["source_url"],"provider":winner["provider"],"match_score":winner["_score"],
      "search_variant":"CLIP ranked: "+text_query,"warnings":[],"dimensions":list(winner["_size"]),
      "top_candidates":[{"provider":x["provider"],"title":x["title"],"source_url":x["source_url"],
                         "rights_statement":x["rights_statement"],"clip_score":x["_score"]} for x in top]}
