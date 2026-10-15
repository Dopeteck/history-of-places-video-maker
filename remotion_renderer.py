"""Render an approved story with the MIT-licensed, keyframed Remotion stickman engine.

This adapter does not generate or approve scripts and never calls a paid media service.
The existing story review/approval gate remains in workflow_run.py.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import struct
import subprocess
import time
import wave
from pathlib import Path
from typing import Any

from captions import word_timings
from story import validate_chapter
from tts import Narrator, RATE

ROOT = Path(__file__).resolve().parent
REMOTION_ROOT = ROOT / "remotion-stickman"
PLAN_PATH = REMOTION_ROOT / "src" / "generated" / "plan.json"
PUBLIC_ROOT = REMOTION_ROOT / "public" / "story"
FPS = 24
TITLE_SECONDS = 2.5
SAMPLE_RATE = RATE


def _run(command: list[str], cwd: Path, timeout: int = 1800) -> str:
    try:
        result = subprocess.run(command, cwd=cwd, text=True, capture_output=True,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Command timed out after {timeout}s: {command[0]}") from exc
    if result.returncode:
        details = (result.stdout + "\n" + result.stderr)[-6000:]
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}\n{details}")
    return result.stdout.strip()


def _write_pcm(path: Path, samples: list[float], sample_rate: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(b"".join(
            struct.pack("<h", max(-32768, min(32767, round(float(x) * 32767))))
            for x in samples
        ))


def _silence_frames(out: wave.Wave_write, frame_count: int) -> None:
    remaining = max(0, int(frame_count))
    block = b"\x00\x00" * min(24000, remaining)
    while remaining:
        take = min(24000, remaining)
        out.writeframesraw(block[:take * 2])
        remaining -= take


def _make_tone(path: Path, seconds: float, frequency: float = 220.0) -> None:
    count = max(1, round(seconds * SAMPLE_RATE))
    samples = [
        0.025 * math.sin(2 * math.pi * frequency * i / SAMPLE_RATE)
        * min(1.0, i / 480.0)
        * min(1.0, (count - i) / 480.0)
        for i in range(count)
    ]
    _write_pcm(path, samples)


def _make_effect(path: Path, kind: str, seconds: float = 0.45) -> None:
    """Synthesize short, royalty-free effects from oscillators/noise; no downloaded samples."""
    rng = random.Random("stickman-remotion-" + kind)
    count = max(1, round(seconds * SAMPLE_RATE))
    samples: list[float] = []
    for i in range(count):
        t = i / SAMPLE_RATE
        p = i / count
        if kind == "impact":
            envelope = math.exp(-8.0 * p)
            sample = 0.62 * math.sin(2 * math.pi * (110 - 35*p) * t) + 0.26 * rng.uniform(-1, 1)
        elif kind == "whoosh":
            envelope = math.sin(math.pi * p) ** 1.4
            sample = 0.6 * rng.uniform(-1, 1) + 0.2 * math.sin(2 * math.pi * (120 + 280*p) * t)
        elif kind == "pop":
            envelope = math.exp(-12.0 * p)
            sample = 0.56 * math.sin(2 * math.pi * (680 - 330*p) * t)
        elif kind == "step":
            envelope = math.exp(-9.0 * p)
            sample = 0.28 * math.sin(2 * math.pi * 85 * t) + 0.12 * rng.uniform(-1, 1)
        else:  # chime
            envelope = math.exp(-3.0 * p)
            sample = 0.38 * math.sin(2 * math.pi * 660 * t) + 0.20 * math.sin(2 * math.pi * 990 * t)
        samples.append(max(-0.8, min(0.8, sample * envelope)))
    _write_pcm(path, samples)


def _make_music_loop(path: Path, seconds: float = 12.0) -> None:
    """A soft original harmonic bed; generated locally and deliberately kept under speech."""
    count = round(seconds * SAMPLE_RATE)
    notes = (110.0, 164.81, 220.0, 261.63)
    samples: list[float] = []
    for i in range(count):
        t = i / SAMPLE_RATE
        swell = 0.55 + 0.45 * math.sin(2 * math.pi * t / seconds) ** 2
        tone = sum(math.sin(2 * math.pi * hz * t) for hz in notes) / len(notes)
        top = 0.14 * math.sin(2 * math.pi * 392.0 * t) * (0.5 + 0.5 * math.sin(2 * math.pi * t / 4.0))
        samples.append((tone * 0.09 + top * 0.25) * swell)
    _write_pcm(path, samples)


def _ensure_audio_assets() -> None:
    effects = PUBLIC_ROOT / "sfx"
    effects.mkdir(parents=True, exist_ok=True)
    for name, duration in (("impact", 0.45), ("whoosh", 0.55), ("pop", 0.22),
                           ("step", 0.22), ("chime", 0.7)):
        path = effects / f"{name}.wav"
        if not path.exists():
            _make_effect(path, name, duration)
    music = PUBLIC_ROOT / "music.wav"
    if not music.exists():
        _make_music_loop(music)


def _scene_headline(scene: dict) -> str:
    value = scene.get("visual_title") or scene.get("title")
    if isinstance(value, str) and value.strip():
        return value.strip()[:60]
    emphases = scene.get("emphasis_words") or []
    if emphases:
        return str(emphases[0])[:60]
    text = str(scene.get("narration", "")).split()
    return " ".join(text[:5])[:60] or "The Story Continues"


def _make_fallback_beats(scene: dict) -> list[dict]:
    """Compatibility for approved drafts made before action_beats existed."""
    narration = str(scene.get("narration", "")).casefold()
    actors = scene.get("characters_on_screen") or []
    pose_map = {
        "standing": "idle", "talking": "talk", "pointing": "point", "shocked": "shock",
        "running": "run", "kneeling": "kneel", "fighting": "punch", "falling": "fall",
        "dead": "dead", "laughing": "laugh", "thinking": "think", "praying": "kneel",
    }
    if any(word in narration for word in ("chase", "running", "ran ", "flee", "escape", "rushed", "hurried")):
        middle, ending = "run", "point"
    elif any(word in narration for word in ("attack", "fought", "fight", "struck", "battle", "punch")):
        middle, ending = "punch", "shock"
    elif any(word in narration for word in ("found", "discover", "revealed", "noticed", "uncovered")):
        middle, ending = "point", "cheer"
    elif any(word in narration for word in ("search", "searched", "examined", "checked", "looked")):
        middle, ending = "crouch", "think"
    else:
        middle, ending = "talk", "point"
    beats = []
    for at, action in ((0.0, None), (0.4, middle), (0.78, ending)):
        states = []
        for index, actor in enumerate(actors):
            initial = pose_map.get(actor.get("pose", "standing"), "idle")
            selected = initial if action is None else (action if index == 0 else "shock" if action in {"run", "punch", "cheer"} else "think")
            x = float(actor.get("x", 0.3 if index == 0 else 0.7))
            if action == "run" and index == 0:
                x = min(0.9, max(0.1, x + (0.13 if x < 0.5 else -0.13)))
            emotion = actor.get("expression", "neutral")
            if selected in {"cheer", "laugh"}:
                emotion = "happy"
            elif selected in {"shock", "fall"}:
                emotion = "shocked"
            states.append({"id": actor["id"], "action": selected, "x": x, "emotion": emotion})
        if states:
            beats.append({"at": at, "actions": states})
    return beats


def _normalise_story(story: dict) -> dict:
    outline = story.get("outline") or {}
    cast = outline.get("cast") or []
    cast_map = {str(c.get("id")): str(c.get("name", c.get("id"))) for c in cast}
    source_ids = {str(source.get("id")) for source in story.get("sources", [])}
    chapters = []
    seen_cast_ids: set[str] = set()
    for number, chapter in enumerate(story.get("chapters", []), 1):
        normal = validate_chapter(
            chapter, cast_map, source_ids, number, 1, 10000, minimum_scenes=1
        )
        normal["title"] = str(chapter.get("title") or f"Chapter {number}")
        for scene in normal["scenes"]:
            scene["visual_title"] = _scene_headline(scene)
            visible_ids = {
                str(actor.get("id")) for actor in scene.get("characters_on_screen", [])
                if isinstance(actor, dict) and actor.get("id")
            }
            # Names appear only when a character is first introduced; repeating floating
            # labels on every shot made the frame noisy and hid facial acting.
            scene["show_character_labels"] = bool(visible_ids - seen_cast_ids)
            seen_cast_ids.update(visible_ids)
            if not scene.get("action_beats"):
                scene["action_beats"] = _make_fallback_beats(scene)
        chapters.append(normal)
    if not chapters:
        raise ValueError("Approved story contains no chapters to animate")
    return {
        "title": str(outline.get("title") or story.get("topic") or "Stickman Story"),
        "hook": str(outline.get("hook") or "A story brought to life"),
        "mode": story.get("mode", "long"),
        "cast": cast,
        "chapters": chapters,
        "sources": story.get("sources", []),
    }


def _caption_cues(words: list[dict], start_frame: int, duration_frames: int, fps: int) -> list[dict]:
    result = []
    groups: list[list[dict]] = []
    current: list[dict] = []
    char_count = 0
    for word in words:
        token = str(word.get("word", "")).strip()
        if not token:
            continue
        if current and (len(current) >= 5 or char_count + len(token) + 1 > 34):
            groups.append(current)
            current, char_count = [], 0
        current.append(word)
        char_count += len(token) + 1
    if current:
        groups.append(current)
    for group in groups:
        start = start_frame + max(0, math.floor(float(group[0]["start"]) * fps))
        end = start_frame + min(duration_frames, max(1, math.ceil(float(group[-1]["end"]) * fps)))
        if end <= start:
            end = start + 1
        result.append({
            "startFrame": start,
            "endFrame": min(start_frame + duration_frames, end),
            "text": " ".join(str(item["word"]) for item in group),
        })
    return result


def _make_scene_sfx(scene: dict, duration_frames: int) -> list[dict]:
    effects = set(scene.get("effects") or [])
    cues = []
    if "pop_in" in effects and duration_frames > 10:
        cues.append({"frame": 6, "name": "pop", "volume": 0.35})
    if "shake" in effects and duration_frames > 20:
        cues.append({"frame": min(duration_frames - 1, round(duration_frames * 0.53)),
                     "name": "impact", "volume": 0.32})
    if scene.get("camera") in {"push_in", "pan_left", "pan_right"} and duration_frames > 30:
        cues.append({"frame": 1, "name": "whoosh", "volume": 0.12})
    return cues


def _write_plan_and_voice(normal: dict, output: Path, voice_name: str, speed: float,
                          preview: bool, smoke: bool, captions: bool) -> dict:
    width, height = ((1080, 1920) if normal["mode"] == "shorts" else (1920, 1080))
    if preview:
        width, height = width // 2, height // 2
    fps = FPS
    title_frames = round(TITLE_SECONDS * fps)
    cast = normal["cast"]
    scene_audio: list[dict] = []
    narrator = None if smoke else Narrator(voice_name, speed)
    audio_cache = output / "audio-cache"
    cursor = 0
    chapters_out = []
    cue_rows = []

    for chapter in normal["chapters"]:
        chapter_start = cursor
        cursor += title_frames
        scenes_out = []
        for scene in chapter["scenes"]:
            if smoke:
                seconds = 3.8 + (len(scene_audio) % 3) * 0.55
                audio_path = output / "smoke-audio" / f"{scene['id']}.wav"
                _make_tone(audio_path, seconds, 196.0 + (len(scene_audio) % 4) * 55.0)
                audio = {"path": str(audio_path), "duration": seconds,
                         "words": word_timings(scene["narration"], seconds),
                         "alignment": "synthetic_test"}
            else:
                audio = narrator.synthesize(scene["narration"], audio_cache)
            duration_frames = max(1, math.ceil(float(audio["duration"]) * fps))
            scene_plan = {
                "id": scene["id"],
                "title": scene.get("visual_title") or _scene_headline(scene),
                "start_frame": cursor,
                "duration_frames": duration_frames,
                "narration": scene["narration"],
                "background": scene.get("background", "room"),
                "camera": scene.get("camera", "static"),
                "effects": scene.get("effects", []),
                "props": scene.get("props", []),
                "emphasis_words": scene.get("emphasis_words", []),
                "characters_on_screen": scene.get("characters_on_screen", []),
                "action_beats": scene.get("action_beats") or _make_fallback_beats(scene),
                "show_character_labels": scene.get("show_character_labels", False) is True,
                "sfx_cues": _make_scene_sfx(scene, duration_frames),
            }
            audio_info = {**audio, "duration_frames": duration_frames,
                          "start_frame": cursor, "scene": scene_plan}
            scene_audio.append(audio_info)
            if captions or normal["mode"] == "shorts":
                cue_rows.extend(_caption_cues(audio.get("words", []), cursor, duration_frames, fps))
            scenes_out.append(scene_plan)
            cursor += duration_frames
        chapters_out.append({
            "number": chapter["number"],
            "title": chapter["title"],
            "start_frame": chapter_start,
            "title_frames": title_frames,
            "scenes": scenes_out,
        })

    plan = {
        "schema_version": 1,
        "title": normal["title"],
        "hook": normal["hook"],
        "mode": normal["mode"],
        "width": width,
        "height": height,
        "fps": fps,
        "total_frames": cursor,
        "cast": cast,
        "chapters": chapters_out,
        "captions": cue_rows,
    }
    PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAN_PATH.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    PUBLIC_ROOT.mkdir(parents=True, exist_ok=True)
    voice_path = PUBLIC_ROOT / "voice.wav"
    with wave.open(str(voice_path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        for chapter in chapters_out:
            _silence_frames(out, round(chapter["title_frames"] / fps * SAMPLE_RATE))
            for scene in chapter["scenes"]:
                row = next(item for item in scene_audio if item["scene"]["id"] == scene["id"])
                target_samples = round(scene["duration_frames"] / fps * SAMPLE_RATE)
                with wave.open(str(row["path"]), "rb") as src:
                    if (src.getnchannels(), src.getsampwidth(), src.getframerate()) != (1, 2, SAMPLE_RATE):
                        raise ValueError(f"Unexpected narration format in {row['path']}")
                    pcm = src.readframes(src.getnframes())
                actual_samples = len(pcm) // 2
                if actual_samples > target_samples:
                    pcm = pcm[:target_samples * 2]
                    actual_samples = target_samples
                out.writeframesraw(pcm)
                _silence_frames(out, target_samples - actual_samples)

    _ensure_audio_assets()
    return plan


def render_story(story: dict, output: Path, voice_name: str = "bm_george", speed: float = 1.0,
                 preview: bool = False, captions: bool = False, smoke: bool = False) -> Path:
    """Render an approved story to a Remotion MP4 and a compatible render-metadata.json."""
    if not shutil.which("node") or not (shutil.which("npm") or shutil.which("npm.cmd")):
        raise RuntimeError("Node.js and npm are required for the Remotion renderer")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and ffprobe are required for the Remotion renderer")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    normal = _normalise_story(story)
    plan = _write_plan_and_voice(normal, output, voice_name, speed, preview, smoke, captions)

    if not (REMOTION_ROOT / "node_modules").exists():
        _run(["npm", "install", "--no-audit", "--no-fund"], REMOTION_ROOT, timeout=900)
    _run(["npm", "run", "typecheck"], REMOTION_ROOT, timeout=300)
    video = output / "video.mp4"
    _run(["npx", "remotion", "render", "src/index.ts", "ChoreographedStory",
          str(video), "--log=error"], REMOTION_ROOT, timeout=1800)
    if not video.is_file() or video.stat().st_size < 10_000:
        raise RuntimeError("Remotion returned without a usable MP4")

    probe = _run(["ffprobe", "-v", "error", "-show_entries",
                  "stream=codec_type,width,height,r_frame_rate,codec_name:format=duration",
                  "-of", "json", str(video)], ROOT, timeout=45)
    media = json.loads(probe)
    video_stream = next((s for s in media.get("streams", []) if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in media.get("streams", []) if s.get("codec_type") == "audio"), None)
    if video_stream is None or audio_stream is None:
        raise RuntimeError("Rendered MP4 must contain both video and audio streams")
    expected = (plan["width"], plan["height"])
    actual = (int(video_stream["width"]), int(video_stream["height"]))
    if actual != expected:
        raise RuntimeError(f"Render resolution mismatch: expected {expected}, got {actual}")
    duration = float(media["format"]["duration"])
    expected_duration = plan["total_frames"] / plan["fps"]
    if abs(duration - expected_duration) > max(0.35, 2 / plan["fps"]):
        raise RuntimeError(f"Render duration mismatch: expected {expected_duration:.2f}s, got {duration:.2f}s")

    chapter_stamps = []
    for chapter in plan["chapters"]:
        chapter_stamps.append({
            "seconds": round(chapter["start_frame"] / plan["fps"], 3),
            "title": chapter["title"],
            "number": chapter["number"],
        })
    in_target = (60 <= duration <= 90) if plan["mode"] == "shorts" else (480 <= duration <= 900)
    meta = {
        "title": plan["title"],
        "duration_seconds": duration,
        "chapters": chapter_stamps,
        "voice": "synthetic tone; no speech" if smoke else voice_name,
        "word_alignment": "synthetic_test" if smoke else "estimated from measured Kokoro WAV durations",
        "resolution": [plan["width"], plan["height"]],
        "fps": plan["fps"],
        "render_wall_seconds": round(time.monotonic() - started, 2),
        "render_seconds_per_video_second": round((time.monotonic() - started) / max(duration, 0.001), 3),
        "duration_in_target": in_target if not smoke else None,
        "renderer": "Remotion + MIT-licensed nispa stickman engine",
        "paid_media_tools": False,
        "sources": normal.get("sources", []),
        "disclosure": ("Offline visual preview with synthetic tone audio, not narration." if smoke else
                       "Narrated with local Kokoro TTS; stickman movement, props, sound effects and background audio are procedurally rendered."),
    }
    (output / "render-metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                                                  encoding="utf-8")
    description = [meta["title"], "", meta["disclosure"], "", "Sources"]
    description.extend(f"{item.get('title','Source')} — {item.get('url','')}" for item in normal.get("sources", []))
    (output / "description.txt").write_text("\n".join(description).rstrip() + "\n", encoding="utf-8")
    print(f"Rendered Remotion video: {video} ({duration:.2f}s, {plan['width']}x{plan['height']} at {plan['fps']} FPS)")
    return video


def _smoke_story() -> dict:
    """A tiny fixture that exercises distinct actions and needs no LLM/API/model download."""
    return {
        "mode": "long",
        "topic": "The Missing Crown",
        "outline": {
            "title": "The Missing Crown",
            "hook": "ONE SMALL MISTAKE CHANGED EVERYTHING",
            "cast": [
                {"id": "ruler", "name": "The Ruler", "role": "ruler", "trait": "proud",
                 "accessory": "none", "accent": "#E0A526"},
                {"id": "guard", "name": "The Guard", "role": "guard", "trait": "alert",
                 "accessory": "none", "accent": "#1FA2A6"},
            ],
        },
        "sources": [],
        "chapters": [
            {"number": 1, "title": "The Missing Crown", "scenes": [
                {"narration": "The guard ran into the throne room and pointed at the empty chair.",
                 "visual_title": "The Throne Is Empty",
                 "characters_on_screen": [
                    {"id": "guard", "x": 0.24, "pose": "running", "expression": "shocked"},
                    {"id": "ruler", "x": 0.73, "pose": "thinking", "expression": "neutral"}],
                 "action_beats": [
                    {"at": 0.0, "actions": [{"id": "guard", "action": "run", "x": 0.12, "emotion": "shocked"},
                                            {"id": "ruler", "action": "think", "x": 0.73, "emotion": "neutral"}]},
                    {"at": 0.42, "actions": [{"id": "guard", "action": "point", "x": 0.32, "emotion": "shocked"},
                                             {"id": "ruler", "action": "shock", "x": 0.73, "emotion": "shocked"}]},
                    {"at": 0.78, "actions": [{"id": "guard", "action": "point", "x": 0.32, "emotion": "shocked"},
                                             {"id": "ruler", "action": "facepalm", "x": 0.73, "emotion": "sad"}]}],
                 "background": "throne", "camera": "push_in", "effects": ["pop_in", "shake"],
                 "props": [], "emphasis_words": ["empty chair"], "source_ids": [], "uncertainty": "unverified"},
                {"narration": "They searched the chamber, first checking the chest and then looking beneath the table.",
                 "visual_title": "Search the Chamber",
                 "characters_on_screen": [
                    {"id": "guard", "x": 0.22, "pose": "thinking", "expression": "neutral"},
                    {"id": "ruler", "x": 0.36, "pose": "pointing", "expression": "angry"}],
                 "action_beats": [
                    {"at": 0.0, "actions": [{"id": "guard", "action": "walk", "x": 0.22, "emotion": "neutral"},
                                            {"id": "ruler", "action": "point", "x": 0.36, "emotion": "angry"}]},
                    {"at": 0.46, "actions": [{"id": "guard", "action": "crouch", "x": 0.76, "emotion": "neutral"},
                                             {"id": "ruler", "action": "think", "x": 0.36, "emotion": "neutral"}]},
                    {"at": 0.80, "actions": [{"id": "guard", "action": "crouch", "x": 0.50, "emotion": "neutral"},
                                             {"id": "ruler", "action": "point", "x": 0.70, "emotion": "neutral"}]}],
                 "background": "palace", "camera": "pan_right", "effects": ["cut"],
                 "props": ["table", "chest"], "emphasis_words": ["searched the chamber"],
                 "source_ids": [], "uncertainty": "unverified"},
                {"narration": "A flash of gold beneath the table revealed the missing crown. The guard lifted it into view, and the ruler cheered.",
                 "visual_title": "The Crown Is Found",
                 "characters_on_screen": [
                    {"id": "guard", "x": 0.50, "pose": "kneeling", "expression": "happy"},
                    {"id": "ruler", "x": 0.71, "pose": "shocked", "expression": "shocked"}],
                 "action_beats": [
                    {"at": 0.0, "actions": [{"id": "guard", "action": "crouch", "x": 0.50, "emotion": "neutral"},
                                            {"id": "ruler", "action": "think", "x": 0.71, "emotion": "neutral"}]},
                    {"at": 0.45, "actions": [{"id": "guard", "action": "point", "x": 0.50, "emotion": "happy"},
                                             {"id": "ruler", "action": "shock", "x": 0.71, "emotion": "shocked"}]},
                    {"at": 0.79, "actions": [{"id": "guard", "action": "hold", "x": 0.50, "emotion": "happy"},
                                             {"id": "ruler", "action": "cheer", "x": 0.71, "emotion": "happy"}]}],
                 "background": "room", "camera": "push_in", "effects": ["fade"],
                 "props": ["crown", "table"], "emphasis_words": ["missing crown"], "source_ids": [], "uncertainty": "unverified"},
            ],
        }
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-test", action="store_true", help="Render an offline choreography fixture using tones instead of TTS")
    parser.add_argument("--out", type=Path, default=ROOT / "output" / "remotion-preview")
    parser.add_argument("--voice", choices=("bm_george", "bm_fable", "am_michael"), default="bm_george")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--captions", action="store_true")
    args = parser.parse_args()
    if not args.smoke_test:
        parser.error("The command-line entry point only supports --smoke-test; approved stories call render_story() from workflow_run.py.")
    render_story(_smoke_story(), args.out, args.voice, args.speed, args.preview, args.captions, smoke=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
