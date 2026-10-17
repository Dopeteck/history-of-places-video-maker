#!/usr/bin/env python3
"""Config-driven region short renderer. Fails closed on rights, relevance, media and QA."""
from __future__ import annotations
import argparse, datetime as dt, hashlib, html, json, math, re, subprocess, sys, time, wave
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
def font(size,bold=False):
 p="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
 return ImageFont.truetype(p,size) if Path(p).exists() else ImageFont.load_default()
def wrap(draw,text,f,maxw):
 out=[]; line=""
 for word in text.split():
  test=(line+" "+word).strip()
  if line and draw.textbbox((0,0),test,font=f)[2]>maxw: out.append(line); line=word
  else: line=test
 if line: out.append(line)
 return out
def banned(text,terms): return any(t.lower() in text.lower() for t in terms)
def score_candidate(title,description,cats,required):
 hay=(title+" "+description+" "+" ".join(cats)).lower()
 return sum(3 if k.lower() in title.lower() else 2 if k.lower() in description.lower() else 1 for k in required if k.lower() in hay)
def get_image(scene,idx,config,asset_dir,used,output):
 terms=config.get("image_banned_terms",[])+scene.get("banned_keywords",[])
 variants=scene["image_search_terms"]
 tried=[]; ranked=[]
 for query in variants:
  tried.append(query)
  payload=get(COMMONS,{"action":"query","format":"json","generator":"search","gsrsearch":query,"gsrnamespace":6,"gsrlimit":30,"prop":"imageinfo|categories","cllimit":30,"iiprop":"url|extmetadata|size","iiurlwidth":2000}).json()
  for p in payload.get("query",{}).get("pages",{}).values():
   title=p.get("title",""); info=(p.get("imageinfo") or [{}])[0]; meta=info.get("extmetadata",{})
   desc=clean(meta.get("ImageDescription",{}).get("value","")); cats=[c.get("title","") for c in p.get("categories",[])]
   alltext=title+" "+desc+" "+" ".join(cats)
   if banned(alltext,terms) or not allowed(clean(meta.get("LicenseShortName",{}).get("value","")),clean(meta.get("LicenseUrl",{}).get("value",""))): continue
   match=score_candidate(title,desc,cats,scene.get("required_keywords",[]))
   if match<=0: continue
   url=info.get("thumburl") or info.get("url"); page=info.get("descriptionurl")
   if not url or not page or not url.startswith("https://") or not page.startswith("https://"): continue
   if title in used: continue
   ranked.append((match,title,desc,cats,info,meta,url,page))
 ranked.sort(key=lambda x:(-x[0],-int(x[4].get("width",0))*int(x[4].get("height",0))))
 for match,title,desc,cats,info,meta,url,page in ranked:
  try:
   data=get(url,timeout=(10,60)).content; digest=hashlib.sha256(data).hexdigest()
   if digest in used: continue
   im=Image.open(BytesIO(data)).convert("RGB")
   if min(im.size)<1080 or max(im.size)/min(im.size)>3.0 or len(data)<40000: continue
   # OCR check when Tesseract is available; reject images with substantial text coverage.
   try:
    import pytesseract
    txt=pytesseract.image_to_data(im.resize((min(900,im.width),int(im.height*min(900,im.width)/im.width))),output_type=pytesseract.Output.DICT)
    words=[x.strip() for x in txt["text"] if x.strip()]
    conf=[float(x) for x in txt["conf"] if str(x).replace(".","",1).isdigit() and float(x)>=30]
    if len(words)>=18 and len(conf)>=14: continue
   except Exception: pass
   filename=f"scene_{idx:02d}.jpg"; path=asset_dir/filename; im.save(path,quality=94)
   lic=clean(meta.get("LicenseShortName",{}).get("value",""))
   author=clean(meta.get("Artist",{}).get("value","Unknown / not stated"))
   used.add(title); used.add(digest)
   return im,{"content_sha256":digest,"scene":scene["title"],"file":str(path.relative_to(ROOT)),"title":title,"author":author,"license":lic,"license_url":clean(meta.get("LicenseUrl",{}).get("value","")),"source_url":page,"match_score":match,"search_variant":query,"warnings":[]}
  except Exception as e:
   print(f"Candidate rejected for {scene['title']}: {title}: {type(e).__name__}: {e}",file=sys.stderr)
 fail(f"No acceptable image for scene '{scene['title']}'. Tried variants: {tried}; ranked relevant candidates: {len(ranked)}")
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
   if match<=0 or not allowed(lic,licurl) or not source.startswith("https://") or not licurl.startswith("https://"): continue
   if banned(title+" "+desc+" "+" ".join(rec.get("categories",[])),config.get("image_banned_terms",[])+scene.get("banned_keywords",[])): continue
   im=Image.open(p).convert("RGB")
   if min(im.size)<1080 or hashlib.sha256(p.read_bytes()).hexdigest() in used: continue
   digest=hashlib.sha256(p.read_bytes()).hexdigest(); used.add(digest)
   return im,{"content_sha256":digest,"scene":scene["title"],"file":str(p.relative_to(ROOT)),"title":title,"author":str(rec.get("author") or "Unknown / not stated"),"license":lic,"license_url":licurl,"source_url":source,"match_score":match,"search_variant":"repository manifest (preferred local still)","warnings":[]}
 return None
def make_map(scene,idx,config,asset_dir):
 from PIL import ImageDraw
 lat=float(config["map"]["latitude"]); lon=float(config["map"]["longitude"])
 # OSM tile background centered on the configured place, with a real location marker.
 z=9; n=2**z; x=(lon+180)/360*n; y=(1-math.asinh(math.tan(math.radians(max(-85,min(85,lat))))/math.pi)/2)*n
 tx,ty=int(x),int(y); canvas=Image.new("RGB",(768,768),(238,236,226))
 for dx in range(-1,2):
  for dy in range(-1,2):
   xx,yy=tx+dx,ty+dy
   r=get(f"https://tile.openstreetmap.org/{z}/{xx}/{yy}.png",timeout=(10,30)); canvas.paste(Image.open(BytesIO(r.content)).convert("RGB"),((dx+1)*256,(dy+1)*256))
 px=384+(x-tx)*256; py=384+(y-ty)*256; d=ImageDraw.Draw(canvas); d.ellipse((px-16,py-16,px+16,py+16),fill="#D33A32",outline="white",width=5); d.text((20,20),config["map"].get("label",config["name"]).upper(),font=font(28,True),fill="#171717",stroke_width=3,stroke_fill="white"); d.text((20,730),"© OpenStreetMap contributors",font=font(14),fill="#111111",stroke_width=2,stroke_fill="white")
 p=asset_dir/f"scene_{idx:02d}_map.jpg"; canvas.save(p,quality=94)
 return canvas,{"content_sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"scene":scene["title"],"file":str(p.relative_to(ROOT)),"title":"OpenStreetMap location map","author":"OpenStreetMap contributors","license":"ODbL","license_url":"https://www.openstreetmap.org/copyright","source_url":"https://www.openstreetmap.org/","match_score":1,"search_variant":"OSM tile map centred on configured coordinates","warnings":[]}
def smart_fit(im,progress):
 # Portrait images use focal-aware fit; wide images use blurred fill rather than cutting through the subject.
 if im.width/im.height < .78:
  scale=max(W/im.width,H/im.height)*(1+.07*progress); rw,rh=round(im.width*scale),round(im.height*scale); big=im.resize((rw,rh),Image.Resampling.LANCZOS); dx=max(0,rw-W); dy=max(0,rh-H); x=int(dx*(.25+.5*progress)); y=int(dy*(.25+.5*progress)); return big.crop((x,y,x+W,y+H))
 bg=ImageOps.fit(im,(W,H),method=Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(28))
 fg=im.copy(); fg.thumbnail((W-80,H-360),Image.Resampling.LANCZOS)
 bg=ImageEnhance.Brightness(bg).enhance(.48); bg.paste(fg,((W-fg.width)//2,(H-fg.height)//2)); return bg
def main():
 ap=argparse.ArgumentParser(); ap.add_argument("--config",required=True); ap.add_argument("--output",default=""); a=ap.parse_args()
 cfgpath=Path(a.config); cfg=yaml.safe_load(cfgpath.read_text(encoding="utf-8"))
 if not cfg.get("name") or len(cfg.get("scenes",[]))<8 or len(cfg["scenes"])>12: fail("Config needs name and 8–12 scenes")
 if len(set(s["title"] for s in cfg["scenes"]))!=len(cfg["scenes"]): fail("Scene titles must be unique")
 for s in cfg["scenes"]:
  if len(s.get("image_search_terms",[]))<3: fail(f"Scene '{s.get('title')}' needs at least 3 search variants")
  if not s.get("required_keywords"): fail(f"Scene '{s.get('title')}' needs required_keywords")
  if s.get("image_type") not in ("historic","modern","map"): fail(f"Scene '{s.get('title')}' has invalid image_type")
  if banned(s.get("title","")+" "+s.get("narration_intent",""),["pending","placeholder","archival asset"]): fail(f"Placeholder text in scene config: {s['title']}")
 slug=cfg.get("slug",cfgpath.stem); out=Path(a.output) if a.output else ROOT/"output"/f"{slug}-region-short"; out.mkdir(parents=True,exist_ok=True); asset_dir=out/"images"; asset_dir.mkdir(exist_ok=True)
 script=cfg["story"]["narration"].strip()
 if "[REVIEW]" in script or "[DRAFT" in script or len(script.split())<110 or len(script.split())>155: fail("Narration must be reviewed, non-placeholder, and 110–155 words")
 for term in ("pending","placeholder","archival asset"):
  if term in script.lower() or term in cfgpath.read_text(encoding="utf-8").lower(): fail(f"Forbidden placeholder text '{term}' in narration/config")
 media=[]; credits=[]; used=set(); previous_digest=None
 for i,scene in enumerate(cfg["scenes"],1):
  if scene["image_type"]=="map": im,credit=make_map(scene,i,cfg,asset_dir)
  else:
   local=local_image(scene,i,cfg,used)
   if local: im,credit=local
   else: im,credit=get_image(scene,i,cfg,asset_dir,used,out)
  if scene["image_type"]=="modern" and not any(x in credit["title"].lower() for x in [cfg["name"].lower(),cfg["country"].lower()]):
   # Match must come from metadata; no quiet substitute for the present-day visual.
   if credit["match_score"]<3: fail(f"Modern/present image relevance gate failed for scene '{scene['title']}'")
  digest=credit.get("content_sha256") or hashlib.sha256(im.tobytes()).hexdigest()
  if digest in {c.get("content_sha256") for c in credits}: fail(f"Duplicate image content detected at scene '{scene['title']}'")
  if digest == previous_digest: fail(f"Consecutive scenes reuse the same image: '{scene['title']}'")
  credit["content_sha256"]=digest; previous_digest=digest
  media.append((scene,im)); credits.append(credit); print(f"IMAGE {i}/{len(cfg['scenes'])}: {scene['title']} score={credit['match_score']} {credit['title']}")
 # Kokoro only: no silent speech-provider fallback.
 try:
  import torch
  from kokoro import KPipeline
 except Exception as e: fail(f"Kokoro import failed: {type(e).__name__}: {e}")
 torch.set_num_threads(2); lang="j" if "Japanese" in cfg.get("languages",[]) else "b"; pipe=KPipeline(lang_code=lang,device="cpu")
 chunks=[]
 try:
  for _,_,audio in pipe(script,voice="jf_alpha" if lang=="j" else "bm_george",speed=1.0):
   if audio is not None: chunks.append(np.asarray(audio.detach().cpu().numpy() if hasattr(audio,"detach") else audio,dtype=np.float32).reshape(-1))
 except Exception as e: fail(f"Kokoro synthesis failed: {type(e).__name__}: {e}")
 if not chunks: fail("Kokoro returned no narration audio")
 voice=np.concatenate(chunks); rate=24000; duration=len(voice)/rate
 if not 45<=duration<=60: fail(f"Narration duration {duration:.2f}s outside 45–60s; edit script, do not fallback")
 t=np.arange(len(voice))/rate; accent=cfg["visual_style"].get("accent_color","#CC5533")
 # Low-level ambient bed, faded at ends. No external copyrighted track.
 bed=(.009*np.sin(2*np.pi*98*t)+.005*np.sin(2*np.pi*146.8*t)+.003*np.sin(2*np.pi*196*t)).astype(np.float32)
 bed*=np.minimum(1,t/2)*np.minimum(1,(duration-t)/3)
 mixed=voice*.84+bed
  rms=float(np.sqrt(np.mean(mixed**2)))
  if rms<0.001: fail("Narration mix is silent or near-silent")
  mixed=np.clip(mixed*(0.12/rms),-.98,.98); wav=out/"narration_mix.wav"
 with wave.open(str(wav),"wb") as f: f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate); f.writeframes((mixed*32767).astype("<i2").tobytes())
 # Scene durations follow scene narration intent lengths, then normalize to the actual narration duration.
 weights=np.array([max(1,len(s["narration_intent"].split())) for s,_ in media],dtype=float); durations=duration*weights/weights.sum(); boundaries=np.cumsum(durations)
 video=out/f"{slug}-history-short.mp4"
 cmd=["ffmpeg","-y","-hide_banner","-loglevel","error","-f","rawvideo","-pix_fmt","rgb24","-s",f"{W}x{H}","-r",str(FPS),"-i","-","-i",str(wav),"-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p","-c:a","aac","-b:a","160k","-ar",str(rate),"-shortest","-movflags","+faststart",str(video)]
 proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=subprocess.PIPE); words=script.split(); ww=np.array([max(1,len(re.sub(r"\W","",w))) for w in words],dtype=float); wordends=np.cumsum(ww/ww.sum())*duration
 contact=[]
 for fi in range(round(duration*FPS)):
  tm=fi/FPS; si=min(len(media)-1,int(np.searchsorted(boundaries,tm,side="right"))); start=0 if si==0 else boundaries[si-1]; progress=max(0,min(1,(tm-start)/max(.01,durations[si]))); scene,im=media[si]
  frame=smart_fit(im,progress).convert("RGBA"); frame=Image.alpha_composite(frame,Image.new("RGBA",(W,H),(0,0,0,35))); d=ImageDraw.Draw(frame,"RGBA")
  d.rounded_rectangle((42,85,W-42,190),radius=22,fill=(0,0,0,165)); d.text((68,105),scene["title"],font=font(56,True),fill="white",stroke_width=2,stroke_fill="black")
  wi=min(len(words)-1,int(np.searchsorted(wordends,tm,side="right"))); lo=max(0,wi-3); hi=min(len(words),wi+4); capwords=words[lo:hi]; cf=font(48,True); y=H-420
  d.rounded_rectangle((42,y-24,W-42,H-265),radius=26,fill=(0,0,0,210),outline=accent,width=4)
  # Word-by-word karaoke emphasis; active word accent colour, all other words white.
  full=" ".join(capwords); widths=[d.textlength(w+" ",font=cf) for w in capwords]; total=sum(widths); x=max(65,(W-total)//2)
  for j,w in enumerate(capwords):
   col=accent if lo+j==wi else "white"; d.text((x,y),w+" ",font=cf,fill=col,stroke_width=2,stroke_fill="black"); x+=widths[j]
  # Crossfade into the next scene over the final 0.4 seconds of each scene.
  if si < len(media)-1 and tm >= boundaries[si]-0.4:
   alpha=max(0.0,min(1.0,(tm-(boundaries[si]-0.4))/0.4))
   nxt_scene,nxt_im=media[si+1]
   nxt=smart_fit(nxt_im,0.0).convert("RGBA")
   nd=ImageDraw.Draw(nxt,"RGBA")
   nd.rounded_rectangle((42,85,W-42,190),radius=22,fill=(0,0,0,165))
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
 warnings=[]
 try:
  import pytesseract
  for i,(_,im) in enumerate(media):
   text=pytesseract.image_to_string(im.resize((360,640))).lower()
   if any(term in text for term in ("archival asset pending","editorial title card","visual needed","placeholder")): fail(f"QA failed: placeholder text OCR detected in scene {i+1}")
 except ImportError: warnings.append("pytesseract unavailable; OCR frame scan not run")
 qa={"region":cfg["name"],"duration_seconds":sec,"resolution":"1080x1920","fps":30,"audio_track":True,"audio_rms":float(np.sqrt(np.mean(mixed**2))),"scene_count":len(media),"duplicate_image_check":"passed","placeholder_ocr":"passed" if not warnings else "warning","warnings":warnings,"scenes":[{"scene":c["scene"],"image":c["file"],"source":c["source_url"],"license":c["license"],"match_score":c["match_score"],"warnings":c["warnings"]} for c in credits]}
 (out/"qa-report.json").write_text(json.dumps(qa,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
 (out/"narration.txt").write_text(script+"\n",encoding="utf-8")
 print(f"QA PASSED: {video} ({sec:.2f}s), {len(media)} scenes; contact sheet and report at {out}")
if __name__=="__main__":
 try: main()
 except Exception as e: print(f"BUILD FAILED: {type(e).__name__}: {e}",file=sys.stderr); raise
