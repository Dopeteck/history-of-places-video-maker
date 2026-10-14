#!/usr/bin/env python3
"""
main.py - lightweight short + long stickman video generator.

Pipeline
  1. generate_script_json()    LLM script (Groq -> Gemini -> OpenRouter fallback)
  2. generate_dialogue_audio() Edge-TTS per line + exact durations + word timings
  3. composite_video()         MoviePy layers (background + stickmen + audio),
                               then FFmpeg burns karaoke captions (ASS/libass)

Usage
  python main.py --category history --mode short
  python main.py --category explainer --mode long --minutes 3 --topic "how memory works"
  python main.py --prefetch-assets           # create placeholder stickmen + backgrounds
"""
import argparse
import asyncio
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import time
from math import sin
from pathlib import Path
from string import Template

# Route script-only commands before loading legacy renderer dependencies.

if __name__ == "__main__":
    if "--story" in sys.argv or "--mode=shorts" in sys.argv or any(
        arg == "--mode" and i + 1 < len(sys.argv) and sys.argv[i + 1] == "shorts"
        for i, arg in enumerate(sys.argv)
    ) or any(
        flag in sys.argv for flag in ("--render", "--smoke-test", "--script-only", "--require-approval", "--approve", "--draft", "--suggest-topics", "--recheck")
    ):
        from story_cli import main as story_main
        sys.exit(story_main([arg for arg in sys.argv[1:] if arg != "--story"]))

import requests
import config

log = logging.getLogger("stickman")


# ==========================================================================
# 1. SCRIPT GENERATION  (Groq -> Gemini -> OpenRouter)
# ==========================================================================
def _extract_json(raw: str) -> dict:
    """Parse JSON from an LLM reply, tolerating <think> blocks and code fences."""
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL)
    raw = re.sub(r"```(?:json)?", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    return json.loads(raw[start:end + 1])


def _validate_script(data: dict, category: str, topic: str) -> dict:
    """Normalise the LLM output so the renderer never sees bad data."""
    fmt = config.FORMATS[category]
    scenes = data.get("scenes")
    if not isinstance(scenes, list):
        raise ValueError("'scenes' missing or not a list")

    clean = []
    for s in scenes[:config.MAX_SCENES]:
        if not isinstance(s, dict):
            continue
        speaker = str(s.get("speaker", "A")).strip().upper()[:1]
        speaker = speaker if speaker in ("A", "B") else "A"
        text = " ".join(str(s.get("text", "")).split())
        if not text:
            continue
        pose = s.get("pose") if s.get("pose") in config.ALLOWED_POSES else "talking"
        reaction = s.get("reaction") if s.get("reaction") in config.ALLOWED_POSES else "standing"
        clean.append({"speaker": speaker, "text": text, "pose": pose, "reaction": reaction})
    if len(clean) < 4:
        raise ValueError(f"only {len(clean)} usable scenes")

    tags = data.get("hashtags") or []
    tags = [t if str(t).startswith("#") else f"#{t}" for t in tags if t][:8]
    return {
        "category": category,
        "topic": topic,
        "title": str(data.get("title") or topic)[:80],
        "description": str(data.get("description") or "")[:300],
        "hashtags": tags or ["#shorts", "#fyp"],
        "cast": fmt["cast"],
        "background": fmt["background"],
        "scenes": clean,
    }


def _call_openai_compatible(provider: dict, model: str, system: str, user: str) -> str:
    """Groq and OpenRouter both speak the OpenAI protocol -> use the openai SDK."""
    from openai import OpenAI  # lazy import: asset prefetch does not need API dependencies
    client = OpenAI(
        base_url=provider["base_url"],
        api_key=os.environ[provider["env_key"]],
        timeout=60,
        max_retries=0,   # we do our own retry/fallback logic
    )
    kwargs = dict(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.9,
    )
    if provider.get("json_mode"):
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content


def _call_gemini(provider: dict, model: str, system: str, user: str) -> str:
    import google.generativeai as genai  # lazy: only needed on fallback

    genai.configure(api_key=os.environ[provider["env_key"]])
    gm = genai.GenerativeModel(
        model,
        system_instruction=system,
        generation_config={"response_mime_type": "application/json", "temperature": 0.9},
    )
    return gm.generate_content(user, request_options={"timeout": 60}).text


def _is_rate_limit(exc: Exception) -> bool:
    s = f"{type(exc).__name__} {exc}".lower()
    return any(k in s for k in ("429", "ratelimit", "rate limit", "quota", "resourceexhausted"))


def generate_script_json(category: str, topic: str | None = None) -> dict:
    """Return a validated script dict. Falls through providers on 429/errors."""
    fmt = config.FORMATS[category]
    topic = topic or random.choice(fmt["seeds"])
    system = Template(config.SYSTEM_PROMPT).substitute(
        char_a=fmt["cast"]["A"],
        char_b=fmt["cast"]["B"],
        poses=", ".join(config.ALLOWED_POSES),
        min_scenes=config.MIN_SCENES,
        max_scenes=config.MAX_SCENES,
        min_words=config.TARGET_WORDS_MIN,
        max_words=config.TARGET_WORDS_MAX,
    )
    user = Template(fmt["prompt"]).substitute(topic=topic) + " Respond in JSON."
    log.info("Script topic: %s", topic)

    for provider in config.PROVIDERS:
        if not os.environ.get(provider["env_key"]):
            log.info("Skipping %s (no %s set)", provider["name"], provider["env_key"])
            continue
        call = _call_gemini if provider["type"] == "gemini" else _call_openai_compatible
        for model in provider["models"]:
            for attempt in range(1, config.MAX_RETRIES_PER_MODEL + 1):
                try:
                    log.info("LLM: %s / %s (attempt %d)", provider["name"], model, attempt)
                    raw = call(provider, model, system, user)
                    return _validate_script(_extract_json(raw), category, topic)
                except ValueError as e:      # includes JSONDecodeError -> bad output, retry
                    log.warning("Bad output from %s/%s: %s", provider["name"], model, e)
                except Exception as e:       # network / API errors
                    if _is_rate_limit(e):
                        log.warning("Rate limited on %s/%s -> falling back", provider["name"], model)
                        break                # next model / provider right away
                    log.warning("Error on %s/%s: %s", provider["name"], model, e)
                    time.sleep(2 * attempt)
    raise RuntimeError("All LLM providers failed. Check API keys / quotas.")


# ==========================================================================
# 2. DIALOGUE AUDIO  (Edge-TTS)
# ==========================================================================
async def _tts_line(text: str, cfg: dict, out_path: Path) -> list[dict]:
    """Synthesize one line to mp3; return word timings (seconds, relative to line)."""
    import edge_tts

    kwargs = dict(rate=cfg["rate"], pitch=cfg["pitch"])
    try:  # edge-tts >= 7 needs boundary=... to emit word events
        comm = edge_tts.Communicate(text, cfg["voice"], boundary="WordBoundary", **kwargs)
    except TypeError:
        comm = edge_tts.Communicate(text, cfg["voice"], **kwargs)

    words = []
    with open(out_path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # offsets are in 100-nanosecond ticks
                words.append({"text": chunk["text"], "start": chunk["offset"] / 1e7,
                              "dur": chunk["duration"] / 1e7})
    return words


def _audio_duration(path: Path) -> float:
    from moviepy.editor import AudioFileClip
    with AudioFileClip(str(path)) as clip:
        return float(clip.duration)


def _estimate_words(text: str, duration: float) -> list[dict]:
    """Fallback karaoke timing: spread words over the clip by character length."""
    toks = text.split()
    weights = [max(1, len(t)) for t in toks]
    total, t, out = sum(weights), 0.0, []
    for tok, w in zip(toks, weights):
        d = duration * w / total
        out.append({"text": tok, "start": t, "dur": d})
        t += d
    return out


def generate_dialogue_audio(script: dict, workdir: Path) -> list[dict]:
    """
    Render every line with its speaker's voice and build the master timeline.
    Returns a list of dicts: speaker, text, pose, reaction, audio, start, end,
    show_until (when the next line starts) and absolute word timings.
    """
    audio_dir = workdir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    timeline, cursor = [], 0.0
    for i, scene in enumerate(script["scenes"]):
        path = audio_dir / f"line_{i:02d}_{scene['speaker']}.mp3"
        words = []
        for attempt in range(1, 4):  # Edge-TTS is a web service -> retry transient failures
            try:
                words = asyncio.run(_tts_line(scene["text"], config.VOICES[scene["speaker"]], path))
                break
            except Exception as e:
                log.warning("TTS line %d attempt %d failed: %s", i, attempt, e)
                time.sleep(2 * attempt)
        else:
            raise RuntimeError(f"Edge-TTS failed for line {i}")

        dur = _audio_duration(path)                       # exact duration from the mp3
        if not words:
            words = _estimate_words(scene["text"], dur)
        for w in words:                                   # clamp to the real clip length
            w["start"] = min(w["start"], dur)
            w["dur"] = max(0.05, min(w["dur"], dur - w["start"]))

        start, end = cursor, cursor + dur
        timeline.append({
            **scene, "audio": str(path), "start": start, "end": end,
            "words": [{"text": w["text"], "start": start + w["start"],
                       "end": start + w["start"] + w["dur"]} for w in words],
        })
        cursor = end + config.LINE_GAP

    for i, seg in enumerate(timeline):  # keep the speaker on screen through the gap
        seg["show_until"] = timeline[i + 1]["start"] if i + 1 < len(timeline) else seg["end"] + config.TAIL_PAD
    (workdir / "timeline.json").write_text(json.dumps(timeline, indent=2))
    return timeline


# ==========================================================================
# 3. ASSETS  (backgrounds via Pollinations.ai, stickman PNGs)
# ==========================================================================
def _cover_resize(img, w: int, h: int):
    from PIL import Image
    scale = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def _gradient_background(name: str, path: Path) -> None:
    from PIL import Image
    top, bot = config.BACKGROUND_PALETTES.get(name, ((160, 175, 205), (55, 60, 85)))
    img = Image.new("RGB", (config.VIDEO_W, config.VIDEO_H))
    px = img.load()
    for y in range(config.VIDEO_H):
        r = y / (config.VIDEO_H - 1)
        c = tuple(int(top[k] + (bot[k] - top[k]) * r) for k in range(3))
        for x in range(config.VIDEO_W):
            px[x, y] = c
    img.save(path)


def ensure_background(name: str) -> Path:
    """Use a committed local background, or create a local gradient fallback."""
    config.BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.BACKGROUNDS_DIR / f"{name}.png"
    if not path.exists():
        log.info("Creating local fallback background '%s'", name)
        _gradient_background(name, path)
    return path


_ACCENTS = [(220, 60, 60), (60, 120, 220), (240, 170, 30), (60, 170, 90), (150, 80, 200)]


def _draw_placeholder(char: str, pose: str, path: Path) -> None:
    """Draw a simple transparent stickman so the pipeline runs before you add art."""
    from PIL import Image, ImageDraw

    W, H, S = 600, 900, 2   # S = supersampling for smooth edges
    img = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    d = Image.new("RGBA", img.size)  # noqa: F841 (kept simple)
    d = ImageDraw.Draw(img)
    P = lambda x, y: (x * S, y * S)
    ink, lw, cx = (20, 20, 20, 255), 16 * S, 300
    accent = _ACCENTS[sum(map(ord, char)) % len(_ACCENTS)] + (255,)

    # body + legs
    d.line([P(cx, 250), P(cx, 520)], fill=ink, width=lw)
    d.line([P(cx, 520), P(210, 800)], fill=ink, width=lw)
    d.line([P(cx, 520), P(390, 800)], fill=ink, width=lw)

    # arms per pose
    arms = {
        "standing": [[(cx, 300), (215, 470)], [(cx, 300), (385, 470)]],
        "talking": [[(cx, 300), (215, 470)], [(cx, 300), (410, 270), (460, 170)]],
        "shocked": [[(cx, 300), (185, 170)], [(cx, 300), (415, 170)]],
        "laughing": [[(cx, 300), (225, 420), (cx, 435)], [(cx, 300), (375, 420), (cx, 435)]],
        "thinking": [[(cx, 300), (215, 470)], [(cx, 300), (400, 380), (335, 238)]],
    }[pose]
    for arm in arms:
        d.line([P(*p) for p in arm], fill=ink, width=lw, joint="curve")

    # head, accent hat band + chest badge (colour is derived from the character name)
    d.ellipse([P(cx - 85, 70), P(cx + 85, 240)], fill=(255, 255, 255, 255), outline=ink, width=lw)
    d.rectangle([P(cx - 62, 62), P(cx + 62, 92)], fill=accent)
    d.ellipse([P(cx - 22, 310), P(cx + 22, 354)], fill=accent)

    # face
    if pose == "shocked":
        for ex in (cx - 32, cx + 32):
            d.ellipse([P(ex - 18, 122), P(ex + 18, 158)], fill=(255, 255, 255, 255), outline=ink, width=4 * S)
            d.ellipse([P(ex - 6, 134), P(ex + 6, 146)], fill=ink)
        d.ellipse([P(cx - 20, 175), P(cx + 20, 225)], fill=ink)
    elif pose == "laughing":
        for ex in (cx - 32, cx + 32):
            d.arc([P(ex - 16, 128), P(ex + 16, 156)], 200, 340, fill=ink, width=5 * S)
        d.pieslice([P(cx - 36, 170), P(cx + 36, 225)], 0, 180, fill=ink)
    else:
        for ex in (cx - 30, cx + 30):
            d.ellipse([P(ex - 9, 132), P(ex + 9, 150)], fill=ink)
        if pose == "talking":
            d.ellipse([P(cx - 22, 175), P(cx + 22, 212)], fill=ink)
        elif pose == "thinking":
            d.line([P(cx - 8, 195), P(cx + 28, 190)], fill=ink, width=6 * S)
            d.line([P(cx + 10, 112), P(cx + 48, 122)], fill=ink, width=6 * S)
        else:
            d.line([P(cx - 24, 192), P(cx + 24, 192)], fill=ink, width=6 * S)

    img.resize((W, H), Image.LANCZOS).save(path)


def prepare_character_image(char: str, pose: str, workdir: Path) -> tuple[Path, int, int]:
    """
    Resolve assets/characters/<char>_<pose>.png (falls back to <char>_standing.png,
    then to an auto-drawn placeholder), scale it to fit, and cache it in workdir.
    All poses of a character should share one canvas size so the figure never "jumps".
    """
    from PIL import Image

    config.CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    src = config.CHARACTERS_DIR / f"{char}_{pose}.png"
    if not src.exists():
        fallback = config.CHARACTERS_DIR / f"{char}_standing.png"
        if fallback.exists():
            log.warning("Missing %s -> using %s", src.name, fallback.name)
            src = fallback
        else:
            log.warning("Missing %s -> drawing placeholder", src.name)
            tmp = workdir / "cache"          # keep placeholders out of assets/ so
            tmp.mkdir(parents=True, exist_ok=True)  # they never shadow real art later
            src = tmp / f"placeholder_{char}_{pose}.png"
            _draw_placeholder(char, pose, src)

    img = Image.open(src).convert("RGBA")
    scale = min(config.CHAR_MAX_W / img.width, config.CHAR_MAX_H / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    cache = workdir / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / f"{src.stem}.png"
    img.save(out)
    return out, img.width, img.height


# ==========================================================================
# 4. VIDEO COMPOSITING  (MoviePy layers 1+2, FFmpeg captions layer 3)
# ==========================================================================
def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ass_clean(text: str) -> str:
    return re.sub(r"[{}\\\n\r]", "", text)


def build_ass(timeline: list[dict], title: str, total: float, path: Path) -> None:
    """Karaoke subtitles: each word fills white -> yellow as it is spoken."""
    # ASS colours are &HAABBGGRR. Primary (yellow) = already spoken, Secondary (white) = upcoming.
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {config.VIDEO_W}
PlayResY: {config.VIDEO_H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{config.FONT_NAME},{config.CAPTION_FONT_SIZE},&H0000FFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,2,60,60,{config.CAPTION_MARGIN_V},1
Style: Title,{config.FONT_NAME},{config.TITLE_FONT_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,8,70,70,{config.TITLE_MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if config.SHOW_TITLE and title:
        lines.append(f"Dialogue: 0,{_ass_time(0)},{_ass_time(total)},Title,,0,0,0,,{_ass_clean(title.upper())}")

    n = config.CAPTION_WORDS_PER_CHUNK
    for seg in timeline:
        words = [w for w in seg["words"] if _ass_clean(w["text"]).strip()]
        chunks = [words[i:i + n] for i in range(0, len(words), n)]
        for ci, chunk in enumerate(chunks):
            c_start = chunk[0]["start"]
            c_end = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else seg["end"]
            c_end = max(c_end, chunk[-1]["end"])
            parts = []
            for wi, w in enumerate(chunk):
                nxt = chunk[wi + 1]["start"] if wi + 1 < len(chunk) else c_end
                k = max(1, int(round((nxt - w["start"]) * 100)))   # centiseconds
                parts.append(f"{{\\k{k}}}{_ass_clean(w['text']).upper()} ")
            lines.append(f"Dialogue: 1,{_ass_time(c_start)},{_ass_time(c_end)},Karaoke,,0,0,0,,{''.join(parts).strip()}")
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def _ffmpeg_bin() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _make_position(x_center: float, w: int, h: int, bounce: bool):
    left, top = x_center - w / 2, config.CHAR_BOTTOM_Y - h
    if bounce:   # active speaker bobs while talking
        return lambda t: (left, top - abs(sin(t * 9.0)) * config.BOUNCE_PX)
    return (left, top)


def composite_video(script: dict, timeline: list[dict], bg_path: Path, out_path: Path, workdir: Path) -> Path:
    """
    Layer 1: background image          (MoviePy ImageClip)
    Layer 2: stickman PNG overlays     (speaker bounces, listener reacts)
    Layer 3: karaoke captions          (FFmpeg `subtitles` filter, burned in)
    """
    from moviepy.editor import (AudioFileClip, CompositeAudioClip, CompositeVideoClip, ImageClip)

    total = timeline[-1]["show_until"]
    cast = script["cast"]

    # ---- Layer 1 --------------------------------------------------------
    layers = [ImageClip(str(bg_path)).set_duration(total)]

    # ---- Layer 2 --------------------------------------------------------
    for seg in timeline:
        speaker = seg["speaker"]
        listener = "B" if speaker == "A" else "A"
        dur = seg["show_until"] - seg["start"]
        for who, pose, active in ((speaker, seg["pose"], True), (listener, seg["reaction"], False)):
            png, w, h = prepare_character_image(cast[who], pose, workdir)
            layers.append(
                ImageClip(str(png))
                .set_start(seg["start"]).set_duration(dur)
                .set_position(_make_position(config.CHAR_X[who], w, h, bounce=active))
            )

    audio_clips = [AudioFileClip(seg["audio"]).set_start(seg["start"]) for seg in timeline]
    final = (CompositeVideoClip(layers, size=(config.VIDEO_W, config.VIDEO_H))
             .set_audio(CompositeAudioClip(audio_clips)).set_duration(total))

    raw_path = workdir / "no_captions.mp4"
    log.info("Rendering layers 1+2 with MoviePy (%.1fs)", total)
    final.write_videofile(str(raw_path), fps=config.FPS, codec="libx264", audio_codec="aac",
                          preset="ultrafast", threads=2, logger=None,
                          ffmpeg_params=["-crf", "18", "-pix_fmt", "yuv420p"])
    final.close()
    for c in audio_clips:
        c.close()

    # ---- Layer 3 --------------------------------------------------------
    ass_path = workdir / "captions.ass"
    build_ass(timeline, script["title"], total, ass_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("Burning karaoke captions with FFmpeg")
    cmd = [_ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(raw_path.resolve()),
           "-vf", f"subtitles={ass_path.name}",   # relative name + cwd avoids path-escaping bugs
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "copy", "-movflags", "+faststart", str(out_path.resolve())]
    subprocess.run(cmd, cwd=workdir, check=True)
    return out_path


# ==========================================================================
# ORCHESTRATION
# ==========================================================================
def prefetch_assets() -> None:
    """Pre-create local placeholder stickmen and local fallback backgrounds."""
    config.CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    for fmt in config.FORMATS.values():
        for char in fmt["cast"].values():
            for pose in config.ALLOWED_POSES:
                p = config.CHARACTERS_DIR / f"{char}_{pose}.png"
                if not p.exists():
                    _draw_placeholder(char, pose, p)
        ensure_background(fmt["background"])
    log.info("Assets ready in %s", config.ASSETS_DIR)


def run(category: str, topic: str | None, mode: str = "short", aspect: str = "auto", minutes: float | None = None) -> Path:
    config.apply_runtime_profile(mode=mode, aspect=aspect, minutes=minutes)
    if category == "random":
        category = random.choice(list(config.FORMATS))
    stamp = time.strftime("%Y%m%d_%H%M%S")
    workdir = config.WORK_DIR / stamp
    workdir.mkdir(parents=True, exist_ok=True)

    script = generate_script_json(category, topic)
    (workdir / "script.json").write_text(json.dumps(script, indent=2))
    log.info("Script: '%s' (%d scenes)", script["title"], len(script["scenes"]))

    timeline = generate_dialogue_audio(script, workdir)
    bg = ensure_background(script["background"])

    out = config.OUTPUT_DIR / f"stickman_{category}_{stamp}.mp4"
    composite_video(script, timeline, bg, out, workdir)

    meta = {"video": out.name, "title": script["title"], "description": script["description"],
            "hashtags": script["hashtags"], "category": category, "duration": timeline[-1]["show_until"],
            "mode": config.MODE, "aspect": config.ASPECT, "target_minutes": config.TARGET_MINUTES,
            "resolution": [config.VIDEO_W, config.VIDEO_H]}
    (config.OUTPUT_DIR / "latest.json").write_text(json.dumps(meta, indent=2))
    (config.OUTPUT_DIR / f"{out.stem}.json").write_text(json.dumps(meta, indent=2))
    log.info("Done: %s", out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Stickman short-video generator")
    ap.add_argument("--category", default="random", choices=[*config.FORMATS, "random"])
    ap.add_argument("--topic", default=None, help="override the random topic seed")
    ap.add_argument("--mode", choices=["short", "long"], default="short",
                    help="short defaults to portrait ~40s; long defaults to landscape ~3min")
    ap.add_argument("--aspect", choices=["auto", "portrait", "landscape"], default="auto")
    ap.add_argument("--minutes", type=float, default=None,
                    help="target spoken duration in minutes (0.35 to 8; default depends on mode)")
    ap.add_argument("--prefetch-assets", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.prefetch_assets:
        prefetch_assets()
        return
    try:
        run(args.category, args.topic, args.mode, args.aspect, args.minutes)
    except Exception:
        log.exception("Pipeline failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
