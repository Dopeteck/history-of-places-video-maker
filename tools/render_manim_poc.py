"""Render, mux, verify, and create a contact sheet for the Manim POC."""
from __future__ import annotations

import argparse
import json
import math
import shutil
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str], timeout: int = 1200) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Command timed out after {timeout}s: {' '.join(command)}") from exc
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stdout[-5000:]}"
        )
    return result


def _probe(path: Path) -> dict[str, Any]:
    raw = _run([
        "ffprobe", "-v", "error", "-show_entries",
        "stream=codec_type,width,height,r_frame_rate,codec_name:format=duration",
        "-of", "json", str(path),
    ], timeout=45).stdout
    data = json.loads(raw)
    video = next((item for item in data.get("streams", [])
                  if item.get("codec_type") == "video"), None)
    audio = next((item for item in data.get("streams", [])
                  if item.get("codec_type") == "audio"), None)
    if not video:
        raise RuntimeError(f"No video stream found in {path}")
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": video["r_frame_rate"],
        "video_codec": video.get("codec_name"),
        "has_audio": audio is not None,
        "duration_seconds": float(data["format"]["duration"]),
    }


def _write_synthetic_audio(path: Path, duration: float, sample_rate: int = 24000) -> None:
    """Quiet, short transition ticks only; deliberately not represented as narration."""
    transitions = (1.25, 3.45, 5.00, 6.25, 7.43, 8.63)
    count = round(duration * sample_rate)
    samples = [0] * count
    tone_len = round(0.055 * sample_rate)
    for start_s in transitions:
        start = round(start_s * sample_rate)
        if start >= count:
            continue
        for index in range(min(tone_len, count - start)):
            fade_in = min(1.0, index / 140)
            fade_out = min(1.0, (tone_len - index) / 500)
            envelope = max(0.0, min(fade_in, fade_out))
            sample = int(2100 * envelope * math.sin(2 * math.pi * 880 * index / sample_rate))
            samples[start + index] = sample
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))


def _contact_sheet(video: Path, output: Path, duration: float) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    cell_w, cell_h = 270, 500
    sheet = Image.new("RGB", (cell_w * 3, cell_h * 2), (237, 234, 226))
    draw = ImageDraw.Draw(sheet)
    font_candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    font_path = next((path for path in font_candidates if path.exists()), None)
    font = ImageFont.truetype(str(font_path), 14) if font_path else ImageFont.load_default()
    # Sample shots after their entrances/FX have settled, especially the steam card.
    times = [min(value, duration - 0.25) for value in (0.82, 2.45, 4.08, 5.72, 7.90, 9.20)]
    for index, seconds in enumerate(times):
        frame_path = output.parent / f".manim-poc-frame-{index}.png"
        _run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{seconds:.3f}", "-i", str(video), "-frames:v", "1",
            str(frame_path),
        ], timeout=30)
        with Image.open(frame_path) as source:
            thumb = source.convert("RGB")
            thumb.thumbnail((cell_w - 10, cell_h - 35), Image.Resampling.LANCZOS)
        x = (index % 3) * cell_w + (cell_w - thumb.width) // 2
        y = (index // 3) * cell_h + 5
        sheet.paste(thumb, (x, y))
        draw.text(((index % 3) * cell_w + 8, (index // 3 + 1) * cell_h - 25),
                  f"{seconds:.2f}s", font=font, fill=(24, 24, 24))
        frame_path.unlink(missing_ok=True)
    sheet.save(output, "JPEG", quality=82, optimize=True)
    return output


def render(output: Path, width: int = 1080, height: int = 1920,
           fps: int = 30, audio: str = "synthetic") -> dict[str, Any]:
    if width != 1080 or height != 1920:
        raise ValueError("The initial comparison benchmark is fixed at 1080x1920.")
    if fps != 30:
        raise ValueError("The initial comparison benchmark is fixed at 30 FPS.")
    if audio not in {"synthetic", "none"}:
        raise ValueError("audio must be 'synthetic' or 'none'")
    for executable in ("ffmpeg", "ffprobe"):
        if not shutil.which(executable):
            raise RuntimeError(f"{executable} is required on PATH.")

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    media_root = output.parent / ".manim-poc-work"
    if media_root.exists():
        shutil.rmtree(media_root)
    media_root.mkdir(parents=True)
    raw_expected = "manim_poc_raw.mp4"
    cmd = [
        sys.executable, "-m", "manim", "render",
        "--renderer=cairo",
        "--resolution", f"{width},{height}",
        "--fps", str(fps),
        "--media_dir", str(media_root / "media"),
        "--disable_caching",
        "--verbosity", "warning",
        "-o", "manim_poc_raw",
        str(ROOT / "manim_poc.py"),
        "ManimHybridProof",
    ]
    started = time.monotonic()
    result = _run(cmd, timeout=1200)
    render_seconds = time.monotonic() - started
    matches = list((media_root / "media").rglob(raw_expected))
    if not matches:
        candidates = [
            path for path in (media_root / "media").rglob("*.mp4")
            if "partial_movie_files" not in path.parts
        ]
        if len(candidates) != 1:
            raise RuntimeError(
                "Could not uniquely identify the Manim output. "
                f"Found: {[str(path) for path in candidates]}\n{result.stdout[-3000:]}"
            )
        raw_video = candidates[0]
    else:
        raw_video = matches[0]

    raw_meta = _probe(raw_video)
    if (raw_meta["width"], raw_meta["height"], raw_meta["fps"]) != (width, height, "30/1"):
        raise RuntimeError(f"Manim raw output has unexpected format: {raw_meta}")

    audio_path = media_root / "synthetic_ticks.wav"
    mux_started = time.monotonic()
    if audio == "synthetic":
        _write_synthetic_audio(audio_path, raw_meta["duration_seconds"])
        mux = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(raw_video), "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "64k",
            "-shortest", "-movflags", "+faststart", str(output),
        ]
    else:
        mux = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(raw_video), "-map", "0:v:0", "-an",
            "-c:v", "copy", "-movflags", "+faststart", str(output),
        ]
    _run(mux, timeout=180)
    mux_seconds = time.monotonic() - mux_started
    final_meta = _probe(output)
    if (final_meta["width"], final_meta["height"], final_meta["fps"]) != (width, height, "30/1"):
        raise RuntimeError(f"Final MP4 has unexpected format: {final_meta}")
    if audio == "synthetic" and not final_meta["has_audio"]:
        raise RuntimeError("Synthetic audio mux requested but no audio stream was found.")

    sheet = _contact_sheet(output, output.with_name(output.stem + ".contact-sheet.jpg"),
                           final_meta["duration_seconds"])
    report = {
        "renderer": "Manim Community Edition / Cairo",
        "manim_poc_only": True,
        "production_renderer_changed": False,
        "output": str(output),
        **final_meta,
        "frame_count_estimate": round(final_meta["duration_seconds"] * fps),
        "manim_render_seconds": round(render_seconds, 3),
        "mux_seconds": round(mux_seconds, 3),
        "total_wall_seconds": round(time.monotonic() - started, 3),
        "audio": "synthetic transition ticks, not narration" if audio == "synthetic" else "none",
        "contact_sheet": str(sheet),
        "raw_manim_video": str(raw_video),
        "validation": "passed",
    }
    output.with_suffix(".report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/manim_poc.mp4"))
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--audio", choices=("synthetic", "none"), default="synthetic")
    args = parser.parse_args(argv)
    try:
        report = render(args.output, args.width, args.height, args.fps, args.audio)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Manim POC failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
