#!/usr/bin/env python3
"""Offline vector map renderer (Natural Earth, public domain).

Replaces OSM tile screenshots: no network, no tile-usage-policy concerns, and the
result is a clean stylised map in the region's own palette on a 1080x1920 canvas.
Data: Natural Earth 1:50m admin-0 countries, vendored as data/ne50_countries.json.gz.
"""
from __future__ import annotations
import gzip, hashlib, json, math
from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "ne50_countries.json.gz"
W, H, SS = 1080, 1920, 2           # output size and supersampling factor
LICENSE_URL = "https://www.naturalearthdata.com/about/terms-of-use/"


@lru_cache(maxsize=1)
def _countries():
    with gzip.open(DATA, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _font(size, bold=True):
    p = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        return ImageFont.truetype(p, size)
    except OSError:
        return ImageFont.load_default()


def _rgb(value, default):
    named = {"ink-black": "#20242B", "warm-ivory": "#F2E6CE", "vermilion": "#E65A45", "lagoon-teal": "#19A7A1",
             "sand-gold": "#D9B56D", "deep-indigo": "#26264A", "steel-blue": "#45657A", "warm-neutral": "#C8B89A",
             "dark-contrast": "#262626"}
    v = named.get(str(value).lower(), value)
    if isinstance(v, str) and v.startswith("#") and len(v) == 7:
        return tuple(int(v[i:i + 2], 16) for i in (1, 3, 5))
    return default


def _mix(a, b, t):
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


def _polys(feature):
    g, c = feature["g"], feature["c"]
    if g == "Polygon":
        return [c]
    if g == "MultiPolygon":
        return list(c)
    return []


def make_projection(center_lat, center_lon, span_deg):
    """Equirectangular projection with cos(lat) correction; span_deg = longitude degrees across the frame."""
    k = W * SS / (span_deg * math.cos(math.radians(center_lat)))      # pixels per degree of latitude

    def project(lat, lon):
        x = (W * SS) / 2 + (lon - center_lon) * math.cos(math.radians(center_lat)) * k
        y = (H * SS) / 2 - (lat - center_lat) * k
        return x, y
    return project


def render_map(scene, config, asset_dir=None, idx=0):
    """Return (PIL image 1080x1920, credit dict). Config keys under `map`:
    latitude, longitude, label, span_deg (default 8), places [{label, latitude, longitude}], connect (bool),
    country (name of country to highlight; default config country)."""
    m = config["map"]
    places = [{"label": p.get("label", ""), "lat": float(p["latitude"]), "lon": float(p["longitude"])} for p in m.get("places", [])]
    main = {"label": m.get("label", config["name"]).upper(), "lat": float(m["latitude"]), "lon": float(m["longitude"])}
    pts = [main] + places
    # centre on the mean of all points so every labelled place is inside the safe area
    clat = sum(p["lat"] for p in pts) / len(pts)
    clon = sum(p["lon"] for p in pts) / len(pts)
    clat = float(m.get("center_latitude", clat)); clon = float(m.get("center_longitude", clon))
    span = float(m.get("span_deg", 8.0))
    project = make_projection(clat, clon, span)

    style = config.get("visual_style", {})
    palette = style.get("palette", ["ink-black", "warm-ivory", "vermilion"])
    sea = _rgb(palette[0], (32, 36, 43))
    land = _rgb(palette[1] if len(palette) > 1 else "warm-ivory", (242, 230, 206))
    accent = _rgb(style.get("accent_color", "#E65A45"), (230, 90, 69))
    sea = _mix(sea, (0, 0, 0), .15)
    other_land = _mix(land, sea, .55)
    home_land = _mix(land, sea, .08)
    border = _mix(sea, land, .25)

    img = Image.new("RGB", (W * SS, H * SS), sea)
    d = ImageDraw.Draw(img)
    # faint graticule so the sea is not an empty flat colour
    step = 1 if span <= 12 else 5
    lat_lo, lat_hi = clat - H / W * span, clat + H / W * span
    for lon in range(int(clon - span) - 1, int(clon + span) + 2, step):
        a, b = project(lat_lo, lon), project(lat_hi, lon)
        d.line([a, b], fill=_mix(sea, land, .07), width=SS)
    for lat in range(int(lat_lo) - 1, int(lat_hi) + 2, step):
        a, b = project(lat, clon - span), project(lat, clon + span)
        d.line([a, b], fill=_mix(sea, land, .07), width=SS)

    home_country = str(m.get("country") or config.get("country", "")).lower()
    for f in _countries():
        fill = home_land if str(f["n"]).lower() == home_country else other_land
        for poly in _polys(f):
            ring = poly[0]
            lons = [pt[0] for pt in ring]
            if max(lons) - min(lons) > 180:         # antimeridian-crossing polygons (Russia, Fiji) are skipped
                continue
            xy = [project(pt[1], pt[0]) for pt in ring]
            xs, ys = [p[0] for p in xy], [p[1] for p in xy]
            if max(xs) < 0 or min(xs) > W * SS or max(ys) < 0 or min(ys) > H * SS:
                continue
            d.polygon(xy, fill=fill, outline=border)
            for hole in poly[1:]:
                d.polygon([project(pt[1], pt[0]) for pt in hole], fill=sea, outline=border)

    # optional connecting line between the main place and listed places (not a claim about the road's path)
    if m.get("connect") and places:
        for p in places:
            a, b = project(main["lat"], main["lon"]), project(p["lat"], p["lon"])
            n = 40
            for i in range(0, n, 2):
                s0 = (a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)
                s1 = (a[0] + (b[0] - a[0]) * (i + 1) / n, a[1] + (b[1] - a[1]) * (i + 1) / n)
                d.line([s0, s1], fill=accent, width=7 * SS // 2)

    f_label = _font(46 * SS // 2)
    f_main = _font(58 * SS // 2)

    def pin(p, main_pin):
        x, y = project(p["lat"], p["lon"])
        r = (30 if main_pin else 20) * SS // 2
        if main_pin:
            d.ellipse((x - r * 2.2, y - r * 2.2, x + r * 2.2, y + r * 2.2), outline=_mix(accent, sea, .35), width=5 * SS // 2)
        d.ellipse((x - r, y - r, x + r, y + r), fill=accent, outline="white", width=6 * SS // 2)
        font = f_main if main_pin else f_label
        text = p["label"]
        if not text:
            return
        tb = d.textbbox((0, 0), text, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        # label sits to the right unless that would leave the safe margin, then to the left
        pad = 22 * SS // 2
        lx = x + r + 18 * SS // 2
        if lx + tw + 2 * pad > W * SS - 60 * SS:
            lx = x - r - 18 * SS // 2 - tw - 2 * pad
        ly = y - th // 2 - pad // 2
        d.rounded_rectangle((lx, ly - pad // 2, lx + tw + 2 * pad, ly + th + pad * 1.2), radius=20 * SS // 2, fill=_mix(sea, (0, 0, 0), .3))
        d.text((lx + pad, ly - tb[1]), text, font=font, fill="white")

    for p in places:
        pin(p, False)
    pin(main, True)

    out = img.resize((W, H), Image.Resampling.LANCZOS)
    credit = {"title": "Vector location map (Natural Earth)", "author": "Natural Earth", "license": "Public Domain",
              "rights": "Public Domain", "rights_statement": "Public Domain (Natural Earth terms of use)", "license_url": LICENSE_URL,
              "source_url": "https://www.naturalearthdata.com/", "asset_url": "https://www.naturalearthdata.com/",
              "provider": "Natural Earth (vendored)",
              "match_score": None, "period_ok": None, "warnings": [],
              "search_variant": f"vector map centred {clat:.2f},{clon:.2f} span {span}°"}
    if asset_dir is not None:
        path = Path(asset_dir) / f"scene_{idx:02d}_map.jpg"
        out.save(path, quality=94)
        credit["file"] = str(path)
        credit["content_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out, credit
