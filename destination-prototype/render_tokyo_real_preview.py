#!/usr/bin/env python3
"""Render a real, rights-attributed Tokyo-history preview. No placeholder fallback."""
from __future__ import annotations
import datetime as dt, html, json, math, os, re, subprocess, sys, time
from pathlib import Path
from urllib.parse import quote
import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
import wave

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "tokyo-real-preview"
ASSETS = ROOT / "destination-prototype" / "assets"
W, H, FPS = 1080, 1920, 30
COMMONS = "https://commons.wikimedia.org/w/api.php"
UA = "TokyoHistoryPreview/1.0 (GitHub Actions; rights-attributed educational preview)"
SCRIPT = ("Tokyo was once known as Edo, a castle town that changed Japan’s future. In 1603, Tokugawa Ieyasu established his shogunate here, and Edo grew into a powerful political center. Merchants filled its streets, boats moved goods along canals, and artists turned everyday life into celebrated woodblock prints. By the eighteenth century, Edo was one of the world’s great cities, even while the imperial court remained in Kyoto. Then came 1868: the shogunate ended, the emperor’s government moved east, and Edo became Tokyo, the eastern capital. Fires, earthquakes, and war transformed the city, but its older story never disappeared completely. Look closely at gardens, temple grounds, street plans, and Nihonbashi, once a vital bridge for journeys across Japan. Today, Tokyo layers modern towers over centuries of history. Choose one historic district, walk slowly, and let the old city emerge between the new.")
SCENES = [
 ("Edo, before Tokyo", "Edo-period cityscape woodblock print Japan", "still"),
 ("A castle town rises", "Edo Castle historical illustration Japan", "still"),
 ("The shogunate begins", "Tokugawa Ieyasu portrait public domain", "still"),
 ("A city of merchants", "Edo period Nihonbashi bridge ukiyo-e", "still"),
 ("Roads across Japan", "", "map"),
 ("A metropolis of culture", "Edo period ukiyo-e street scene public domain", "still"),
 ("Kyoto and Edo", "", "map"),
 ("The year 1868", "Meiji Restoration 1868 historical photograph Japan", "still"),
 ("Edo becomes Tokyo", "Tokyo historical document 1868 Japan", "still"),
 ("Gardens remember", "Edo period Japanese garden historical photograph", "still"),
 ("Nihonbashi", "Nihonbashi bridge Edo ukiyo-e public domain", "still"),
 ("Routes to the capital", "", "map"),
 ("Prints and theatre", "Ukiyo-e kabuki theatre public domain", "still"),
 ("A changing skyline", "Tokyo old city historical photograph public domain", "still"),
 ("History in the present", "Edo Tokyo Museum historical artefact", "still"),
 ("Walk the old city", "Tokyo historic district traditional street", "still"),
]
ALLOWED = ("public domain", "public domain mark", "cc0", "cc by", "cc-by", "cc by-sa", "cc-by-sa")
S = requests.Session(); S.headers.update({"User-Agent": UA})
def fail(msg): raise RuntimeError(msg)
def license_ok(short, url):
    x = (short or "").strip().lower()
    u = (url or "").strip().lower()
    if any(k in x for k in ("noncommercial","non-commercial","cc by-nc","cc-by-nc","cc by-nd","cc-by-nd","all rights reserved")): return False
    return any(k in x for k in ALLOWED) or "creativecommons.org/publicdomain" in u or "creativecommons.org/licenses/by/" in u or "creativecommons.org/licenses/by-sa/" in u
def font(size, bold=False):
    paths = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for p in paths:
        if Path(p).exists(): return ImageFont.truetype(p,size)
    return ImageFont.load_default()
def wrap(draw, text, f, maxw):
    out=[]; line=""
    for word in text.split():
        t=(line+" "+word).strip()
        if line and draw.textbbox((0,0),t,font=f)[2] > maxw: out.append(line); line=word
        else: line=t
    if line: out.append(line)
    return out
def commons_image(query, index):
    params={"action":"query","format":"json","generator":"search","gsrsearch":f'filetype:bitmap {query} (incategory:Public_domain OR incategory:CC-BY OR incategory:CC-BY-SA OR incategory:CC0)',
            "gsrnamespace":6,"gsrlimit":30,"prop":"imageinfo","iiprop":"url|extmetadata","iiurlwidth":1800}
    try: data=S.get(COMMONS,params=params,timeout=(10,40)); data.raise_for_status(); payload=data.json()
    except Exception as e: fail(f"Wikimedia Commons API failed for scene {index} ({query}): {type(e).__name__}: {e}")
    pages=payload.get("query",{}).get("pages",{})
    candidates=[]
    for p in pages.values():
        info=(p.get("imageinfo") or [{}])[0]; meta=info.get("extmetadata") or {}
        short=html.unescape((meta.get("LicenseShortName") or {}).get("value","")).strip()
        licurl=html.unescape((meta.get("LicenseUrl") or {}).get("value","")).strip()
        if not license_ok(short,licurl): continue
        url=info.get("thumburl") or info.get("url")
        page=info.get("descriptionurl")
        if url and page and url.startswith("https://") and page.startswith("https://"):
            artist=html.unescape(re.sub("<[^>]+>"," ",(meta.get("Artist") or {}).get("value","Unknown / not stated"))).strip() or "Unknown / not stated"
            candidates.append((p,info,short,licurl,artist,url,page))
    for p,info,short,licurl,artist,url,page in candidates:
        try:
            response=S.get(url,timeout=(10,60)); response.raise_for_status()
            from io import BytesIO
            im=Image.open(BytesIO(response.content)).convert("RGB")
            if im.width<640 or im.height<360: continue
            path=ASSETS/f"commons_{index:02d}.jpg"; im.save(path,quality=94)
            return im, {"scene_id":f"scene-{index:02d}","file":str(path.relative_to(ROOT)),"title":p.get("title",""),
              "author":artist,"license":short,"rights_statement":short,"license_url":licurl or page,
              "source_url":page,"download_url":url,"date_retrieved":dt.date.today().isoformat()}
        except Exception as e:
            print(f"Rejected broken Commons candidate {p.get('title')}: {type(e).__name__}: {e}",file=sys.stderr)
    fail(f"No usable Public Domain/CC image found for scene {index}: {query}")
def tile_xy(lon,lat,z):
    lat=max(-85,min(85,lat)); n=2**z
    return ((lon+180)/360*n, (1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)
def map_image(index):
    # Real OpenStreetMap raster tiles, with a real Tokyo-Kyoto route drawn on the map.
    from io import BytesIO
    z=6; center=tile_xy(137.2,36.1,z); tx0=int(center[0])-1; ty0=int(center[1])-1
    canvas=Image.new("RGB",(768,768),(235,232,220))
    for dx in range(3):
      for dy in range(3):
        x,y=tx0+dx,ty0+dy
        try:
          r=S.get(f"https://tile.openstreetmap.org/{z}/{x}/{y}.png",timeout=(10,30),headers={"User-Agent":UA}); r.raise_for_status()
          tile=Image.open(BytesIO(r.content)).convert("RGB")
          canvas.paste(tile,(dx*256,dy*256))
        except Exception as e: fail(f"OpenStreetMap tile download failed z={z} x={x} y={y}: {type(e).__name__}: {e}")
        time.sleep(.12)
    # Route points plotted in the map's Web Mercator tile coordinate space.
    pts=[]
    for lon,lat in [(135.7681,35.0116),(136.9066,35.1815),(137.7,35.3),(139.6917,35.6895)]:
      x,y=tile_xy(lon,lat,z); pts.append(((x-tx0)*256,(y-ty0)*256))
    d=ImageDraw.Draw(canvas); d.line(pts,fill=(196,38,35),width=7,joint="curve")
    for x,y in (pts[0],pts[-1]): d.ellipse((x-9,y-9,x+9,y+9),fill=(196,38,35),outline="white",width=3)
    d.text((20,20),"KYOTO → TOKYO",font=font(25,True),fill=(35,30,25),stroke_width=2,stroke_fill="white")
    d.text((20,730),"© OpenStreetMap contributors",font=font(15),fill=(25,25,25),stroke_width=2,stroke_fill="white")
    return canvas, {"scene_id":f"scene-{index:02d}","file":"generated OpenStreetMap route map","author":"OpenStreetMap contributors",
      "license":"Open Database License (ODbL)","rights_statement":"© OpenStreetMap contributors; data available under ODbL",
      "license_url":"https://www.openstreetmap.org/copyright","source_url":"https://www.openstreetmap.org/","date_retrieved":dt.date.today().isoformat()}
def cover(im, progress):
    scale=max(W/im.width,H/im.height)*(1+0.10*progress)
    rw,rh=round(im.width*scale),round(im.height*scale)
    im=im.resize((rw,rh),Image.Resampling.LANCZOS)
    dx=max(0,rw-W); dy=max(0,rh-H)
    x=int(dx*(.2+.6*progress)); y=int(dy*(.2+.6*progress))
    return im.crop((x,y,x+W,y+H))
def frame(im,title,caption,progress):
    canvas=cover(im,progress).convert("RGB")
    canvas=ImageEnhance.Color(canvas).enhance(.86)
    canvas=Image.alpha_composite(canvas.convert("RGBA"),Image.new("RGBA",(W,H),(0,0,0,40)))
    d=ImageDraw.Draw(canvas,"RGBA")
    tf,cf=font(64,True),font(50,True)
    lines=wrap(d,title,tf,W-130); y=115
    d.rounded_rectangle((45,70,W-45,70+len(lines)*78+40),radius=24,fill=(0,0,0,145))
    for line in lines[:2]: d.text((75,y),line,font=tf,fill="white",stroke_width=2,stroke_fill=(0,0,0,180)); y+=76
    # Captions use readable chunks with karaoke word timing supplied in ASS.
    cl=wrap(d,caption,cf,W-120); cl=cl[:3]; boxh=len(cl)*65+46; top=H-150-boxh
    d.rounded_rectangle((48,top,W-48,H-85),radius=24,fill=(0,0,0,205),outline=(255,255,255,80),width=2)
    for line in cl:
      d.text((75,top+20),line,font=cf,fill="white",stroke_width=2,stroke_fill=(0,0,0,220)); top+=65
    return canvas.convert("RGB")
def main():
    OUT.mkdir(parents=True,exist_ok=True); ASSETS.mkdir(parents=True,exist_ok=True)
    # Existing local stills are preferred only when filename explicitly matches a scene
    # and the matching file has provenance in a previous credits.json. Otherwise Commons is queried.
    oldcredits=OUT/"credits.json"
    old={}
    if oldcredits.exists():
      try: old={x.get("file"):x for x in json.loads(oldcredits.read_text())}
      except Exception: old={}
    media=[]; credits=[]
    for i,(title,query,kind) in enumerate(SCENES,1):
      if kind=="map":
        im,credit=map_image(i)
      else:
        local_candidates=[]
        tokens=[t for t in re.findall(r"[a-z0-9]+",title.lower()) if len(t)>3]
        for p in list(ASSETS.glob("*")):
          if p.suffix.lower() in {".jpg",".jpeg",".png",".webp"} and any(t in p.stem.lower() for t in tokens) and p.name in old:
            local_candidates.append(p)
        if local_candidates:
          p=local_candidates[0]; im=Image.open(p).convert("RGB"); credit=old[p.name]
        else:
          im,credit=commons_image(query,i)
      if im.width<640 or im.height<360: fail(f"Scene {i} image too small: {im.size}")
      media.append((title,im))
      credits.append(credit)
      print(f"Scene {i:02d}/{len(SCENES)} image ready: {title}")
    (OUT/"credits.json").write_text(json.dumps(credits,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    # Full-story Kokoro narration. No speech fallback: failure is fatal.
    try:
      import torch
      from kokoro import KPipeline
    except Exception as e: fail(f"Kokoro import failed (no fallback permitted): {type(e).__name__}: {e}")
    torch.set_num_threads(2); pipe=KPipeline(lang_code="b",device="cpu")
    chunks=[]
    try:
      for _,_,audio in pipe(SCRIPT,voice="bm_george",speed=1.0):
        if audio is not None:
          if hasattr(audio,"detach"): audio=audio.detach().cpu().numpy()
          chunks.append(np.asarray(audio,dtype=np.float32).reshape(-1))
    except Exception as e: fail(f"Kokoro synthesis failed: {type(e).__name__}: {e}")
    if not chunks: fail("Kokoro returned no audio chunks")
    voice=np.concatenate(chunks); duration=len(voice)/24000
    if not 45<=duration<=60: fail(f"Kokoro narration duration is {duration:.2f}s, outside 45–60s; revise script/speed rather than substituting audio")
    # Low-volume generated ambient bed; no copyrighted track.
    rate=24000; t=np.arange(len(voice),dtype=np.float32)/rate
    music=(.018*np.sin(2*np.pi*110*t)+.009*np.sin(2*np.pi*164.81*t)+.006*np.sin(2*np.pi*220*t)).astype(np.float32)
    fade=np.minimum(1,t/2)*np.minimum(1,(duration-t)/3); music*=fade
    mixed=np.clip(voice*.88+music, -.98,.98)
    wav=OUT/"mix.wav"
    with wave.open(str(wav),"wb") as f:
      f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate); f.writeframes((mixed*32767).astype("<i2").tobytes())
    # Allocate each scene in story order, scaled to measured speech length.
    weights=np.array([max(1,len(re.findall(r"\w+",caption))) for _,caption,_ in SCENES],dtype=float)
    durations=duration*weights/weights.sum()
    # Build one continuous raw RGB stream; every still gets motion and adjacent frames crossfade.
    cmd=["ffmpeg","-y","-hide_banner","-loglevel","error","-f","rawvideo","-pix_fmt","rgb24","-s",f"{W}x{H}","-r",str(FPS),"-i","-","-i",str(wav),
         "-c:v","libx264","-preset","veryfast","-crf","21","-pix_fmt","yuv420p","-c:a","aac","-b:a","160k","-ar",str(rate),"-shortest","-movflags","+faststart",str(OUT/"tokyo-history-preview.mp4")]
    proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
      nframes=round(duration*FPS); cumulative=np.cumsum(durations)
      cross=12 # 0.4 sec transition at 30 fps
      for fi in range(nframes):
        tm=fi/FPS; si=min(len(media)-1,int(np.searchsorted(cumulative,tm,side="right")))
        start=0 if si==0 else cumulative[si-1]; local=max(0,min(1,(tm-start)/max(.1,durations[si])))
        current=frame(media[si][1],media[si][0],SCRIPT,local) if False else cover(media[si][1],local).convert("RGB")
        # Large title/caption treatment; captions are overlaid as real video text on each frame.
        draw=ImageDraw.Draw(current,"RGBA"); title=media[si][0]; tf=font(64,True)
        draw.rounded_rectangle((45,75,W-45,185),radius=24,fill=(0,0,0,165))
        draw.text((75,92),title,font=tf,fill="white",stroke_width=2,stroke_fill=(0,0,0,200))
        # Word-timed karaoke captions: estimate individual token timing against full narration.
        tokens=SCRIPT.split(); weights_word=np.array([max(1,len(re.sub(r"\W","",x))) for x in tokens],dtype=float); ww=weights_word/weights_word.sum()
        ends=np.cumsum(ww)*duration; wi=min(len(tokens)-1,int(np.searchsorted(ends,tm,side="right")))
        first=max(0,wi-3); last=min(len(tokens),wi+4); cap=" ".join(tokens[first:last])
        cf=font(47,True); lines=wrap(draw,cap,cf,W-120); lines=lines[-2:]; bh=len(lines)*60+40; top=H-120-bh
        draw.rounded_rectangle((45,top,W-45,H-70),radius=22,fill=(0,0,0,215),outline=(255,255,255,80),width=2)
        for line in lines:
          draw.text((70,top+18),line,font=cf,fill="white",stroke_width=2,stroke_fill=(0,0,0,220)); top+=60
        # Crossfade each scene boundary over the last 12 frames of the outgoing scene.
        if si < len(media)-1 and tm >= cumulative[si]-cross/FPS:
          alpha=max(0,min(1,(tm-(cumulative[si]-cross/FPS))/(cross/FPS)))
          nxt=cover(media[si+1][1],0).convert("RGB")
          nd=ImageDraw.Draw(nxt,"RGBA"); nd.rounded_rectangle((45,75,W-45,185),radius=24,fill=(0,0,0,165)); nd.text((75,92),media[si+1][0],font=tf,fill="white",stroke_width=2,stroke_fill=(0,0,0,200))
          current=Image.blend(current,nxt,alpha)
        proc.stdin.write(np.asarray(current,dtype=np.uint8).tobytes())
      proc.stdin.close()
    except Exception:
      if proc.stdin and not proc.stdin.closed: proc.stdin.close()
      proc.kill(); raise
    stderr=proc.stderr.read().decode("utf-8","replace"); rc=proc.wait()
    if rc: fail(f"FFmpeg encode failed (exit {rc}): {stderr[-4000:]}")
    video=OUT/"tokyo-history-preview.mp4"
    probe=subprocess.run(["ffprobe","-v","error","-show_entries","stream=codec_type,width,height,r_frame_rate","-show_entries","format=duration","-of","json",str(video)],capture_output=True,text=True)
    if probe.returncode: fail(f"ffprobe failed: {probe.stderr}")
    info=json.loads(probe.stdout); streams=info.get("streams",[])
    if not any(x.get("codec_type")=="audio" for x in streams): fail("Validation failed: MP4 has no audio stream")
    v=next((x for x in streams if x.get("codec_type")=="video"),{})
    if (v.get("width"),v.get("height"),v.get("r_frame_rate"))!=(1080,1920,"30/1"): fail(f"Validation failed: video stream metadata is {v}")
    sec=float(info["format"]["duration"])
    if not 45<=sec<=60: fail(f"Validation failed: encoded duration {sec:.2f}s outside 45–60s")
    # Ensure no known placeholder labels were accidentally baked into frames.
    for forbidden in (b"archival asset pending",b"editorial title card",b"visual needed"):
      # OCR is intentionally not guessed; this scan checks generated strings/source script for prohibited placeholder text.
      if forbidden.decode() in SCRIPT.lower(): fail(f"Validation failed: prohibited placeholder text found: {forbidden.decode()}")
    (OUT/"validation.json").write_text(json.dumps({"duration_seconds":sec,"width":1080,"height":1920,"fps":30,"audio_stream":True,
      "scene_count":len(SCENES),"credits_file":"credits.json","placeholder_fallback":False,"narration":"Kokoro","ambient_bed":"generated low-volume synth"},indent=2)+"\n")
    print(f"VALIDATED: {video} duration={sec:.2f}s 1080x1920 30fps audio=yes; credits={len(credits)}")
if __name__=="__main__":
  try: main()
  except Exception as exc:
    print(f"BUILD FAILED: {type(exc).__name__}: {exc}",file=sys.stderr); raise
