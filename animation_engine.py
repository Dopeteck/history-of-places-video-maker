"""Offline, deterministic compositor for reference-paced portrait explainers.

This module deliberately knows nothing about story generation, research, networking,
Telegram or publishing. It takes a small hand-written storyboard and renders its
visual grammar with Pillow, then streams raw frames to FFmpeg.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import shutil
import subprocess
import time
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

LOG = logging.getLogger("animation")
FPS = 30
SIZE = (1080, 1920)
PLATE_SIZE = (1280, 720)
INK = (31, 34, 39)
IVORY = (250, 247, 239)
WHITE = (255, 255, 255)
MUSTARD = (237, 177, 38)
ORANGE = (239, 112, 41)
SKY = (119, 192, 225)
GREEN = (72, 167, 104)
RED = (213, 63, 56)
PALETTES: dict[str, tuple[tuple[int, int, int], ...]] = {
    "snow_forest": ((125, 180, 213), (217, 232, 239), (91, 127, 135), (48, 76, 78)),
    "village_street": ((236, 192, 132), (238, 221, 182), (147, 102, 74), (83, 87, 75)),
    "barn": ((226, 190, 133), (238, 220, 181), (139, 69, 49), (87, 55, 40)),
    "kitchen": ((197, 219, 218), (235, 232, 213), (120, 139, 126), (70, 76, 70)),
    "winter_room": ((142, 171, 195), (223, 231, 233), (117, 122, 121), (61, 73, 85)),
    "market": ((240, 194, 107), (242, 222, 175), (164, 101, 54), (77, 86, 66)),
    "forest": ((105, 161, 159), (207, 222, 196), (58, 104, 79), (37, 71, 57)),
    "generic": ((149, 189, 206), (225, 227, 211), (128, 128, 104), (69, 75, 76)),
}


def _clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


def smoothstep(t: float) -> float:
    t = _clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def ease_out_cubic(t: float) -> float:
    t = _clamp(t, 0.0, 1.0)
    return 1.0 - (1.0 - t) ** 3


@lru_cache(maxsize=64)
def _font_path(bold: bool = True) -> str | None:
    choices = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
    )
    return next((p for p in choices if Path(p).is_file()), None)


@lru_cache(maxsize=96)
def _font(size: int, bold: bool = True) -> ImageFont.ImageFont:
    path = _font_path(bold)
    return ImageFont.truetype(path, max(10, int(size))) if path else ImageFont.load_default()


def _text_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont,
                max_width: int) -> list[str]:
    words = str(text).split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textbbox((0, 0), candidate, font=font, stroke_width=0)[2] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def draw_fitted_text(draw: ImageDraw.ImageDraw, text: str, center: tuple[float, float],
                     max_width: int, size: int, fill: tuple[int, ...] = WHITE,
                     stroke_fill: tuple[int, ...] = INK, stroke_width: int = 4,
                     max_lines: int = 2, bold: bool = True) -> tuple[int, int, int, int]:
    chosen = _font(size, bold)
    lines = _text_lines(draw, text, chosen, max_width)
    while (len(lines) > max_lines or any(draw.textbbox((0, 0), line, font=chosen,
                                                       stroke_width=stroke_width)[2] > max_width
                                          for line in lines)) and size > 24:
        size -= 2
        chosen = _font(size, bold)
        lines = _text_lines(draw, text, chosen, max_width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    line_height = int(size * 1.13)
    y = center[1] - (len(lines) * line_height) / 2
    left = 10**9
    top = 10**9
    right = -1
    bottom = -1
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=chosen, stroke_width=stroke_width)
        x = center[0] - (bbox[2] - bbox[0]) / 2 - bbox[0]
        draw.text((round(x), round(y)), line, font=chosen, fill=fill,
                  stroke_width=stroke_width, stroke_fill=stroke_fill)
        left = min(left, int(x + bbox[0]))
        right = max(right, int(x + bbox[2]))
        top = min(top, int(y + bbox[1]))
        bottom = max(bottom, int(y + bbox[3]))
        y += line_height
    if right < 0:
        return (0, 0, 0, 0)
    return left, top, right, bottom


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def _seed(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


@lru_cache(maxsize=48)
def procedural_plate(theme: str, variant: str = "wide", size: tuple[int, int] = PLATE_SIZE) -> Image.Image:
    """Create a cached 16:9 painted plate. Generated plates live in memory, never assets/."""
    import random
    w, h = size
    colors = PALETTES.get(theme, PALETTES["generic"])
    sky, pale, mid, dark = colors
    im = Image.new("RGB", size, sky)
    d = ImageDraw.Draw(im)
    for y in range(int(h * 0.66)):
        p = y / max(1, int(h * 0.66))
        col = tuple(round(sky[i] * (1-p) + pale[i] * p) for i in range(3))
        d.line((0, y, w, y), fill=col)
    if theme in {"snow_forest", "forest", "winter_room"}:
        d.polygon([(0, h*.54), (w*.16, h*.24), (w*.32, h*.52), (w*.52, h*.18),
                   (w*.74, h*.52), (w*.88, h*.30), (w, h*.48), (w, h*.72), (0, h*.72)], fill=pale)
        d.polygon([(w*.16, h*.24), (w*.24, h*.37), (w*.32, h*.52)], fill=(180, 208, 218))
        d.polygon([(w*.52, h*.18), (w*.60, h*.35), (w*.74, h*.52)], fill=(181, 207, 215))
        ground = (232, 237, 231) if theme != "forest" else (113, 148, 94)
        d.rectangle((0, int(h*.64), w, h), fill=ground)
        rng = random.Random(_seed(theme + variant))
        for i in range(21):
            x = int(i * w / 20 + rng.randint(-16, 16))
            height = rng.randint(int(h*.22), int(h*.48))
            base = int(h*.79 + rng.randint(-12, 22))
            trunk = max(4, w//170)
            d.rectangle((x-trunk//2, base-height//4, x+trunk//2, base), fill=dark)
            for tier in range(4):
                yy = base - height + tier * height * .17
                half = height * (.16 - tier*.018)
                d.polygon([(x, yy), (x-half, yy+height*.24), (x+half, yy+height*.24)], fill=mid if i%3 else dark)
                if theme == "snow_forest":
                    d.line((x, yy+height*.13, x-half*.62, yy+height*.24), fill=(236, 244, 245), width=max(2, w//400))
        if theme == "snow_forest":
            d.ellipse((w*.78, h*.12, w*.88, h*.28), fill=(246, 244, 223))
    elif theme in {"village_street", "market", "barn", "kitchen"}:
        d.rectangle((0, int(h*.66), w, h), fill=(172, 153, 116) if theme != "kitchen" else (183, 190, 173))
        d.polygon([(0,h*.68),(w*.28,h*.48),(w*.54,h*.67),(w*.8,h*.5),(w,h*.66),(w,h),(0,h)], fill=(197, 177, 138))
        if theme == "barn":
            d.polygon([(w*.12,h*.48),(w*.5,h*.19),(w*.88,h*.48)], fill=(93, 53, 42))
            d.rectangle((w*.18,h*.46,w*.82,h*.91), fill=(151, 58, 47), outline=(65, 42, 36), width=8)
            for x in (w*.25,w*.4,w*.6,w*.75):
                d.line((x,h*.48,x,h*.9), fill=(190, 92, 72), width=5)
            d.polygon([(w*.38,h*.91),(w*.38,h*.64),(w*.62,h*.64),(w*.62,h*.91)], fill=(68, 42, 34))
        elif theme == "kitchen":
            d.rectangle((w*.04,h*.16,w*.96,h*.8), fill=(238, 234, 215), outline=(99, 111, 108), width=7)
            d.rectangle((w*.09,h*.22,w*.36,h*.49), fill=(153, 197, 206), outline=(99, 111, 108), width=6)
            d.line((w*.225,h*.22,w*.225,h*.49), fill=(99,111,108), width=5)
            d.rectangle((w*.56,h*.49,w*.88,h*.73), fill=(101, 101, 91), outline=(55,59,57), width=6)
            for cx in (w*.64,w*.79):
                d.ellipse((cx-30,h*.52,cx+30,h*.59), outline=(210, 208, 196), width=6)
        else:
            d.polygon([(w*.36,h*.66),(w*.64,h*.66),(w*.84,h),(w*.17,h)], fill=(211, 195, 160))
            for x, col in ((w*.04,(201,153,103)),(w*.68,(206,175,130))):
                d.rectangle((x,h*.4,x+w*.25,h*.76), fill=col, outline=(89,68,50), width=4)
                d.polygon([(x-w*.02,h*.41),(x+w*.12,h*.24),(x+w*.28,h*.41)], fill=(126,73,52))
                d.rectangle((x+w*.09,h*.52,x+w*.16,h*.76), fill=(77,60,47))
            if theme == "market":
                d.polygon([(w*.38,h*.47),(w*.59,h*.47),(w*.63,h*.58),(w*.34,h*.58)], fill=(173,76,55))
                for j in range(5):
                    x = w*.36 + j*w*.052
                    d.polygon([(x,h*.47),(x+w*.025,h*.47),(x+w*.025,h*.58),(x,h*.58)], fill=(244,218,171) if j%2 else (192,79,58))
    else:
        d.rectangle((0,int(h*.68),w,h), fill=(191,175,145))
        d.ellipse((w*.66,h*.15,w*.9,h*.55), fill=(244,222,152))
        for i in range(5):
            x = w*(.08+i*.2)
            d.line((x,h*.25,x+w*.06,h*.63), fill=mid, width=5)
            d.ellipse((x-w*.025,h*.21,x+w*.085,h*.38), fill=mid)
    rng = random.Random(_seed(theme + ":grain:" + variant))
    for _ in range(65):
        x, y = rng.randrange(w), rng.randrange(h)
        c = tuple(int(_clamp(ch + rng.randint(-9, 9), 0, 255)) for ch in im.getpixel((x,y)))
        d.point((x,y), fill=c)
    return im


def _plate_for(shot: dict[str, Any], assets_dir: Path | None) -> Image.Image:
    asset = shot.get("asset")
    if asset and assets_dir:
        path = (assets_dir / str(asset)).resolve()
        try:
            path.relative_to(assets_dir.resolve())
        except ValueError as exc:
            raise ValueError(f"Asset path escapes the asset directory: {asset}") from exc
        if path.is_file():
            with Image.open(path) as opened:
                return _cover(opened.copy(), PLATE_SIZE)
        raise FileNotFoundError(f"Storyboard asset does not exist: {path}")
    return procedural_plate(str(shot.get("theme", "generic")), str(shot.get("id", "default")))


def _color(value: Any, default: tuple[int, int, int]) -> tuple[int, int, int]:
    if isinstance(value, str) and len(value) == 7 and value.startswith("#"):
        try:
            return tuple(int(value[i:i+2], 16) for i in (1,3,5))  # type: ignore[return-value]
        except ValueError:
            return default
    if isinstance(value, (tuple, list)) and len(value) == 3:
        try:
            return tuple(int(_clamp(float(v), 0, 255)) for v in value)  # type: ignore[return-value]
        except (TypeError, ValueError):
            pass
    return default


def _draw_background(plate: Image.Image, hook: str, size: tuple[int, int], tint: tuple[int,int,int]) -> Image.Image:
    w, h = size
    background = _cover(plate, size).filter(ImageFilter.GaussianBlur(radius=max(8, round(w*.055))))
    background = Image.alpha_composite(background.convert("RGBA"), Image.new("RGBA", size, (6,12,18,132)))
    background = Image.alpha_composite(background, Image.new("RGBA", size, (*tint,30)))
    draw_fitted_text(ImageDraw.Draw(background), hook, (w/2,h*.205), int(w*.87), int(w*.072),
                     WHITE, (15,18,22), max(3,w//220), 2)
    return background


def _draw_stick_figure(im: Image.Image, x: float, ground_y: float, scale: float,
                       expression: str = "neutral", pose: str = "standing",
                       color: tuple[int,int,int] = INK, time_s: float = 0.0) -> None:
    d = ImageDraw.Draw(im)
    sw = max(3, round(scale*.035))
    head_r = scale*.115
    head_c = (int(x), int(ground_y-scale*.78))
    d.ellipse((head_c[0]-head_r,head_c[1]-head_r,head_c[0]+head_r,head_c[1]+head_r),
              fill=(255,249,232),outline=color,width=sw)
    eye_y=head_c[1]-scale*.015
    d.ellipse((x-scale*.045,eye_y-scale*.015,x-scale*.02,eye_y+scale*.02),fill=color)
    d.ellipse((x+scale*.02,eye_y-scale*.015,x+scale*.045,eye_y+scale*.02),fill=color)
    if expression in {"shocked","scared"}:
        d.ellipse((x-scale*.02,head_c[1]+scale*.035,x+scale*.02,head_c[1]+scale*.085),outline=color,width=sw)
    elif expression in {"happy","smile"}:
        d.arc((x-scale*.055,head_c[1]+scale*.005,x+scale*.055,head_c[1]+scale*.09),0,180,fill=color,width=sw)
    elif expression in {"sad","worried"}:
        d.arc((x-scale*.05,head_c[1]+scale*.05,x+scale*.05,head_c[1]+scale*.12),180,360,fill=color,width=sw)
    else:
        d.line((x-scale*.035,head_c[1]+scale*.065,x+scale*.035,head_c[1]+scale*.065),fill=color,width=sw)
    neck_y=head_c[1]+head_r*.9
    hip_y=ground_y-scale*.28
    d.line((x,neck_y,x,hip_y),fill=color,width=sw)
    is_running=pose in {"running","run","chase"}
    phase=time_s*2*math.pi*8.5
    arm_swing=math.sin(phase)*scale*.12 if is_running else 0
    leg_swing=math.sin(phase)*scale*.17 if is_running else 0
    shoulder_y=neck_y+scale*.09
    d.line((x,shoulder_y,x-scale*.14+arm_swing,shoulder_y+scale*.19),fill=color,width=sw)
    d.line((x,shoulder_y,x+scale*.14-arm_swing,shoulder_y+scale*.18),fill=color,width=sw)
    d.line((x,hip_y,x-scale*.12+leg_swing,ground_y),fill=color,width=sw)
    d.line((x,hip_y,x+scale*.12-leg_swing,ground_y),fill=color,width=sw)
    if pose=="knight":
        d.polygon([(x-scale*.12,neck_y),(x+scale*.12,neck_y),(x+scale*.09,neck_y+scale*.2),(x-scale*.09,neck_y+scale*.2)],fill=(73,126,151))
        d.arc((x-scale*.1,head_c[1]-head_r*.9,x+scale*.1,head_c[1]+head_r*.7),180,360,fill=(176,181,185),width=sw+2)
    elif pose in {"thief","sneaky"}:
        d.arc((x-scale*.12,head_c[1]-head_r*1.08,x+scale*.12,head_c[1]+head_r*.6),180,360,fill=(55,60,67),width=sw+2)


def _draw_icon(draw: ImageDraw.ImageDraw, icon: str, center: tuple[float,float], radius: float,
               stroke: tuple[int,int,int] = INK, fill: tuple[int,int,int] = WHITE) -> None:
    x,y,r=center[0],center[1],radius
    sw=max(4,round(r*.055))
    if icon in {"dog","pet"}:
        draw.ellipse((x-r*.55,y-r*.25,x+r*.55,y+r*.48),fill=fill,outline=stroke,width=sw)
        draw.ellipse((x-r*.72,y-r*.48,x-r*.27,y+r*.04),fill=fill,outline=stroke,width=sw)
        draw.ellipse((x+r*.27,y-r*.48,x+r*.72,y+r*.04),fill=fill,outline=stroke,width=sw)
        draw.ellipse((x-r*.19,y-r*.02,x-r*.08,y+r*.09),fill=stroke)
        draw.ellipse((x+r*.08,y-r*.02,x+r*.19,y+r*.09),fill=stroke)
        draw.ellipse((x-r*.07,y+r*.13,x+r*.07,y+r*.24),fill=stroke)
        draw.arc((x-r*.22,y+r*.17,x+r*.22,y+r*.4),0,180,fill=stroke,width=sw)
    elif icon in {"cow","cattle"}:
        draw.rounded_rectangle((x-r*.53,y-r*.35,x+r*.53,y+r*.43),radius=int(r*.13),fill=fill,outline=stroke,width=sw)
        draw.ellipse((x-r*.78,y-r*.55,x-r*.43,y-r*.22),fill=fill,outline=stroke,width=sw)
        draw.ellipse((x+r*.43,y-r*.55,x+r*.78,y-r*.22),fill=fill,outline=stroke,width=sw)
        draw.ellipse((x-r*.28,y-r*.05,x-r*.16,y+r*.07),fill=stroke)
        draw.ellipse((x+r*.16,y-r*.05,x+r*.28,y+r*.07),fill=stroke)
        draw.ellipse((x-r*.25,y+r*.14,x+r*.25,y+r*.35),fill=(236,169,159),outline=stroke,width=max(2,sw//2))
    elif icon in {"face","head","smile"}:
        draw.ellipse((x-r*.58,y-r*.58,x+r*.58,y+r*.58),fill=fill,outline=stroke,width=sw)
        draw.ellipse((x-r*.26,y-r*.17,x-r*.13,y-r*.02),fill=stroke)
        draw.ellipse((x+r*.13,y-r*.17,x+r*.26,y-r*.02),fill=stroke)
        draw.arc((x-r*.28,y-r*.03,x+r*.28,y+r*.33),0,180,fill=stroke,width=sw)
    elif icon in {"radiator","heater"}:
        draw.rounded_rectangle((x-r*.64,y-r*.45,x+r*.64,y+r*.48),radius=int(r*.09),fill=(246,244,231),outline=stroke,width=sw)
        for i in range(5):
            xx=x-r*.48+i*r*.24
            draw.rounded_rectangle((xx,y-r*.33,xx+r*.12,y+r*.31),radius=int(r*.04),fill=(216,220,215),outline=stroke,width=max(2,sw//2))
        draw.line((x-r*.45,y+r*.55,x-r*.45,y+r*.68),fill=stroke,width=sw)
        draw.line((x+r*.45,y+r*.55,x+r*.45,y+r*.68),fill=stroke,width=sw)
    elif icon in {"stove","fire"}:
        draw.rounded_rectangle((x-r*.55,y-r*.12,x+r*.55,y+r*.51),radius=int(r*.08),fill=(111,116,114),outline=stroke,width=sw)
        draw.rectangle((x-r*.43,y-r*.42,x+r*.43,y-r*.12),fill=(52,57,59),outline=stroke,width=sw)
        draw.polygon([(x,y-r*.3),(x-r*.16,y+.02*r),(x-r*.04,y-r*.04),(x+r*.17,y+r*.04)],fill=ORANGE)
        draw.polygon([(x,y-r*.2),(x-r*.07,y-r*.01),(x+r*.07,y+r*.03)],fill=MUSTARD)
    elif icon in {"bread","loaf"}:
        draw.rounded_rectangle((x-r*.58,y-r*.24,x+r*.58,y+r*.35),radius=int(r*.25),fill=(217,166,92),outline=stroke,width=sw)
        for offset in (-.27,0,.27):
            draw.arc((x+r*(offset-.1),y-r*.16,x+r*(offset+.1),y+r*.22),200,340,fill=(246,211,154),width=max(3,sw//2))
    elif icon in {"snow","snowflake"}:
        for angle in (0,math.pi/3,2*math.pi/3):
            dx,dy=math.cos(angle)*r*.62,math.sin(angle)*r*.62
            draw.line((x-dx,y-dy,x+dx,y+dy),fill=fill,width=sw)
    elif icon in {"coin","money"}:
        draw.ellipse((x-r*.58,y-r*.58,x+r*.58,y+r*.58),fill=MUSTARD,outline=stroke,width=sw)
        draw.text((x,y),"$",font=_font(int(r*.9)),fill=stroke,anchor="mm")
    else:
        draw.ellipse((x-r*.52,y-r*.52,x+r*.52,y+r*.52),fill=fill,outline=stroke,width=sw)
        draw.text((x,y),"?",font=_font(int(r)),fill=stroke,anchor="mm")


def _draw_fx(im: Image.Image, shot: dict[str,Any], t: float, seed_text: str) -> None:
    effects=set(shot.get("effects",[]))
    if not effects:
        return
    d=ImageDraw.Draw(im,"RGBA")
    w,h=im.size
    import random
    rng=random.Random(_seed(seed_text))
    if "snow" in effects:
        for i in range(28):
            x0=rng.uniform(0,w);y0=rng.uniform(-h*.1,h)
            x=(x0+math.sin(t*.65+i)*w*.018)%w
            y=(y0+t*(16+(i%5)*8))%h
            rr=2+i%3
            d.ellipse((x-rr,y-rr,x+rr,y+rr),fill=(248,251,255,155))
    if "heat_waves" in effects:
        for k in range(3):
            pts=[]
            base_x=w*(.35+k*.12)
            for j in range(17):
                yy=h*(.67-j*.033)
                xx=base_x+math.sin(t*2*math.pi*1.4+j*.75+k)*w*.014
                pts.append((xx,yy))
            d.line(pts,fill=(255,250,224,220),width=max(3,w//260),joint="curve")
    for name,frequency,color in (("steam",.55,(245,247,245,210)),("green_fumes",.5,(118,224,132,215)),
                                 ("smoke",.75,(85,87,87,190)),("smell",.42,(244,248,221,215))):
        if name not in effects:
            continue
        for k in range(3):
            phase=(t*frequency+k/3)%1
            x=w*(.47+k*.035)+math.sin(t*1.7+k)*w*.012
            y=h*(.66-phase*.35)
            r=w*(.013+phase*.011)
            alpha=int(color[3]*(1-phase*.68))
            d.ellipse((x-r,y-r,x+r,y+r),fill=(color[0],color[1],color[2],alpha))
    if "zzz" in effects:
        phase=(t*.42)%1
        x=w*.68+math.sin(t*1.3)*w*.015
        y=h*(.43-phase*.18)
        for k in range(3):
            size=int(w*(.042-k*.006))
            d.text((x+k*w*.035,y-k*w*.02),"Z",font=_font(size),fill=(255,255,255,225),
                   stroke_width=max(2,w//540),stroke_fill=(40,47,55,210))
    if "fire_flicker" in effects:
        for k in range(4):
            phase=math.sin(t*2*math.pi*(7.5+k*.41)+k*2.7)
            cx=w*(.48+(k-1.5)*.035);base=h*.77
            height=h*(.12+.035*phase)
            d.polygon([(cx-w*.025,base),(cx,base-height),(cx+w*.025,base)],fill=(232,76+max(0,int(phase*25)),32,190))
            d.polygon([(cx-w*.01,base),(cx,base-height*.57),(cx+w*.012,base)],fill=(255,198,48,225))
    if "dust" in effects:
        for k in range(7):
            phase=(t*1.1+k/7)%1
            x=w*(.2+phase*.7);y=h*(.84-math.sin(phase*math.pi)*.07)
            r=w*(.006+.004*math.sin(phase*math.pi))
            d.ellipse((x-r,y-r,x+r,y+r),fill=(229,206,158,int(150*(1-phase))))


def _draw_tick_or_cross(draw: ImageDraw.ImageDraw, center: tuple[float,float], good: bool,
                        size: float, color: tuple[int,int,int] | None = None, width: int | None = None) -> None:
    x,y=center;c=color or (GREEN if good else RED);sw=width or max(8,round(size*.13))
    if good:
        draw.line((x-size*.5,y,x-size*.12,y+size*.34,x+size*.55,y-size*.42),fill=c,width=sw,joint="curve")
    else:
        draw.line((x-size*.42,y-size*.42,x+size*.42,y+size*.42),fill=c,width=sw)
        draw.line((x+size*.42,y-size*.42,x-size*.42,y+size*.42),fill=c,width=sw)


def _draw_stamp(im: Image.Image, text: str, t: float, duration: float,
                color: tuple[int,int,int] = RED) -> None:
    w,h=im.size
    pop=1.8-.8*ease_out_cubic(t/.22)
    fade=_clamp(t/.055,0,1)
    sw=int(w*.56*pop);sh=int(h*.112*pop)
    layer=Image.new("RGBA",(sw+60,sh+60),(0,0,0,0));d=ImageDraw.Draw(layer)
    c=(*color,int(245*fade))
    d.rounded_rectangle((12,12,sw+48,sh+48),radius=max(12,sw//24),outline=c,width=max(8,sw//45))
    draw_fitted_text(d,text,(layer.width/2,layer.height/2),int(sw*.9),int(sh*.55),c,
                     (255,255,255,220),max(1,sw//180),1)
    shake=(1-t/.30)*w*.009*math.sin(t*85) if t<.30 else 0.0
    layer=layer.rotate(-8+2*ease_out_cubic(t/.22),resample=Image.Resampling.BICUBIC,expand=True)
    im.alpha_composite(layer,(round(w/2-layer.width/2+shake),round(h*.52-layer.height/2)))


def _render_icon_card(shot: dict[str,Any], size: tuple[int,int], t: float) -> Image.Image:
    w,h=size;color=_color(shot.get("card_color"),MUSTARD)
    im=Image.new("RGBA",size,(*color,255));d=ImageDraw.Draw(im)
    d.ellipse((w*.23,h*.05,w*.77,h*.95),fill=(255,255,255,35))
    _draw_icon(d,str(shot.get("icon","question")),(w*.5,h*.42),min(w*.18,h*.36),stroke=INK,fill=WHITE)
    caption=shot.get("caption","")
    if caption:
        draw_fitted_text(d,str(caption),(w*.5,h*.86),int(w*.88),int(h*.14),WHITE,INK,max(3,w//180),2)
    overlay=shot.get("overlay",{})
    if overlay.get("kind") in {"cross","tick"}:
        _draw_tick_or_cross(d,(w*.82,h*.22),overlay.get("kind")=="tick",min(w,h)*.16)
    _draw_fx(im,shot,t,str(shot.get("id","shot")))
    if shot.get("stamp"):
        _draw_stamp(im,str(shot["stamp"]),t,float(shot.get("duration",1.25)),_color(shot.get("stamp_color"),RED))
    return im


def _apply_camera(plate: Image.Image, shot: dict[str,Any], t: float, duration: float,
                  card_size: tuple[int,int]) -> Image.Image:
    w,h=card_size
    progress=smoothstep(t/max(.001,duration))
    camera=str(shot.get("camera","push_in"))
    zoom_start=float(shot.get("zoom_start",1.0))
    zoom_end=float(shot.get("zoom_end",1.09 if camera=="push_in" else 1.035))
    zoom=zoom_start+(zoom_end-zoom_start)*progress if camera in {"push_in","pull_out"} else zoom_end
    if camera=="pull_out":
        zoom=zoom_end+(zoom_start-zoom_end)*progress
    zoom=max(1.0,zoom)
    src=plate.convert("RGB");sw,sh=src.size
    crop_w,crop_h=sw/zoom,sh/zoom
    pan=float(shot.get("pan",.025))
    if camera=="pan_left":
        x0=(sw-crop_w)*progress
    elif camera=="pan_right":
        x0=(sw-crop_w)*(1-progress)
    else:
        x0=(sw-crop_w)/2+math.sin(progress*math.pi)*pan*sw
    y0=(sh-crop_h)/2+math.sin(progress*math.pi*1.2)*float(shot.get("pan_y",.012))*sh
    x0=_clamp(x0,0,sw-crop_w);y0=_clamp(y0,0,sh-crop_h)
    return src.transform((w,h),Image.Transform.EXTENT,(x0,y0,x0+crop_w,y0+crop_h),resample=Image.Resampling.BICUBIC)


def _draw_comparison(card: Image.Image, shot: dict[str,Any], t: float) -> None:
    w,h=card.size;d=ImageDraw.Draw(card)
    left=_color(shot.get("left_color"),(240,236,223));right=_color(shot.get("right_color"),(190,218,196))
    d.rectangle((0,0,w//2,h),fill=left);d.rectangle((w//2,0,w,h),fill=right)
    d.line((w//2,0,w//2,h),fill=WHITE,width=max(5,w//180))
    _draw_icon(d,str(shot.get("left_icon","stove")),(w*.25,h*.39),min(w*.125,h*.24),fill=WHITE)
    _draw_icon(d,str(shot.get("right_icon","radiator")),(w*.75,h*.39),min(w*.125,h*.24),fill=WHITE)
    draw_fitted_text(d,str(shot.get("left_label","THEN")),(w*.25,h*.77),int(w*.43),int(h*.10),INK,WHITE,max(1,w//450),1)
    draw_fitted_text(d,str(shot.get("right_label","NOW")),(w*.75,h*.77),int(w*.43),int(h*.10),INK,WHITE,max(1,w//450),1)
    _draw_tick_or_cross(d,(w*.25,h*.18),False,min(w,h)*.09)
    _draw_tick_or_cross(d,(w*.75,h*.18),True,min(w,h)*.09)


def _render_card(shot: dict[str,Any], plate: Image.Image, t: float, duration: float,
                 size: tuple[int,int]) -> Image.Image:
    w,h=size;kind=str(shot.get("type","illustration"))
    if kind=="icon_card":
        return _render_icon_card(shot,size,t)
    card=_apply_camera(plate,shot,t,duration,size).convert("RGBA");d=ImageDraw.Draw(card)
    if kind=="then_now":
        _draw_comparison(card,shot,t);d=ImageDraw.Draw(card)
    elif kind=="statistic":
        d.rectangle((0,0,w,h),fill=_color(shot.get("card_color"),MUSTARD))
        draw_fitted_text(d,str(shot.get("stat",shot.get("caption","50%"))),(w*.5,h*.43),int(w*.84),int(h*.42),WHITE,INK,max(4,w//140),1)
        if shot.get("subcaption"):
            draw_fitted_text(d,str(shot["subcaption"]),(w*.5,h*.77),int(w*.85),int(h*.13),INK,WHITE,max(2,w//250),2)
    elif kind=="method_card":
        d.rectangle((0,0,w,h),fill=_color(shot.get("card_color"),(252,250,241)))
        d.rounded_rectangle((w*.07,h*.12,w*.27,h*.50),radius=max(10,w//70),fill=_color(shot.get("number_color"),ORANGE))
        draw_fitted_text(d,str(shot.get("number","5")),(w*.17,h*.31),int(w*.18),int(h*.28),WHITE,INK,max(2,w//320),1)
        _draw_icon(d,str(shot.get("icon","bread")),(w*.51,h*.40),min(w*.13,h*.25),fill=WHITE)
        draw_fitted_text(d,str(shot.get("caption","BARK BREAD")),(w*.5,h*.82),int(w*.86),int(h*.17),INK,WHITE,max(2,w//260),2)
    elif kind=="face_card":
        d.rectangle((0,0,w,h),fill=_color(shot.get("card_color"),SKY))
        _draw_icon(d,"face",(w*.5,h*.42),min(w*.2,h*.37),fill=(255,244,211))
    else:
        actor=shot.get("actor")
        if isinstance(actor,dict):
            scale=float(actor.get("scale",h*.72))*w/1080.0
            entrance=str(actor.get("entrance","none"));p=ease_out_cubic(t/.42) if entrance!="none" else 1.0
            x_frac=float(actor.get("x",.5))
            if entrance=="left":x_frac=-.12+(x_frac+.12)*p
            elif entrance=="right":x_frac=1.12+(x_frac-1.12)*p
            elif entrance=="slide_left":x_frac=1.15+(x_frac-1.15)*p
            bob=abs(math.sin(t*2*math.pi*8.5))*scale*.025 if actor.get("pose") in {"run","running","chase"} else math.sin(t*2.2)*scale*.008
            x=x_frac*w;y=h*.95-bob
            _draw_stick_figure(card,x,y,scale,str(actor.get("expression","neutral")),str(actor.get("pose","standing")),_color(actor.get("color"),INK),t)
            if entrance in {"left","right","slide_left"} and t<.42:
                alpha=int(90*(1-t/.42))
                ImageDraw.Draw(card).line((max(0,x-w*.05),h*.91,min(w,x+w*.05),h*.91),fill=(255,255,255,alpha),width=max(3,w//180))
    _draw_fx(card,shot,t,str(shot.get("id","shot")))
    if shot.get("stamp"):
        _draw_stamp(card,str(shot["stamp"]),t,duration,_color(shot.get("stamp_color"),RED))
    overlay=shot.get("overlay",{})
    if isinstance(overlay,dict) and overlay.get("kind") in {"cross","tick"}:
        pos=overlay.get("position",[.86,.18])
        _draw_tick_or_cross(ImageDraw.Draw(card),(w*float(pos[0]),h*float(pos[1])),overlay["kind"]=="tick",min(w,h)*.12)
    if shot.get("caption") and kind not in {"statistic","method_card"}:
        d=ImageDraw.Draw(card);d.rounded_rectangle((w*.045,h*.80,w*.955,h*.955),radius=max(12,w//90),fill=(18,22,27,210))
        draw_fitted_text(d,str(shot["caption"]),(w*.5,h*.875),int(w*.84),int(h*.10),WHITE,(20,24,28),max(2,w//280),2)
    if shot.get("rounded_card"):
        mask=Image.new("L",size,0);ImageDraw.Draw(mask).rounded_rectangle((0,0,w-1,h-1),radius=max(16,w//55),fill=255);card.putalpha(mask)
    return card


@dataclass(frozen=True)
class ShotEntry:
    index: int
    data: dict[str,Any]
    start: float
    duration: float


def parse_storyboard(data: dict[str,Any]) -> tuple[str,list[ShotEntry]]:
    if not isinstance(data,dict) or not isinstance(data.get("shots"),list) or not data["shots"]:
        raise ValueError("Storyboard must contain a non-empty 'shots' array")
    hook=str(data.get("hook","A VISUAL EXPLAINER")).strip()
    entries: list[ShotEntry]=[];cursor=0.0;seen:set[str]=set()
    for i,shot in enumerate(data["shots"]):
        if not isinstance(shot,dict):raise ValueError(f"Shot {i} must be an object")
        shot_id=str(shot.get("id",f"shot_{i+1:02d}"))
        if shot_id in seen:raise ValueError(f"Duplicate shot id: {shot_id}")
        seen.add(shot_id)
        duration=float(shot.get("duration",1.25))
        if not math.isfinite(duration) or duration<.2 or duration>5.0:
            raise ValueError(f"Shot {shot_id} duration must be between 0.2 and 5.0 seconds")
        clean=dict(shot);clean["id"]=shot_id;clean["duration"]=duration
        entries.append(ShotEntry(i,clean,cursor,duration));cursor+=duration
    return hook,entries


class AnimationCompositor:
    """State-free compositor with cached per-shot plates and backgrounds."""
    def __init__(self, storyboard: dict[str,Any], size: tuple[int,int] = SIZE, assets_dir: Path | None = None):
        self.hook,self.entries=parse_storyboard(storyboard)
        self.size=size;self.assets_dir=assets_dir.resolve() if assets_dir else None
        self.duration=sum(x.duration for x in self.entries)
        self._plates:dict[str,Image.Image]={};self._backgrounds:dict[str,Image.Image]={}
        for entry in self.entries:
            shot=entry.data;plate=_plate_for(shot,self.assets_dir);self._plates[shot["id"]]=plate
            default=PALETTES.get(str(shot.get("theme","generic")),PALETTES["generic"])[0]
            tint=_color(shot.get("tint"),_color(shot.get("card_color"),default))
            self._backgrounds[shot["id"]]=_draw_background(plate,self.hook,size,tint)

    def frame(self,time_s:float)->Image.Image:
        if not math.isfinite(time_s):raise ValueError("Frame timestamp must be finite")
        t=_clamp(time_s,0,max(0,self.duration-1/FPS));entry=self.entries[-1]
        for candidate in self.entries:
            if t<candidate.start+candidate.duration:
                entry=candidate;break
        local_t=t-entry.start;shot=entry.data
        background=self._backgrounds[shot["id"]].copy();plate=self._plates[shot["id"]]
        card_w=self.size[0];card_h=min(round(card_w*9/16),round(self.size[1]*.42))
        card=_render_card(shot,plate,local_t,entry.duration,(card_w,card_h))
        # Full-width 16:9 art card spans 34.2–65.8% of 1080x1920 reference output.
        y=(self.size[1]-card_h)//2
        if shot.get("rounded_card"):
            shadow=Image.new("RGBA",self.size,(0,0,0,0));sd=ImageDraw.Draw(shadow)
            sd.rounded_rectangle((8,y+12,self.size[0]-8,y+card_h+12),radius=max(16,self.size[0]//55),fill=(0,0,0,120))
            background=Image.alpha_composite(background,shadow.filter(ImageFilter.GaussianBlur(max(5,self.size[0]//90))))
            background.alpha_composite(card,(0,y))
            ImageDraw.Draw(background).rounded_rectangle((2,y+2,self.size[0]-3,y+card_h-3),radius=max(16,self.size[0]//55),outline=(255,255,255,230),width=max(3,self.size[0]//240))
        else:
            background.alpha_composite(card,(0,y))
        return background.convert("RGB")

    def frame_at_index(self,index:int,fps:int=FPS)->Image.Image:
        if index<0:raise ValueError("Frame index must be non-negative")
        return self.frame(index/fps)


def make_synthetic_ticks(path:Path,entries:Iterable[ShotEntry],duration:float,sample_rate:int=24000)->None:
    import struct
    nframes=round(duration*sample_rate);samples=[0]*nframes
    for entry in entries:
        start=round(entry.start*sample_rate);tone_len=round(.055*sample_rate)
        for i in range(tone_len):
            at=start+i
            if 0<=at<nframes:
                envelope=min(1,i/120,(tone_len-i)/420)
                samples[at]=int(3800*max(0,envelope)*math.sin(2*math.pi*880*i/sample_rate))
    path.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(path),"wb") as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(sample_rate)
        wav.writeframes(b"".join(struct.pack("<h",max(-32768,min(32767,v))) for v in samples))


def render_video(compositor:AnimationCompositor,output:Path,fps:int=FPS,audio:str="synthetic",ffmpeg:str="ffmpeg")->dict[str,Any]:
    """Render through FFmpeg stdin; no MoviePy callback or per-frame files."""
    if audio not in {"synthetic","none"}:raise ValueError("audio must be 'synthetic' or 'none'")
    ffmpeg_path=shutil.which(ffmpeg)
    if not ffmpeg_path:raise RuntimeError("FFmpeg is required; install FFmpeg and ensure it is on PATH")
    output=output.resolve();output.parent.mkdir(parents=True,exist_ok=True)
    frame_count=math.ceil(compositor.duration*fps);audio_path=output.with_name(output.stem+".synthetic.wav")
    if audio=="synthetic":make_synthetic_ticks(audio_path,compositor.entries,frame_count/fps)
    cmd=[ffmpeg_path,"-hide_banner","-loglevel","error","-y","-f","rawvideo","-pix_fmt","rgb24","-s:v",
         f"{compositor.size[0]}x{compositor.size[1]}","-r",str(fps),"-i","-"]
    if audio=="synthetic":cmd += ["-i",str(audio_path),"-map","0:v:0","-map","1:a:0","-c:a","aac","-b:a","64k","-shortest"]
    else:cmd += ["-an"]
    cmd += ["-c:v","libx264","-preset","ultrafast","-crf","22","-pix_fmt","yuv420p","-movflags","+faststart",str(output)]
    started=time.monotonic()
    try:proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    except OSError as exc:raise RuntimeError(f"Could not start FFmpeg: {exc}") from exc
    assert proc.stdin is not None
    try:
        for i in range(frame_count):
            proc.stdin.write(compositor.frame_at_index(i,fps).tobytes())
            if i and i%(fps*5)==0:LOG.info("Rendered %d/%d frames (%.1f%%)",i,frame_count,100*i/frame_count)
        proc.stdin.close()
        stderr=proc.stderr.read().decode("utf-8",errors="replace") if proc.stderr else ""
        code=proc.wait(timeout=1200)
    except (BrokenPipeError,OSError) as exc:
        proc.kill();proc.wait()
        tail=proc.stderr.read().decode("utf-8",errors="replace")[-3000:] if proc.stderr else ""
        raise RuntimeError(f"FFmpeg stopped while encoding frames: {exc}\n{tail}") from exc
    if proc.stderr:proc.stderr.close()
    if code:raise RuntimeError(f"FFmpeg exited with code {code}: {stderr[-3000:]}")
    elapsed=time.monotonic()-started
    result={"output":str(output),"width":compositor.size[0],"height":compositor.size[1],"fps":fps,
            "duration_seconds":frame_count/fps,"frame_count":frame_count,"shot_count":len(compositor.entries),
            "render_seconds":round(elapsed,3),"render_fps":round(frame_count/max(.001,elapsed),2),
            "audio":"synthetic transition tones" if audio=="synthetic" else "none",
            "shot_durations":[round(e.duration,3) for e in compositor.entries]}
    output.with_suffix(".report.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    if audio=="synthetic":audio_path.unlink(missing_ok=True)
    return result


def create_contact_sheet(compositor:AnimationCompositor,output:Path,cols:int=4)->Path:
    thumbs=[]
    for entry in compositor.entries:
        frame=compositor.frame(entry.start+min(.35,entry.duration*.4));thumb=frame.copy()
        thumb.thumbnail((270,480),Image.Resampling.LANCZOS)
        canvas=Image.new("RGB",(280,520),(245,245,245));canvas.paste(thumb,((280-thumb.width)//2,5))
        d=ImageDraw.Draw(canvas);d.text((8,490),f'{entry.index+1:02d} {entry.data["id"]}',font=_font(13),fill=INK)
        d.text((8,505),f'{entry.duration:.2f}s / {entry.data.get("type","illustration")}',font=_font(11,False),fill=INK)
        thumbs.append(canvas)
    rows=math.ceil(len(thumbs)/cols);sheet=Image.new("RGB",(cols*280,rows*520),(225,225,225))
    for i,thumb in enumerate(thumbs):sheet.paste(thumb,((i%cols)*280,(i//cols)*520))
    output.parent.mkdir(parents=True,exist_ok=True);sheet.save(output,quality=90)
    return output


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description="Render an offline animation storyboard without LLM or network calls")
    parser.add_argument("--render-golden",action="store_true",help="Render the checked-in 30-second test storyboard")
    parser.add_argument("--storyboard",type=Path,default=Path("tests/golden_storyboard.json"))
    parser.add_argument("--output",type=Path,default=Path("output/animation_stage2.mp4"))
    parser.add_argument("--assets-dir",type=Path,default=None)
    parser.add_argument("--width",type=int,default=1080);parser.add_argument("--height",type=int,default=1920)
    parser.add_argument("--fps",type=int,default=FPS);parser.add_argument("--audio",choices=("synthetic","none"),default="synthetic")
    parser.add_argument("--contact-sheet",type=Path,default=Path("output/animation_contact_sheet.jpg"))
    parser.add_argument("--log-level",default="INFO",choices=("DEBUG","INFO","WARNING","ERROR"))
    args=parser.parse_args(argv);logging.basicConfig(level=getattr(logging,args.log_level),format="%(levelname)s %(message)s")
    if not args.render_golden and not args.storyboard.is_file():parser.error(f"Storyboard file not found: {args.storyboard}")
    if args.width<320 or args.height<568 or args.width%2 or args.height%2:parser.error("Output width/height must be even and at least 320x568")
    if args.fps<1 or args.fps>60:parser.error("FPS must be in the range 1–60")
    try:
        storyboard=json.loads(args.storyboard.read_text(encoding="utf-8"))
        compositor=AnimationCompositor(storyboard,(args.width,args.height),args.assets_dir)
        create_contact_sheet(compositor,args.contact_sheet)
        print(json.dumps(render_video(compositor,args.output,args.fps,args.audio),indent=2))
        return 0
    except (OSError,ValueError,KeyError,RuntimeError,json.JSONDecodeError) as exc:
        LOG.error("Animation render failed: %s",exc);return 2


# Apply reference-specific visual overrides before the CLI or tests render frames.
import reference_art_direction  # noqa: F401,E402

if __name__=="__main__":
    raise SystemExit(main())
