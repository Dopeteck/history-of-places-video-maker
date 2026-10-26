#!/usr/bin/env python3
"""Config-driven region short renderer. Fails closed on rights, relevance, media and QA."""
from __future__ import annotations
import argparse, datetime as dt, hashlib, html, json, math, os, re, subprocess, sys, time, wave
from pathlib import Path
from io import BytesIO
import requests, yaml, numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter, ImageOps

ROOT=Path(__file__).resolve().parents[1]
COMMONS="https://commons.wikimedia.org/w/api.php"
UA="RegionHistoryShort/1.0 (GitHub Actions; rights-attributed educational project)"
W,H,FPS=1080,1920,30
ALLOWED=("public domain","public domain mark","cc0","cc by","cc-by","cc by-sa","cc-by-sa")
last_req=0.0
session=requests.Session(); session.headers.update({"User-Agent":UA})
def fail(msg): raise RuntimeError(msg)
def get(url,params=None,timeout=(10,45)):
 global last_req
 for attempt in range(6):
  wait=1.0-(time.monotonic()-last_req)
  if wait>0: time.sleep(wait)
  try: r=session.get(url,params=params,timeout=timeout); last_req=time.monotonic()
  except requests.RequestException:
   if attempt==5: raise
   time.sleep(min(2**attempt,30)); continue
  if r.status_code==429 or 500<=r.status_code<600:
   if attempt==5: r.raise_for_status()
   try: delay=float(r.headers.get("Retry-After",""))
   except ValueError: delay=min(3*2**attempt,30)
   time.sleep(max(1,min(delay,45))); continue
  r.raise_for_status(); return r
 fail("Commons retry budget exhausted")
def clean(s): return html.unescape(re.sub(r"<[^>]+>"," ",str(s or ""))).strip()
def allowed(short,url):
 x=(short+" "+url).lower()
 if any(k in x for k in ("noncommercial","non-commercial","cc by-nc","cc-by-nc","cc by-nd","cc-by-nd","all rights reserved")): return False
 return any(k in x for k in ALLOWED) or "creativecommons.org/publicdomain" in x or "creativecommons.org/licenses/by/" in x or "creativecommons.org/licenses/by-sa/" in x
def color(value,default=(32,32,32)):
 try:
  named={"ink-black":"#20242B","warm-ivory":"#F2E6CE","vermilion":"#E65A45","lagoon-teal":"#19A7A1","sand-gold":"#D9B56D","deep-indigo":"#26264A","steel-blue":"#45657A","warm-neutral":"#C8B89A","dark-contrast":"#262626"}
  v=named.get(str(value).lower(),value)
  if isinstance(v,str) and v.startswith("#") and len(v)==7: return tuple(int(v[i:i+2],16) for i in (1,3,5))
 except Exception: pass
 return default
from functools import lru_cache
@lru_cache(maxsize=32)
def font(size,bold=False):
 p="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
 return ImageFont.truetype(p,size) if Path(p).exists() else ImageFont.load_default()
def caption_window(d,words,wi,f,maxw):
 # Up to 3 words before and 3 after the spoken word, shrunk so the line never leaves the frame.
 lo,hi=wi,wi+1
 width=lambda a,b: d.textlength(" ".join(words[a:b]),font=f)
 while True:
  grew=False
  if lo>0 and wi-lo<3 and width(lo-1,hi)<=maxw: lo-=1; grew=True
  if hi<len(words) and hi-wi<4 and width(lo,hi+1)<=maxw: hi+=1; grew=True
  if not grew: return lo,hi
def wrap(draw,text,f,maxw):
 out=[]; line=""
 for word in text.split():
  test=(line+" "+word).strip()
  if line and draw.textbbox((0,0),test,font=f)[2]>maxw: out.append(line); line=word
  else: line=test
 if line: out.append(line)
 return out
def normalize_config(cfg):
 # YAML turns bare 1868 / 1603 into ints; every keyword/term list must be text before any .lower() call.
 for key in ("image_banned_terms",): cfg[key]=[str(x) for x in cfg.get(key,[]) or []]
 for sc in cfg.get("scenes",[]):
  for key in ("required_keywords","banned_keywords","image_search_terms","visual_tags","asset_tags","named_entities","entities","commons_categories","optional_second_beat"):
   if key in sc: sc[key]=[str(x) for x in sc[key] or []]
  if sc.get("starts_at") is not None: sc["starts_at"]=str(sc["starts_at"])
 return cfg
def banned(text,terms): return any(t.lower() in text.lower() for t in terms)
def score_candidate(title,description,cats,required):
 hay=(title+" "+description+" "+" ".join(cats)).lower()
 return sum(3 if k.lower() in title.lower() else 2 if k.lower() in description.lower() else 1 for k in required if k.lower() in hay)
def get_image(scene,idx,config,asset_dir,used,output):
 from asset_resolver import resolve_scene_image
 return resolve_scene_image(scene,idx,config,asset_dir,used,output)
def local_image(scene,idx,config,used):
 # Prefer repository stills only when the manifest proves both provenance and license.
 manifest_path=ROOT/"destination-prototype"/"assets_manifest.json"
 if not manifest_path.exists(): return None
 try: manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
 except Exception as e: fail(f"Cannot read asset manifest: {e}")
 assets=[ROOT/"assets",ROOT/"destination-prototype"/"assets"]
 for rec in manifest:
  if not isinstance(rec,dict): continue
  rel=rec.get("local_filename") or rec.get("file") or rec.get("path")
  if not rel: continue
  candidates=[]
  p=Path(rel)
  candidates.append(p if p.is_absolute() else ROOT/p)
  for base in assets: candidates.append(base/p.name)
  for p in candidates:
   if not p.is_file() or p.suffix.lower() not in (".jpg",".jpeg",".png",".webp"): continue
   title=str(rec.get("title") or p.name); desc=str(rec.get("description") or "")
   keys=scene.get("required_keywords",[])
   match=score_candidate(title,desc,rec.get("categories",[]),keys)
   lic=str(rec.get("license") or rec.get("rights_statement") or "")
   licurl=str(rec.get("license_url") or rec.get("rights_url") or "")
   source=str(rec.get("source_url") or "")
   if rec.get("approved") is not True: continue
   if match<=0 or not allowed(lic,licurl) or not source.startswith("https://") or not licurl.startswith("https://"): continue
   if banned(title+" "+desc+" "+" ".join(rec.get("categories",[])),config.get("image_banned_terms",[])+scene.get("banned_keywords",[])): continue
   im=Image.open(p).convert("RGB")
   if min(im.size)<1080: continue
   # Use decoded RGB pixels for identity, matching the online resolver. File-byte
   # hashes differ when the same photograph is re-encoded as JPEG/PNG.
   digest=hashlib.sha256(im.tobytes()).hexdigest()
   if digest in used or source in used: continue
   used.add(digest); used.add(source)
   return im,{"content_sha256":digest,"scene":scene["title"],"file":str(p.relative_to(ROOT)),"title":title,"author":str(rec.get("author") or "Unknown / not stated"),"license":lic,"license_url":licurl,"source_url":source,"match_score":match,"period_ok":True if str(rec.get("period","")).lower()=="modern" else None,"search_variant":"repository manifest (preferred local still)","warnings":[]}
 return None
def make_map(scene,idx,config,asset_dir):
 # Offline vector map (Natural Earth, public domain) in the region palette; see vector_map.py.
 from vector_map import render_map
 im,credit=render_map(scene,config,asset_dir,idx)
 credit["scene"]=scene["title"]
 return im,credit
def text_fallback_card(scene,idx,config,asset_dir,error):
  # Review-only missing-asset marker. Never fabricate an illustration to stand in
  # for historical evidence; the production gate blocks any unresolved scene.
  W0,H0=1080,1440
  bg=color(config.get("visual_style",{}).get("palette",["warm-ivory"])[0],(242,230,206))
  im=Image.new("RGB",(W0,H0),bg)
  p=asset_dir/f"scene_{idx:02d}_missing.jpg"; im.save(p,quality=94)
  return im,{"content_sha256":hashlib.sha256(f"missing:{idx}:{scene.get('title','Scene')}".encode("utf-8")).hexdigest(),"scene":scene["title"],"file":str(p.relative_to(ROOT)),
   "title":"NO APPROVED SOURCE IMAGE — "+str(scene.get("title","Scene")),"author":"","license":"","license_url":"","source_url":"",
   "provider":"missing asset","match_score":None,"search_variant":"No candidate passed rights, quality and relevance checks",
   "warnings":[str(error).split(" Diagnostics:")[0]],"diagnostics":_parse_diagnostics(str(error))}

def _parse_diagnostics(message):
 import json, re
 match=re.search(r"Diagnostics:\s*(\{.*\})\s*$",message,re.S)
 if match:
  try: return json.loads(match.group(1))
  except (ValueError,TypeError): pass
 return {"providers_found":{},"rejected":{"note":"resolver gave no diagnostics for this failure"},"top_rejected":[]}
def write_approval_board(media,credits,cfg,out):
 # A standalone HTML approval board puts each real chosen image beside its narration line and rights.
 cards=[]
 for i,((scene,im),c) in enumerate(zip(media,credits),1):
   asset_path=Path(c["file"])
   if not asset_path.is_absolute(): asset_path=ROOT/asset_path
   rel=Path(os.path.relpath(asset_path, out)).as_posix()
   cards.append(f"""<article><img src="{html.escape(rel)}" alt="{html.escape(c['title'])}"><section><h2>{i}. {html.escape(scene['title'])}</h2><p><b>Narration intent:</b> {html.escape(scene.get('narration_intent',''))}</p><p><b>Chosen asset:</b> {html.escape(c['title'])}</p><p><b>Provider:</b> {html.escape(c.get('provider','unknown'))}</p><p><b>Rights:</b> {html.escape(c.get('rights_statement',c.get('license','unknown')))}</p><p><b>License URL:</b> <a href="{html.escape(c.get('license_url',''),quote=True)}">{html.escape(c.get('license_url','not provided'))}</a></p><p><b>Source:</b> <a href="{html.escape(c.get('source_url',''),quote=True)}">{html.escape(c.get('source_url','not provided'))}</a></p><p><b>CLIP relevance:</b> {c.get('match_score') if c.get('match_score') is not None else 'not scored'}</p><p><b>Warnings:</b> {html.escape('; '.join(c.get('warnings',[])) or 'None')}</p><p><b>Author:</b> {html.escape(c.get('author','Not stated'))}</p><h3>Top candidates</h3><pre>{html.escape(json.dumps(c.get('top_candidates',[]),indent=2,ensure_ascii=False))}</pre><details><summary>Resolver diagnostics</summary><pre>{html.escape(json.dumps(c.get('diagnostics',{}),indent=2,ensure_ascii=False))}</pre></details></section></article>""")
 page="""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Visual approval board</title><style>body{font:16px system-ui;max-width:1200px;margin:auto;padding:24px;background:#f4f1ea;color:#20242b}h1{margin-bottom:4px}.notice{padding:14px;background:#fff0ce;border-left:5px solid #b7791f}article{display:grid;grid-template-columns:minmax(240px,38%) 1fr;gap:22px;background:white;padding:18px;margin:20px 0;border-radius:12px}img{width:100%;height:auto;max-height:520px;object-fit:contain;background:#222}a{overflow-wrap:anywhere}@media(max-width:700px){article{grid-template-columns:1fr}}</style></head><body><h1>Visual approval board — """+html.escape(cfg["name"])+"""</h1><p class="notice"><b>REVIEW ONLY — NOT APPROVED FOR PUBLISHING.</b> Verify every image against the narration and inspect the rights/source links. A text card means no candidate passed the relevance threshold.</p>"""+''.join(cards)+"</body></html>"
 (out/"approval-board.html").write_text(page,encoding="utf-8")
 # Persist rights/source details to the resolver manifest; existing manually supplied entries are preserved.
 manifest=ROOT/"destination-prototype"/"assets_manifest.json"
 try: old=json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else []
 except Exception: old=[]
 if not isinstance(old,list): old=[]
 byhash={str(x.get("content_sha256")):x for x in old if isinstance(x,dict) and x.get("content_sha256")}
 for c in credits:
  rec={k:c.get(k) for k in ("content_sha256","scene","file","title","author","license","rights_statement","license_url","source_url","provider","match_score","dimensions","warnings")}; rec["approved"]=byhash.get(str(c.get("content_sha256")),{}).get("approved",False)
  if c.get("content_sha256"): byhash[c["content_sha256"]]=rec
  
 manifest.write_text(json.dumps(list(byhash.values()),indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
_BLUR_CACHE={}
def _blurred_backdrop(im):
 # The blurred, darkened backdrop does not change during a scene: compute once per image, not once per frame.
 key=id(im)
 if key not in _BLUR_CACHE:
  bg=ImageOps.fit(im,(W,H),method=Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(28))
  _BLUR_CACHE[key]=(im,ImageEnhance.Brightness(bg).enhance(.48))
 return _BLUR_CACHE[key][1].copy()
def smart_fit(im,progress):
 # Portrait images use focal-aware fit; wide images keep the whole picture over a blurred backdrop and
 # get a slow push-in so landscape art is never a static slide.
 if im.width/im.height < .78:
  scale=max(W/im.width,H/im.height)*(1+.07*progress); rw,rh=round(im.width*scale),round(im.height*scale); big=im.resize((rw,rh),Image.Resampling.LANCZOS); dx=max(0,rw-W); dy=max(0,rh-H); x=int(dx*(.25+.5*progress)); y=int(dy*(.25+.5*progress)); return big.crop((x,y,x+W,y+H))
 bg=_blurred_backdrop(im)
 fg=im.copy(); fg.thumbnail((W-80,H-360),Image.Resampling.LANCZOS)
 zoom=1+.06*progress; fw,fh=round(fg.width*zoom),round(fg.height*zoom)
 fg=fg.resize((fw,fh),Image.Resampling.LANCZOS)
 bg.paste(fg,((W-fw)//2,(H-fh)//2-round(14*progress))); return bg
def _timed(label, started):
 print(f"TIMING {label}: {time.perf_counter()-started:.2f}s", flush=True)
def main():
 run_started=time.perf_counter()
 ap=argparse.ArgumentParser(); ap.add_argument("--config",required=True); ap.add_argument("--output",default=""); ap.add_argument("--visual-review-only",action="store_true"); ap.add_argument("--draft-preview",action="store_true",help="Render a non-publishable draft MP4; all source and QA gates remain enforced"); ap.add_argument("--only-scenes",default="",help="Comma-separated exact scene titles to review"); ap.add_argument("--hook-variant",choices=("A","B"),default="A",help="Opening hook variant to render"); a=ap.parse_args()
 step_started=time.perf_counter()
 cfgpath=Path(a.config); cfg=normalize_config(yaml.safe_load(cfgpath.read_text(encoding="utf-8")))
 _timed("config_load_and_validate",step_started)
 if not cfg.get("name") or len(cfg.get("scenes",[]))<8 or len(cfg["scenes"])>12: fail("Config needs name and 8–12 scenes")
 if len(set(s["title"] for s in cfg["scenes"]))!=len(cfg["scenes"]): fail("Scene titles must be unique")
 for s in cfg["scenes"]:
  if len(s.get("image_search_terms",[]))<3: fail(f"Scene '{s.get('title')}' needs at least 3 search variants")
  if not s.get("required_keywords"): fail(f"Scene '{s.get('title')}' needs required_keywords")
  if s.get("image_type") not in ("historic","modern","map"): fail(f"Scene '{s.get('title')}' has invalid image_type")
  if banned(s.get("title","")+" "+s.get("narration_intent",""),["pending","placeholder","archival asset"]): fail(f"Placeholder text in scene config: {s['title']}")
 slug=cfg.get("slug",cfgpath.stem); out=Path(a.output) if a.output else ROOT/"output"/f"{slug}-region-short"; out.mkdir(parents=True,exist_ok=True); asset_dir=out/"images"; asset_dir.mkdir(exist_ok=True)
 script=cfg["story"]["narration"].strip()
 hooks=cfg.get("story",{}).get("hook_variants",{})
 hook=str(hooks.get(a.hook_variant,"")).strip()
 if len(hook.split()) not in range(3,6): fail(f"Hook variant {a.hook_variant} must contain 3–5 words")
 if "[REVIEW]" in script or "[DRAFT" in script or len(script.split())<110 or len(script.split())>155: fail("Narration must be reviewed, non-placeholder, and 110–155 words")
 for term in ("pending","placeholder","archival asset"):
  if term in script.lower() or term in cfgpath.read_text(encoding="utf-8").lower(): fail(f"Forbidden placeholder text '{term}' in narration/config")
 media=[]; credits=[]; used=set(); previous_digest=None
 selected={x.strip() for x in a.only_scenes.split(",") if x.strip()}
 unknown=selected-{str(x.get("title","")) for x in cfg["scenes"]}
 if unknown: fail("Unknown --only-scenes title(s): "+", ".join(sorted(unknown)))
 scene_rows=[(i,sc) for i,sc in enumerate(cfg["scenes"],1) if not selected or sc.get("title") in selected]
 for i,scene in scene_rows:
  scene_started=time.perf_counter()
  try:
      scene_started=time.perf_counter()
      if scene["image_type"]=="map": im,credit=make_map(scene,i,cfg,asset_dir)
      else:
       local=local_image(scene,i,cfg,used)
       if local: im,credit=local
       else:
        try: im,credit=get_image(scene,i,cfg,asset_dir,used,out)
        except Exception as resolver_error:
         print(f"SAFE FALLBACK for {scene['title']}: {resolver_error}",file=sys.stderr)
         im,credit=text_fallback_card(scene,i,cfg,asset_dir,resolver_error)
      if scene["image_type"]=="modern" and credit.get("provider")!="procedural illustration" and credit.get("period_ok") is not True:
       # Present-day scenes must be positively identified as a modern photograph by the CLIP period probe.
       reason=f"Modern/present image check failed for scene '{scene['title']}': not confirmed as a modern photograph"
       if a.visual_review_only:
        print(f"REVIEW CARD for {scene['title']}: {reason}",file=sys.stderr)
        im,credit=text_fallback_card(scene,i,cfg,asset_dir,reason)
       else:
        fail(reason)
      if credit.get("provider")!="fallback text card" and str(credit.get("license","")).lower().find("cc by")>=0 and not str(credit.get("author","")).strip():
       fail(f"Attribution-required asset has no author: {scene['title']}")
      credit.setdefault("warnings",[])
      print(f"TIMING scene_total[{i}:{scene['title']}]: {time.perf_counter()-scene_started:.2f}s",flush=True)
      digest=credit.get("content_sha256") or hashlib.sha256(im.tobytes()).hexdigest()
      if credit.get("provider") != "missing asset":
       if digest in {c.get("content_sha256") for c in credits if c.get("provider") != "missing asset"}: fail(f"Duplicate image content detected at scene '{scene['title']}'")
       if digest == previous_digest: fail(f"Consecutive scenes reuse the same image: '{scene['title']}'")
       previous_digest=digest
      credit["content_sha256"]=digest; used.add(digest)
      media.append((scene,im)); credits.append(credit)
  except Exception as scene_error:
   if not a.visual_review_only: raise
   print(f"REVIEW SCENE ERROR [{scene.get('title')}]: {type(scene_error).__name__}: {scene_error}",file=sys.stderr)
   im,credit=text_fallback_card(scene,i,cfg,asset_dir,scene_error)
   credit.setdefault("diagnostics",{}).setdefault("scene_exception",f"{type(scene_error).__name__}: {scene_error}")
   credit["content_sha256"]=hashlib.sha256(f"missing:{i}:{scene.get('title','Scene')}".encode("utf-8")).hexdigest()
   credit.setdefault("warnings",[])
   media.append((scene,im)); credits.append(credit)
  finally:
   print(f"TIMING scene_total[{i}:{scene['title']}]: {time.perf_counter()-scene_started:.2f}s",flush=True)
 write_approval_board(media,credits,cfg,out)
 if a.visual_review_only: print(f"Visual review artifacts saved to {out}; no narration, audio, or publishing generated."); return
 if any(x.get("provider") in ("fallback text card", "procedural illustration", "missing asset") for x in credits): fail("Visual approval board saved, but production render is blocked: at least one scene has no approved source image. Replace every missing scene with a verified, rights-cleared, relevant image before final rendering.")
 # Kokoro only: no silent speech-provider fallback.
 try:
     import torch
     from kokoro import KPipeline
 except Exception as e: fail(f"Kokoro import failed: {type(e).__name__}: {e}")
 narration_language=str(cfg.get("story",{}).get("narration_language","English")).strip().lower()
 # Map language to Kokoro pipeline code
 lang_map = {"japanese": "j", "english": "a", "mandarin": "z", "spanish": "e", "french": "f", "korean": "k", "hindi": "h"}
 lang = lang_map.get(narration_language)
 if lang is None:
    # Fallback: try prefix matching for backwards compatibility
    if narration_language.startswith("japan"): lang = "j"
    elif narration_language.startswith("english"): lang = "a"
    elif narration_language.startswith("mandarin") or narration_language.startswith("chinese"): lang = "z"
    elif narration_language.startswith("spanish"): lang = "e"
    elif narration_language.startswith("french"): lang = "f"
    elif narration_language.startswith("korean"): lang = "k"
    elif narration_language.startswith("hindi"): lang = "h"
 if lang is None: fail(f"Unsupported narration_language {narration_language!r}; configure a supported language (English, Japanese, Mandarin, Spanish, French, Korean, Hindi)")
 # Use configured voice or default for the language
 default_voices = {"j": "jf_alpha", "a": "af_heart", "z": "zf_xiaobei", "e": "ef_dora", "f": "ff_siwis", "k": "kf_alpha", "h": "hf_alpha"}
 voice = cfg.get("story",{}).get("kokoro_voice", default_voices.get(lang, "af_heart"))
 torch.set_num_threads(2); print(f"Kokoro narration language: {narration_language} (pipeline={lang}, voice={voice})"); pipe=KPipeline(lang_code=lang,device="cpu")
 chunks=[]; chunk_texts=[]
 try:
    for gs,_,audio in pipe(script,voice=voice,speed=1.0):
        if audio is not None:
            chunks.append(np.asarray(audio.detach().cpu().numpy() if hasattr(audio,"detach") else audio,dtype=np.float32).reshape(-1)); chunk_texts.append(str(gs or ""))
 except Exception as e: fail(f"Kokoro synthesis failed: {type(e).__name__}: {e}")
 if not chunks: fail("Kokoro returned no narration audio")
 voice=np.concatenate(chunks); rate=24000; duration=len(voice)/rate
 if not 45<=duration<=60: fail(f"Narration duration {duration:.2f}s outside 45–60s; edit script, do not fallback")
 t=np.arange(len(voice))/rate; accent=cfg["visual_style"].get("accent_color","#CC5533")
 style=cfg.get("visual_style",{}); mood=style.get("music_mood","calm ambient").lower()
 # Original procedural ambient bed shaped by each region's visual/music brief.
 envelope=np.minimum(1,t/2)*np.minimum(1,(duration-t)/3)
 if any(k in mood for k in ("percussion","rhythmic","african","lagoon")):
  pulse=(np.sin(2*np.pi*2.0*t)>0.92).astype(np.float32)
  pulse=np.convolve(pulse,np.ones(1200,dtype=np.float32)/1200,mode="same")
  bed=(.010*np.sin(2*np.pi*73*t)+.006*np.sin(2*np.pi*110*t)+.009*pulse*np.sin(2*np.pi*55*t)).astype(np.float32)
 elif any(k in mood for k in ("plucked","strings","japan","reflective")):
  pulse=np.maximum(0,np.sin(2*np.pi*.55*t))**8
  bed=(.008*np.sin(2*np.pi*110*t)+.004*np.sin(2*np.pi*164.8*t)+.010*pulse*np.sin(2*np.pi*220*t)).astype(np.float32)
 else:
  bed=(.009*np.sin(2*np.pi*98*t)+.005*np.sin(2*np.pi*146.8*t)+.003*np.sin(2*np.pi*196*t)).astype(np.float32)
 bed*=envelope
 mixed=voice*.84+bed
 rms=float(np.sqrt(np.mean(mixed**2)))
 if rms<0.001: fail("Narration mix is silent or near-silent")
 mixed=np.clip(mixed*(0.12/rms),-.98,.98); wav=out/"narration_mix.wav"
 with wave.open(str(wav),"wb") as f: f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate); f.writeframes((mixed*32767).astype("<i2").tobytes())
 # Word times come from Kokoro chunk boundaries; scene cuts follow `starts_at` phrases (fallback: intent length).
 import timing
 words=script.split()
 wtimes=timing.word_times(words,[(t,len(a)) for t,a in zip(chunk_texts,chunks)],rate,duration)
 wordends=np.array([e for _,e in wtimes])
 fallback=[max(1,len(sc["narration_intent"].split())) for sc,_ in media]
 try: boundaries=np.array(timing.scene_ends([sc for sc,_ in media],words,wtimes,duration,fallback))
 except ValueError as e: fail(f"Scene timing config error: {e}")
 durations=np.diff(np.concatenate([[0.0],boundaries]))
 short_scenes=[sc["title"] for (sc,_),dd in zip(media,durations) if dd<1.5]
 if short_scenes: print(f"WARNING: scenes shorter than 1.5s: {short_scenes}",file=sys.stderr)
 video=out/(f"{slug}-draft-preview.mp4" if a.draft_preview else f"{slug}-history-short.mp4")
 cmd=["ffmpeg","-y","-hide_banner","-loglevel","error","-f","rawvideo","-pix_fmt","rgb24","-s",f"{W}x{H}","-r",str(FPS),"-i","-","-i",str(wav),"-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p","-c:a","aac","-b:a","160k","-ar",str(rate),"-shortest","-movflags","+faststart",str(video)]
 proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
 contact=[]
 for fi in range(round(duration*FPS)):
  tm=fi/FPS; si=min(len(media)-1,int(np.searchsorted(boundaries,tm,side="right"))); start=0 if si==0 else boundaries[si-1]; progress=max(0,min(1,(tm-start)/max(.01,durations[si]))); scene,im=media[si]
  # First two seconds use a full-bleed non-map visual with a 3–5-word hook overlay.
  opening=tm < 2.0
  if opening and scene.get("image_type")=="map":
   alt=next(((s,im0) for s,im0 in media if s.get("image_type")!="map"),None)
   if alt: scene,im=alt
  frame=smart_fit(im,progress).convert("RGBA"); frame=Image.alpha_composite(frame,Image.new("RGBA",(W,H),(0,0,0,35))); d=ImageDraw.Draw(frame,"RGBA")
  palette=style.get("palette",["dark-contrast"]); title_bg=color(palette[0],(28,28,28))
  if opening:
   hook_font=font(78,True); hook_lines=wrap(d,hook,hook_font,W-150); hy=180
   for line in hook_lines:
    tw=d.textlength(line,font=hook_font); d.text(((W-tw)//2,hy),line,font=hook_font,fill="white",stroke_width=4,stroke_fill="black"); hy+=96
  else:
   d.rounded_rectangle((42,85,W-42,190),radius=22,fill=(*title_bg,220)); d.text((68,105),scene["title"],font=font(56,True),fill="white",stroke_width=2,stroke_fill="black")
  wi=min(len(words)-1,int(np.searchsorted(wordends,tm,side="right"))); cf=font(48,True); box_top=H-450; box_bottom=H-265; lo,hi=caption_window(d,words,wi,cf,W-190); capwords=words[lo:hi]
  bbox=d.textbbox((0,0),"Ag",font=cf); text_h=bbox[3]-bbox[1]; y=box_top+(box_bottom-box_top-text_h)//2-bbox[1]
  d.rounded_rectangle((42,box_top,W-42,box_bottom),radius=26,fill=(0,0,0,210),outline=accent,width=4)
  # Word-by-word karaoke emphasis; active word accent colour, all other words white.
  full=" ".join(capwords); widths=[d.textlength(w+" ",font=cf) for w in capwords]; total=sum(widths); x=max(65,(W-total)//2)
  if total>W-130:
   cf=font(40,True); widths=[d.textlength(w+" ",font=cf) for w in capwords]; total=sum(widths); x=max(65,(W-total)//2)
  for j,w in enumerate(capwords):
   col=accent if lo+j==wi else "white"; d.text((x,y),w+" ",font=cf,fill=col,stroke_width=2,stroke_fill="black"); x+=widths[j]
  # Subtle channel mark throughout; a short branded end-card replaces a long generic intro.
  brand_font=font(25,True)
  d.rounded_rectangle((W-360,32,W-42,78),radius=16,fill=(0,0,0,135))
  d.text((W-340,42),"PAST THE POSTCARD",font=brand_font,fill="white")
  if tm >= max(0.0,duration-1.5):
   d.rounded_rectangle((110,H-700,W-110,H-535),radius=28,fill=(0,0,0,205),outline=accent,width=4)
   brand_end=font(43,True); brand_text="PAST THE POSTCARD"
   bw=d.textlength(brand_text,font=brand_end)
   d.text(((W-bw)//2,H-650),brand_text,font=brand_end,fill="white")
  # Crossfade into the next scene over the final 0.4 seconds of each scene.
  if si < len(media)-1 and tm >= boundaries[si]-0.4:
   alpha=max(0.0,min(1.0,(tm-(boundaries[si]-0.4))/0.4))
   nxt_scene,nxt_im=media[si+1]
   nxt=smart_fit(nxt_im,0.0).convert("RGBA")
   nd=ImageDraw.Draw(nxt,"RGBA")
   nd.rounded_rectangle((42,85,W-42,190),radius=22,fill=(*title_bg,220))
   nd.text((68,105),nxt_scene["title"],font=font(56,True),fill="white",stroke_width=2,stroke_fill="black")
   frame=Image.blend(frame.convert("RGB"),nxt.convert("RGB"),alpha)
  proc.stdin.write(np.asarray(frame.convert("RGB"),dtype=np.uint8).tobytes())
  if fi % (FPS*5)==0: contact.append(frame.convert("RGB").resize((180,320)))
 proc.stdin.close(); err=proc.stderr.read().decode("utf-8","replace"); rc=proc.wait()
 if rc: fail(f"FFmpeg encode failed {rc}: {err[-3000:]}")
 # Contact sheet includes one representative scene frame for every scene.
 sheet=Image.new("RGB",(180*4,320*math.ceil(len(media)/4)),(24,24,24))
 for i,(_,im) in enumerate(media):
  thumb=smart_fit(im,.4).resize((180,320)); sheet.paste(thumb,((i%4)*180,(i//4)*320))
 contactpath=out/"contact-sheet.jpg"; sheet.save(contactpath,quality=90)
 (out/"credits.json").write_text(json.dumps(credits,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
 block="\n".join(f"- {c['title']} — {c['author']}; {c['license']} ({c['license_url']}); source: {c['source_url']}" for c in credits)
 (out/"credits-description.txt").write_text("Image and map credits\n"+block+"\n",encoding="utf-8")
 (out/"description.txt").write_text("Image and map credits\n"+block+"\n",encoding="utf-8")
 _timed("total_render",run_started)
 # Automated validation: real audio, duration, format, non-silence, uniqueness and placeholder text.
 probe=subprocess.run(["ffprobe","-v","error","-show_entries","stream=codec_type,width,height,r_frame_rate","-show_entries","format=duration","-of","json",str(video)],capture_output=True,text=True)
 if probe.returncode: fail("ffprobe failed: "+probe.stderr)
 info=json.loads(probe.stdout); streams=info.get("streams",[]); aud=next((x for x in streams if x.get("codec_type")=="audio"),None); vid=next((x for x in streams if x.get("codec_type")=="video"),{})
 if not aud: fail("QA failed: no audio track")
 if float(np.max(np.abs(mixed)))<0.002 or float(np.sqrt(np.mean(mixed**2)))<0.001: fail("QA failed: audio is silent or near-silent")
 sec=float(info["format"]["duration"])
 if not 45<=sec<=60: fail(f"QA failed: duration {sec:.2f}s outside 45–60s")
 if (vid.get("width"),vid.get("height"),vid.get("r_frame_rate"))!=(1080,1920,"30/1"): fail(f"QA failed: wrong video stream {vid}")
 files=[c["file"] for c in credits if "map" not in c["file"]]
 if len(files)!=len(set(files)): fail("QA failed: duplicate image file used")
 # OCR frame scan via tesseract CLI if available; fail if placeholder text is detected.
 warnings=[f"Scene shorter than 1.5s: {t}" for t in short_scenes]
 try:
  import pytesseract
  for i,(_,im) in enumerate(media):
   text=pytesseract.image_to_string(im.resize((360,640))).lower()
   if any(term in text for term in ("archival asset pending","editorial title card","visual needed","placeholder")): fail(f"QA failed: placeholder text OCR detected in scene {i+1}")
 except ImportError as e: fail(f"QA OCR dependency missing; cannot certify frames: {e}")
 qa={"region":cfg["name"],"duration_seconds":sec,"resolution":"1080x1920","fps":30,"audio_track":True,"audio_rms":float(np.sqrt(np.mean(mixed**2))),"visual_style":style,"hook_variant":a.hook_variant,"hook_text":hook,"ambient_audio":"procedurally synthesized; no external recording","scene_count":len(media),"duplicate_image_check":"passed","placeholder_ocr":"passed","claim_sources":cfg.get("claim_sources",{}),"source_list":cfg.get("sources",[]),"warnings":warnings,"scenes":[{"scene":c["scene"],"image":c["file"],"source":c["source_url"],"license":c["license"],"match_score":c["match_score"],"warnings":c["warnings"]} for c in credits]}
 (out/"qa-report.json").write_text(json.dumps(qa,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
 (out/"narration.txt").write_text(script+"\n",encoding="utf-8")
 print(f"QA PASSED: {video} ({sec:.2f}s), {len(media)} scenes; contact sheet and report at {out}")
if __name__=="__main__":
 try: main()
 except Exception as e: print(f"BUILD FAILED: {type(e).__name__}: {e}",file=sys.stderr); raise
