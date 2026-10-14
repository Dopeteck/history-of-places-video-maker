"""Cached image-provider chain and deterministic local illustration fallback.

Optional remote providers are attempted in order. Any timeout, provider error, malformed
response or failed image-quality gate falls through to the next provider and finally to
procedural artwork. No network/API key is needed for the fallback.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
from pathlib import Path
from typing import Callable

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageStat

DEFAULT_SIZE = (1280, 720)
PALETTE = {
    "ivory": "#F7F1E3", "midnight": "#202532", "sand": "#E9D8B5",
    "teal": "#D8EFEB", "slate": "#DCE1E8",
}
INK = "#2B2B2B"
GOLD = "#E0A526"
TEAL = "#1FA2A6"
RED = "#C0392B"


def image_sha1(prompt: str, provider: str, model: str, size: tuple[int, int]) -> str:
    payload = json.dumps({"prompt": prompt, "provider": provider, "model": model,
                          "size": list(size), "version": 1}, sort_keys=True,
                         ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def validate_image(image: Image.Image, size: tuple[int, int] = DEFAULT_SIZE) -> Image.Image:
    """Reject invalid, tiny, blank, near-monochrome, or incorrectly proportioned output."""
    if not isinstance(image, Image.Image):
        raise ValueError("Provider did not return a PIL image")
    image = image.convert("RGB")
    w, h = image.size
    if w < 320 or h < 180:
        raise ValueError("Image is too small for a scene card")
    target_ratio = size[0] / size[1]
    if abs((w / h) - target_ratio) > 0.12:
        raise ValueError("Image aspect ratio is too far from the requested card ratio")
    thumb = image.resize((64, 36), Image.Resampling.BILINEAR)
    extrema = ImageStat.Stat(thumb).stddev
    if max(extrema) < 7.0:
        raise ValueError("Image appears blank or nearly monochrome")
    colors = len(thumb.convert("RGB").getcolors(64 * 36) or [])
    if colors < 18:
        raise ValueError("Image has too little visual variation")
    if image.getbbox() is None:
        raise ValueError("Image has no visible content")
    return image.resize(size, Image.Resampling.LANCZOS)


def _fit_cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Center-crop provider output to the requested card ratio before the quality gate."""
    image = image.convert("RGB")
    target = size[0] / size[1]
    ratio = image.width / image.height
    if ratio > target:
        width = max(1, round(image.height * target))
        left = (image.width - width) // 2
        image = image.crop((left, 0, left + width, image.height))
    elif ratio < target:
        height = max(1, round(image.width / target))
        top = (image.height - height) // 2
        image = image.crop((0, top, image.width, top + height))
    return image


def _download(url: str, timeout: int = 45) -> Image.Image:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    if len(response.content) > 20_000_000:
        raise ValueError("Image provider response exceeds 20 MB")
    return Image.open(io.BytesIO(response.content))


def _fal(prompt: str, size: tuple[int, int], timeout: int) -> Image.Image:
    key = os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")
    if not key:
        raise RuntimeError("FAL_KEY is not configured")
    model = os.getenv("FAL_IMAGE_MODEL", "fal-ai/flux/schnell")
    response = requests.post(
        "https://fal.run/" + model,
        headers={"Authorization": "Key " + key, "Content-Type": "application/json"},
        json={"prompt": prompt, "image_size": "landscape_16_9", "num_images": 1,
              "enable_safety_checker": True},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    images = data.get("images") or []
    if not images or not images[0].get("url"):
        raise ValueError("FAL response contained no image URL")
    return _download(images[0]["url"], timeout)


def _openai(prompt: str, size: tuple[int, int], timeout: int) -> Image.Image:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    response = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": "Bearer " + key},
        json={"model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1"),
              "prompt": prompt, "size": "1536x1024", "n": 1},
        timeout=timeout,
    )
    response.raise_for_status()
    item = response.json()["data"][0]
    if item.get("b64_json"):
        return Image.open(io.BytesIO(__import__("base64").b64decode(item["b64_json"])))
    if item.get("url"):
        return _download(item["url"], timeout)
    raise ValueError("OpenAI image response contained no image")


def procedural_illustration(prompt: str, size: tuple[int, int] = DEFAULT_SIZE,
                            background: str = "ivory") -> Image.Image:
    """Generate deterministic editorial artwork from prompt keywords without external services."""
    w, h = size
    seed = int(hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:8], 16)
    import random
    rng = random.Random(seed)
    bg = PALETTE.get(background, PALETTE["ivory"])
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    # Gentle geometric layers make a deliberate, non-empty illustration.
    horizon = int(h * (0.68 + rng.random() * 0.08))
    d.ellipse((-w * .12, h * .46, w * .58, h * 1.12), fill="#E5D5B6")
    d.ellipse((w * .42, h * .51, w * 1.15, h * 1.14), fill="#B7D8D0")
    d.rectangle((0, horizon, w, h), fill="#EAE0CA")
    words = set(re.findall(r"[a-z]+", prompt.lower()))
    if words & {"city", "building", "palace", "castle", "architecture", "street"}:
        for i in range(6):
            x = int(w * (.06 + i * .15)); bw = int(w * rng.uniform(.07, .12))
            bh = int(h * rng.uniform(.2, .52)); y = horizon - bh
            d.rectangle((x, y, x + bw, horizon), fill=rng.choice(["#D8BF91", "#9FBEB8", "#C8B5A0"]), outline=INK, width=3)
            for wy in range(y + 18, horizon - 12, 28):
                d.rectangle((x + 10, wy, x + 20, wy + 10), fill=GOLD)
    if words & {"person", "people", "student", "worker", "king", "queen", "man", "woman", "character"}:
        for i, x in enumerate((w * .38, w * .62)):
            x, y = int(x), int(h * .58)
            d.ellipse((x - 32, y - 130, x + 32, y - 66), fill="#D7A57B", outline=INK, width=4)
            d.rounded_rectangle((x - 45, y - 70, x + 45, y + 70), radius=18,
                                fill=TEAL if i == 0 else GOLD, outline=INK, width=4)
            d.line((x - 24, y + 70, x - 48, y + 142), fill=INK, width=12)
            d.line((x + 24, y + 70, x + 48, y + 142), fill=INK, width=12)
            d.line((x - 40, y - 24, x - 90, y + 22), fill=INK, width=10)
            d.line((x + 40, y - 24, x + 90, y + 22), fill=INK, width=10)
    if words & {"chart", "growth", "increase", "statistics", "data", "money", "profit", "market"}:
        x0, y0 = int(w * .66), int(h * .64)
        d.line((x0, y0, x0, int(h * .25)), fill=INK, width=5)
        d.line((x0, y0, int(w * .92), y0), fill=INK, width=5)
        for i, bh in enumerate((.12, .22, .32, .43)):
            x = int(w * (.7 + i * .055)); top = int(h * (.62 - bh))
            d.rounded_rectangle((x, top, x + 34, y0), radius=6, fill=TEAL if i < 3 else GOLD)
        d.line((int(w * .68), int(h * .48), int(w * .79), int(h * .39),
                int(w * .87), int(h * .42), int(w * .94), int(h * .25)), fill=RED, width=6)
    if words & {"book", "study", "school", "education", "library"}:
        d.rounded_rectangle((int(w * .12), int(h * .32), int(w * .37), int(h * .62)),
                            radius=12, fill="#F4E8CC", outline=INK, width=5)
        d.line((int(w * .245), int(h * .34), int(w * .245), int(h * .60)), fill=INK, width=4)
        d.line((int(w * .15), int(h * .40), int(w * .21), int(h * .40)), fill=TEAL, width=5)
    # Small decorative circles and a framed composition.
    for _ in range(10):
        x, y, r = rng.randint(20, w - 20), rng.randint(20, h - 20), rng.randint(4, 13)
        d.ellipse((x-r, y-r, x+r, y+r), fill=rng.choice([GOLD, TEAL, "#C0392B"]))
    d.rounded_rectangle((8, 8, w - 9, h - 9), radius=24, outline=INK, width=4)
    return im


class ImageProviderChain:
    """SHA-1 cached remote generation with quality-gated fallbacks."""

    def __init__(self, cache_dir: Path, providers: list[tuple[str, Callable]] | None = None,
                 timeout: int = 60, retries: int = 1):
        self.cache_dir = Path(cache_dir)
        self.providers = providers or [("fal", _fal), ("openai", _openai)]
        self.timeout = max(5, min(int(timeout), 120))
        self.retries = max(0, min(int(retries), 3))

    def generate(self, prompt: str, size: tuple[int, int] = DEFAULT_SIZE,
                 background: str = "ivory") -> tuple[Image.Image, dict]:
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("Image prompt cannot be empty")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        errors = []
        for name, provider in self.providers:
            model = os.getenv("FAL_IMAGE_MODEL", "fal-ai/flux/schnell") if name == "fal" else os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
            key = image_sha1(prompt, name, model, size)
            path = self.cache_dir / (key + ".png")
            if path.exists():
                try:
                    with Image.open(path) as cached:
                        return validate_image(cached.copy(), size), {"provider": name, "cached": True, "sha1": key}
                except Exception:
                    path.unlink(missing_ok=True)
            for attempt in range(self.retries + 1):
                try:
                    image = validate_image(_fit_cover(provider(prompt, size, self.timeout), size), size)
                    tmp = path.with_suffix(".tmp.png")
                    image.save(tmp, format="PNG", optimize=True)
                    tmp.replace(path)
                    return image, {"provider": name, "cached": False, "sha1": key}
                except Exception as exc:
                    errors.append({"provider": name, "error": type(exc).__name__})
                    if attempt < self.retries:
                        time.sleep(min(2 ** attempt, 3))
        # Cache the deterministic local result under a prompt-specific key as well.
        fallback_key = image_sha1(prompt + "|background=" + background, "procedural", "v1", size)
        fallback_path = self.cache_dir / (fallback_key + ".png")
        if fallback_path.exists():
            try:
                with Image.open(fallback_path) as cached:
                    image = validate_image(cached.copy(), size)
                return image, {"provider": "procedural", "cached": True,
                               "sha1": fallback_key, "provider_errors": errors}
            except Exception:
                fallback_path.unlink(missing_ok=True)
        image = validate_image(procedural_illustration(prompt, size, background), size)
        tmp = fallback_path.with_suffix(".tmp.png")
        image.save(tmp, format="PNG", optimize=True)
        tmp.replace(fallback_path)
        return image, {"provider": "procedural", "cached": False,
                       "sha1": fallback_key, "provider_errors": errors}
