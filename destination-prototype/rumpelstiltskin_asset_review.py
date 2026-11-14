#!/usr/bin/env python3
"""Build a private, source-backed image review board for the Grimm pilot.

Candidate discovery, licensing, OCR, size/upscale, duplicate and CLIP gates are
delegated to the existing destination-prototype asset resolver. Nothing here
approves an asset or renders a video.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import asset_resolver as ar
import rumpelstiltskin_visual_policy as visual_policy

# These prototype modules are loaded explicitly above; do not leave their
# directory ahead of the repository root and shadow the root scene_planner.
if str(HERE) in sys.path:
    sys.path.remove(str(HERE))

SEEDS = [
    {"file_title": "Rumpelstiltskin at the Spinning-Wheel and the Dwarf MET DP827694.jpg",
     "scene_id": "beat-03-first-bargains",
     "source_url": "https://commons.wikimedia.org/wiki/File:Rumpelstiltskin_at_the_Spinning-Wheel_and_the_Dwarf_MET_DP827694.jpg"},
    {"file_title": "The Blue Fairy Book, p. 97.jpg",
     "scene_id": "beat-03-first-bargains",
     "source_url": "https://commons.wikimedia.org/wiki/File:The_Blue_Fairy_Book,_p._97.jpg"},
    {"file_title": "Andrew Lang - The Blue Fairy Book p.116.png",
     "scene_id": "beat-03-first-bargains",
     "source_url": "https://commons.wikimedia.org/wiki/File:Andrew_Lang_-_The_Blue_Fairy_Book_p.116.png"},
    {"file_title": "GrimmsGoblins-140-Rumpelstiltskin.jpg",
     "scene_id": "beat-09-foot-revelation",
     "source_url": "https://commons.wikimedia.org/wiki/File:GrimmsGoblins-140-Rumpelstiltskin.jpg"},
]

SEARCH_TERMS = {
    "beat-01-threat": ["Rumpelstiltskin threatened daughter spinning straw", "Rumpelstilzchen Müllerstochter Stroh Spinnrad", "Rumpel-Stilts-Kin miller's daughter Grimm"],
    "beat-02-boast": ["Rumpelstiltskin miller daughter Grimm illustration", "Rumpelstilzchen Müller Tochter illustration", "Grimm's Goblins 1876 Rumpel-Stilts-Kin"],
    "beat-03-first-bargains": ["Rumpelstiltskin spinning wheel dwarf necklace ring", "Rumpelstilzchen Spinnrad Zwerg Ring Halsband", "Gustav Heinrich Naeke Rumpelstiltskin spinning wheel", "George Cruikshank Grimm's Goblins Rumpel-Stilts-Kin"],
    "beat-04-king-demands": ["Rumpelstiltskin king chamber golden straw", "Rumpelstilzchen König Kammer goldenes Stroh", "Grimm Rumpel-Stilts-Kin Cruikshank 1876"],
    "beat-05-child-pact": ["Rumpelstiltskin first child bargain Grimm", "Rumpelstilzchen erstes Kind Versprechen", "Rumpel-Stilts-Kin miller's daughter first child"],
    "beat-06-claim": ["Rumpelstiltskin queen infant dwarf illustration", "Rumpelstilzchen Königin Kind Zwerg", "Grimm's Goblins Rumpel-Stilts-Kin 1876"],
    "beat-07-three-days": ["Rumpelstiltskin queen guesses name messenger", "Rumpelstilzchen Königin Namen Bote", "Rumpel-Stilts-Kin name Grimm Cruikshank"],
    "beat-08-forest-fire": ["Rumpelstiltskin dancing fire forest hut", "Rumpelstilzchen Feuer Waldhütte tanzend", "Rumpel-Stilts-Kin fire Grimm's Goblins"],
    "beat-09-foot-revelation": ["Rumpelstiltskin foot stuck floor ending", "Rumpelstilzchen Fuß Boden steckt Ende", "George Cruikshank Rumpel-Stilts-Kin Grimm's Goblins 1876"],
}
ALIASES = tuple(visual_policy.STORY_ALIASES)


def review_scene(raw: dict[str, Any], index: int, visual_mode: str = "exact") -> dict[str, Any]:
    sid = str(raw["id"])
    seed_files = [x["file_title"] for x in SEEDS if x["scene_id"] == sid]
    return visual_policy.build_review_scene(raw, index, visual_mode, seed_files)


def _font(size: int, bold: bool = False):
    candidates = (["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"] if bold else
                  ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _wrap(draw, text: str, font, width: int) -> list[str]:
    lines, current = [], ""
    for word in str(text).split():
        trial = (current + " " + word).strip()
        if current and draw.textbbox((0, 0), trial, font=font)[2] > width:
            lines.append(current); current = word
        else:
            current = trial
    if current: lines.append(current)
    return lines


def _story_role(candidate: dict[str, Any], beat_id: str, visual_mode: str = "symbolic") -> tuple[str, str]:
    role = visual_policy.classify_candidate(candidate, beat_id, visual_mode)
    labels = {
        "exact_story_illustration": "Exact Rumpelstiltskin story illustration — verify this beat visually",
        "symbolic_contextual_asset": "Fairy-tale illustration — scene-relevant symbolic candidate",
        "unrelated_contextual_art": "Rejected — unrelated or non-illustrative visual",
    }
    return role, labels[role]


def _candidate_cell(canvas: Image.Image, box: tuple[int, int, int, int], record: dict[str, Any], beat_title: str,
                    base_dir: Path):
    draw = ImageDraw.Draw(canvas)
    x, y, w, h = box
    draw.rounded_rectangle((x, y, x+w, y+h), radius=18, fill=(43, 38, 34), outline=(113, 92, 70), width=2)
    source = Path(record["file"])
    if not source.is_absolute():
        source = base_dir / source
    img_box = (x+16, y+16, w-32, 330)
    if source.is_file():
        with Image.open(source) as im:
            im = im.convert("RGB")
            im.thumbnail((img_box[2], img_box[3]), Image.Resampling.LANCZOS)  # never upscale for the board
            px = x + (w-im.width)//2
            py = y + 16 + (330-im.height)//2
            canvas.paste(im, (px, py))
    draw.text((x+18, y+354), beat_title[:62], font=_font(20, True), fill=(245, 231, 207))
    role = record.get("visual_role_label", "Candidate")
    draw.text((x+18, y+382), role, font=_font(16, True), fill=(226, 182, 111))
    lines = [f"Creator: {record.get('author') or 'Not stated'}",
             f"Original: {record.get('dimensions', ['?', '?'])[0]} × {record.get('dimensions', ['?', '?'])[1]} px",
             f"Rights: {record.get('rights_statement', 'unavailable')}",
             f"CLIP: {record.get('clip_score', 'not scored')} | approved: false"]
    for i, line in enumerate(lines):
        draw.text((x+18, y+408+i*22), line[:72], font=_font(14), fill=(225, 216, 199))
    src_lines = _wrap(draw, record.get("source_url", ""), _font(12), w-36)
    for i, line in enumerate(src_lines[:3]):
        draw.text((x+18, y+500+i*18), line, font=_font(12), fill=(178, 196, 201))


def create_contact_sheet(plan: dict, candidates_by_scene: dict[str, list[dict]],
                         seed_images: list[dict], out: Path,
                         rejected_by_scene: dict[str, list[dict]] | None = None,
                         unplaced_story_illustrations: list[dict] | None = None) -> Path:
    width, cell_w, cell_h, margin = 1600, 500, 570, 30
    rejected_by_scene = rejected_by_scene or {}
    unplaced_story_illustrations = unplaced_story_illustrations or []
    roles = [
        ("exact_story_illustration", "EXACT STORY ILLUSTRATIONS"),
        ("symbolic_contextual_asset", "SUITABLE SYMBOLIC ASSETS"),
    ]
    height = 160
    for scene in plan["scenes"]:
        height += 42
        group = candidates_by_scene.get(scene["id"], [])
        for role, _ in roles:
            count = min(6, sum(c.get("visual_role") == role and c.get("review_status") != "rejected_duplicate"
                               for c in group))
            height += 34 + (max(1, (count + 2) // 3) * cell_h if count else 54)
        rejected = rejected_by_scene.get(scene["id"], [])
        if rejected:
            height += 34 + min(5, len(rejected)) * 30
    if unplaced_story_illustrations:
        height += 75 + ((min(12, len(unplaced_story_illustrations)) + 2) // 3) * cell_h
    if seed_images:
        height += 55 + cell_h
    sheet = Image.new("RGB", (width, max(height, 800)), (24, 22, 21))
    draw = ImageDraw.Draw(sheet)
    draw.text((margin, 24), "RUMPELSTILTSKIN — STORY / SYMBOLIC ASSET REVIEW",
              font=_font(34, True), fill=(247, 231, 205))
    draw.text((margin, 76),
              "Exact story illustrations and beat-matched symbolic assets are separated. Every item remains unapproved.",
              font=_font(18), fill=(209, 192, 168))
    y = 132
    for scene in plan["scenes"]:
        sid = scene["id"]
        group = candidates_by_scene.get(sid, [])
        draw.text((margin, y), f"{sid} · {scene.get('title', sid)}",
                  font=_font(22, True), fill=(235, 197, 132))
        y += 38
        for role, heading in roles:
            records = [c for c in group if c.get("visual_role") == role
                       and c.get("review_status") != "rejected_duplicate"]
            draw.text((margin, y), heading, font=_font(17, True), fill=(210, 186, 148))
            y += 28
            if not records:
                draw.text((margin+14, y+8), "No asset passed this category's evidence and automatic gates.",
                          font=_font(15), fill=(170, 163, 152))
                y += 50
                continue
            for offset in range(0, min(len(records), 6), 3):
                row = records[offset:offset+3]
                for j, candidate in enumerate(row):
                    _candidate_cell(sheet, (margin + j*(cell_w+20), y, cell_w, cell_h-16),
                                    candidate, scene.get("title", sid), out.parent)
                y += cell_h
        rejected = rejected_by_scene.get(sid, [])
        if rejected:
            draw.text((margin, y), "REJECTED / UNCERTAIN SEARCH RESULTS (not candidates)",
                      font=_font(16, True), fill=(210, 132, 120))
            y += 25
            for item in rejected[:5]:
                label = f"{item.get('visual_role', 'unverified_contextual_art')}: {item.get('title', 'Untitled')} — {item.get('reason', 'metadata gate')}"
                draw.text((margin+14, y), label[:180], font=_font(13), fill=(193, 174, 165))
                y += 30
        y += 8
    if unplaced_story_illustrations:
        draw.text((margin, y), "STORY-IDENTIFIED ILLUSTRATIONS — BEAT PLACEMENT NOT VERIFIED",
                  font=_font(20, True), fill=(235, 197, 132))
        y += 38
        for offset in range(0, min(len(unplaced_story_illustrations), 12), 3):
            row = unplaced_story_illustrations[offset:offset+3]
            for j, candidate in enumerate(row):
                _candidate_cell(sheet, (margin + j*(cell_w+20), y, cell_w, cell_h-16),
                                candidate, "Story identified; specific beat not verified", out.parent)
            y += cell_h
    if seed_images:
        draw.text((margin, y), "KNOWN SEED CHECKS — not automatically accepted or counted as beat coverage",
                  font=_font(20, True), fill=(235, 197, 132))
        y += 38
        for j, record in enumerate(seed_images[:3]):
            if record.get("file") and Path(record["file"]).is_file():
                _candidate_cell(sheet, (margin + j*(cell_w+20), y, cell_w, cell_h-16),
                                record, record.get("visual_role_label", "Known Commons seed"), out.parent)
                draw.text((margin + j*(cell_w+20)+18, y+540),
                          record.get("review_status", "review"), font=_font(12, True),
                          fill=(239, 137, 119))
        y += cell_h
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.crop((0, 0, width, min(y + 20, sheet.height))).save(out, quality=92)
    return out


def _seed_review(seed: dict, out: Path, config: dict) -> dict:
    base = {"seed_file_title": seed["file_title"], "scene_id": seed["scene_id"],
            "source_url": seed["source_url"], "approved": False}
    try:
        c = ar._commons_file_candidate(seed["file_title"])
        if not c:
            return {**base, "review_status": "rejected_or_uncertain_rights_metadata",
                    "rights_status": "not accepted by existing rights gate"}
        base.update({"title": c["title"], "author": c["author"], "publication_date": c.get("date_text", ""),
                     "image_url": c["image_url"], "rights_statement": c["rights_statement"],
                     "rights_url": c["rights_url"], "original_dimensions": [c.get("width"), c.get("height")],
                     "categories": c.get("categories", []),
                     "rights_status": "passed existing resolver rights gate"})
        data = ar._download(c)
        digest = hashlib.sha256(data).hexdigest()
        from io import BytesIO
        with Image.open(BytesIO(data)) as im:
            im.verify()
        with Image.open(BytesIO(data)) as im:
            dims = list(im.size)
            decoded = hashlib.sha256(im.convert("RGB").tobytes()).hexdigest()
            size_ok = ar._size_ok(im, config["asset_resolver"])
            suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "TIFF": ".tif"}.get(im.format, ".img")
        dest_dir = out / "seed_candidates"
        dest_dir.mkdir(parents=True, exist_ok=True)
        file_path = dest_dir / (digest[:16] + suffix)
        file_path.write_bytes(data)
        rights_ok = ar._rights_ok(c["rights_statement"]) and (not ar._needs_attribution(c["rights_statement"]) or ar._has_author(c.get("author", "")))
        status = ("eligible_seed_pending_scene_relevance" if size_ok else "rejected_source_resolution") if rights_ok else "rejected_or_uncertain_rights"
        role, label = _story_role(
            {**base, "visual_metadata": {"categories": c.get("categories", [])}},
            seed["scene_id"], "exact",
        )
        return {**base, "file": str(file_path), "dimensions": dims, "file_sha256": digest,
                "decoded_pixels_sha256": decoded, "byte_size": len(data),
                "resolution_status": "passed" if size_ok else "rejected: source below 1920×1080 requirement",
                "rights_status": "passed existing resolver rights gate" if rights_ok else "rejected or uncertain under existing rights gate",
                "review_status": status, "rights_statement": c["rights_statement"],
                "visual_role": role, "visual_role_label": label, "approved": False}
    except Exception as exc:
        return {**base, "review_status": "uncertain_download_or_decode",
                "error": f"{type(exc).__name__}: {exc}"}


def run(plan_path: Path, out: Path) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    out = out.resolve()
    resolver_cfg = {"providers": list(ar.DEFAULT_PROVIDERS), "max_queries": 8,
                    "max_queries_per_provider": 3, "max_downloads": 24,
                    "top_candidates": 3, "min_short_side": 1080,
                    "min_long_side": 1920, "max_upscale": 1.25,
                    "review_candidates_dir": str((out / "candidate-files").resolve())}
    config = {"name": "The Grimm tale Rumpelstiltskin", "asset_resolver": resolver_cfg,
              "image_banned_terms": ["book cover", "title page", "frontispiece", "scanned page",
                                     "text page", "page scan", "catalogue page", "exhibition poster",
                                     "plate list", "contents page", "pdf", "djvu", "document scan",
                                     "report cover"]}
    seed_images = [_seed_review(seed, out, config) for seed in SEEDS]
    results, candidates_by_scene, rejected_by_scene = [], {}, {}
    unplaced_story_illustrations: list[dict[str, Any]] = []
    all_sources = {"provider": set(), "rights": set(), "size": set(), "clip": set()}
    rejected, resolver_errors = [], []

    for index, raw in enumerate(plan.get("scenes", []), 1):
        sid = str(raw["id"])
        beat_candidates: list[dict[str, Any]] = []
        beat_rejections: list[dict[str, Any]] = []
        diagnostics_by_mode: dict[str, Any] = {}
        errors_by_mode: dict[str, str] = {}
        queries_by_mode: dict[str, list[str]] = {}
        # A scene gets its own duplicate set. A story illustration discovered
        # while searching one beat must remain eligible for the beat it depicts.
        scene_used: set[str] = set()

        # Run both searches for every beat. Exact tale illustrations often
        # appear in several search results; global deduplication later assigns
        # each one to its strongest scene. Symbolic storybook imagery must still
        # be searched for the other beats so one illustration cannot leave them blank.
        for visual_mode in ("exact", "symbolic"):
            scene = review_scene(raw, index, visual_mode)
            queries_by_mode[visual_mode] = scene.get("image_search_terms", [])
            try:
                resolver_preview_dir = out / "resolver-jpegs" / scene["id"]
                resolver_preview_dir.mkdir(parents=True, exist_ok=True)
                _, resolved = ar.resolve_scene_image(scene, index, config,
                    resolver_preview_dir, scene_used, out)
                diag = resolved.get("diagnostics", {})
                diagnostics_by_mode[visual_mode] = diag
                for key, field in (("provider", "provider_source_urls"),
                                   ("rights", "rights_pass_source_urls"),
                                   ("size", "size_pass_source_urls"),
                                   ("clip", "clip_pass_source_urls")):
                    all_sources[key].update(diag.get(field, []))

                for candidate in resolved.get("review_candidates", []):
                    candidate["file"] = str(Path(candidate["file"]).relative_to(out.resolve()))
                    role, label = _story_role(candidate, sid, visual_mode)
                    candidate.update({
                        "story_beat_id": sid,
                        "visual_mode": visual_mode,
                        "visual_role": role,
                        "visual_role_label": label,
                        "beat_title": raw.get("title", sid),
                        "approved": False,
                    })
                    if role == "exact_story_illustration":
                        scene_status, missing_scene_terms = visual_policy.exact_scene_match_status(candidate, sid)
                        candidate["missing_scene_metadata_groups"] = missing_scene_terms
                        if scene_status in ("metadata_scene_match", "curated_story_beat_match"):
                            candidate["scene_match_status"] = scene_status
                        else:
                            # The resolver has already ranked this image against
                            # this beat's visual prompt and passed its CLIP gate.
                            # Keep it as a story-specific review candidate rather
                            # than discarding it for sparse catalogue metadata.
                            candidate["scene_match_status"] = "clip_ranked_human_review"
                            candidate["placement_note"] = (
                                "The source identifies Rumpelstiltskin and CLIP ranked this image for this beat; "
                                "a human must confirm the depicted moment before approval."
                            )
                        beat_candidates.append(candidate)
                    elif role == "symbolic_contextual_asset" and visual_mode == "symbolic":
                        beat_candidates.append(candidate)
                    else:
                        item = {
                            "scene_id": sid,
                            "visual_mode": visual_mode,
                            "visual_role": "unrelated_contextual_art",
                            "reason": "candidate metadata did not establish story identity or all beat-specific symbolic requirements",
                            "title": candidate.get("title", ""),
                            "source_url": candidate.get("source_url", ""),
                            "clip_score": candidate.get("clip_score"),
                            "approved": False,
                        }
                        beat_rejections.append(item)
                        rejected.append(item)

            except Exception as exc:
                diag = getattr(exc, "diagnostics", {})
                diagnostics_by_mode[visual_mode] = diag if isinstance(diag, dict) else {}
                errors_by_mode[visual_mode] = f"{type(exc).__name__}: {exc}"
                if isinstance(diag, dict):
                    for key, field in (("provider", "provider_source_urls"),
                                       ("rights", "rights_pass_source_urls"),
                                       ("size", "size_pass_source_urls"),
                                       ("clip", "clip_pass_source_urls")):
                        all_sources[key].update(diag.get(field, []))
                    for item in diag.get("top_rejected", []):
                        marker_role = visual_policy.classify_candidate(
                            {"title": item.get("title", ""),
                             "description": item.get("checked_text", "")}, sid)
                        entry = {
                            "scene_id": sid,
                            "visual_mode": visual_mode,
                            **item,
                            "visual_role": marker_role,
                        }
                        beat_rejections.append(entry)
                        rejected.append(entry)
                    resolver_errors.extend({"scene_id": sid, "visual_mode": visual_mode,
                                             "provider": k, "error": v}
                                            for k, v in diag.get("provider_errors", {}).items())

        result = {
            "scene_id": sid,
            "beat_title": raw.get("title", sid),
            "queries_by_mode": queries_by_mode,
            "status": "candidates_available" if beat_candidates else "no_usable_visual",
            "approved": False,
            "candidates": beat_candidates,
            "diagnostics_by_mode": diagnostics_by_mode,
            "errors_by_mode": errors_by_mode,
            "rejected_or_uncertain": beat_rejections,
        }
        candidates_by_scene[sid] = beat_candidates
        rejected_by_scene[sid] = beat_rejections
        results.append(result)

    # Move inspected story illustrations to their known story beat even
    # when a broad exact-story query discovered them while searching another
    # beat. Discovery order must never determine storyboard placement.
    results_by_id = {result["scene_id"]: result for result in results}
    for result in results:
        retained = []
        for candidate in result["candidates"]:
            target_id = visual_policy.curated_beat_for_candidate(candidate)
            if (
                candidate.get("visual_role") == "exact_story_illustration"
                and target_id
                and target_id != result["scene_id"]
                and target_id in results_by_id
            ):
                target_result = results_by_id[target_id]
                candidate["discovered_scene_id"] = candidate.get("story_beat_id") or result["scene_id"]
                candidate["story_beat_id"] = target_id
                candidate["scene_id"] = target_id
                candidate["beat_title"] = target_result["beat_title"]
                candidate["scene_match_status"] = "curated_story_beat_match"
                candidate["missing_scene_metadata_groups"] = []
                candidate["placement_note"] = (
                    "Placed using the source's known Rumpelstiltskin illustration identity; "
                    "human review must still confirm that this image suits the beat."
                )
                target_result["candidates"].append(candidate)
                target_result["status"] = "candidates_available"
            else:
                retained.append(candidate)
        result["candidates"] = retained
        candidates_by_scene[result["scene_id"]] = retained

    # Assign a repeated illustration to the beat with the strongest evidence.
    # Curated/source-metadata placement wins first; otherwise use the per-beat
    # CLIP score. This prevents the first search result from stealing an image
    # from the scene it actually depicts.
    def visual_key(candidate: dict[str, Any]) -> str:
        return str(candidate.get("decoded_pixels_sha256") or candidate.get("source_url") or
                   candidate.get("candidate_id") or "")

    def placement_rank(candidate: dict[str, Any]) -> tuple[int, float, int]:
        status = candidate.get("scene_match_status")
        confidence = 2 if status in ("metadata_scene_match", "curated_story_beat_match") else 1
        score = float(candidate.get("clip_score") or 0.0)
        rank = -int(candidate.get("rank") or 99)
        return confidence, score, rank

    best_by_visual: dict[str, dict[str, Any]] = {}
    for result in results:
        for candidate in result["candidates"]:
            key = visual_key(candidate)
            if not key:
                continue
            previous = best_by_visual.get(key)
            if previous is None or placement_rank(candidate) > placement_rank(previous):
                best_by_visual[key] = candidate

    for result in results:
        retained = []
        for candidate in result["candidates"]:
            key = visual_key(candidate)
            winner = best_by_visual.get(key) if key else candidate
            if winner is not candidate:
                candidate["review_status"] = "rejected_duplicate"
                candidate["duplicate_of"] = winner.get("candidate_id") or winner.get("source_url")
                entry = {
                    "scene_id": result["scene_id"],
                    "visual_role": candidate.get("visual_role"),
                    "reason": "same image assigned to its strongest-scoring or curated story beat",
                    "title": candidate.get("title", ""),
                    "source_url": candidate.get("source_url", ""),
                    "assigned_to": winner.get("story_beat_id"),
                    "approved": False,
                }
                rejected.append(entry)
                rejected_by_scene[result["scene_id"]].append(entry)
                continue
            retained.append(candidate)
        result["candidates"] = retained
        candidates_by_scene[result["scene_id"]] = retained

    for seed in seed_images:
        matching = next((c for r in results for c in r["candidates"]
                         if c.get("source_url") == seed.get("source_url")), None)
        if matching:
            seed["relevance_status"] = "passed CLIP and visual metadata checks in " + matching["story_beat_id"]
        else:
            seed["relevance_status"] = "seed only; not counted as beat coverage without passing resolver CLIP and visual checks"

    coverage = visual_policy.summarize_visual_coverage(results)
    uncovered_ids = coverage["uncovered_beat_ids"]
    summary = {
        "beats": len(results),
        **coverage,
        "assets_found_unique_source_pages": len(all_sources["provider"]),
        "unique_source_pages_passed_rights_and_attribution": len(all_sources["rights"]),
        "unique_source_pages_passed_resolution_format_ocr_and_duplicate_gates": len(all_sources["size"]),
        "unique_source_pages_passed_clip_relevance_threshold": len(all_sources["clip"]),
        "unique_candidate_visual_files_for_human_review": sum(len(r["candidates"]) for r in results) + len(unplaced_story_illustrations),
        "story_identified_illustrations_without_verified_beat_placement": len(unplaced_story_illustrations),
        "unplaced_story_illustration_titles": list(dict.fromkeys(str(c.get("title") or c.get("candidate_title") or "Untitled") for c in unplaced_story_illustrations)),
        "unrelated_or_uncertain_search_results_rejected": len(rejected),
        "all_candidates_approved": False,
        "clip_threshold": 0.22,
        "source_size_gate": "short side >= 1080 px; long side >= 1920 px; existing max-upscale <= 1.25; no enlargement performed",
        "coverage_interpretation": "Story-identified illustrations are assigned to their strongest scene by curated source mapping or per-beat CLIP ranking. Symbolic visuals must pass a scene-relevant metadata anchor, illustrated-art style gate, CLIP relevance and all existing technical gates. Every item remains unapproved.",
        "recommendation": (
            "Some beats still need story-specific or symbolic artwork; review all candidates and fill any uncovered beats."
            if uncovered_ids else
            "Every beat has at least one story-specific or scene-relevant illustrated candidate; confirm each placement and approve the chosen images."
        ),
    }
    manifest = {
        "project": plan.get("series"),
        "episode": plan.get("title"),
        "source_story_edition": plan.get("version_scope"),
        "summary": summary,
        "provider_scope": [
            "Wikimedia Commons official API (including edition-specific story categories)",
            "The Metropolitan Museum of Art Open Access API",
            "Art Institute of Chicago Open Access",
            "Cleveland Museum of Art Open Access",
            "National Gallery of Art API",
            "Library of Congress API",
        ],
        "visual_policy": {
            "exact_story_illustration": "Rumpelstiltskin/Rumpelstilzchen or a documented historical title alias must appear in source metadata/categories. Beat placement uses explicit curated source mapping, matching scene metadata, or per-beat CLIP ranking; human confirmation is still required.",
            "symbolic_contextual_asset": "Must match a scene-relevant object, setting or mood anchor, pass the fairytale illustration-vs-photograph CLIP gate, and pass all existing technical gates; it is contextual atmosphere, not proof of a literal story scene.",
            "unrelated_contextual_art": "Fails story identity, illustrated-art style, or beat relevance; excluded from coverage.",
            "unverified_story_identity": "Metadata without a story identity anchor is rejected from exact coverage even when CLIP passes.",
        },
        "seeds": seed_images,
        "unplaced_story_illustrations": unplaced_story_illustrations,
        "beats": results,
        "rejected_or_uncertain": rejected,
        "provider_errors": resolver_errors,
        "review_rules": {
            "approved": False,
            "manual_approval_required": True,
            "licensing": "existing asset_resolver rights allowlist and attribution gate unchanged",
            "clip": "existing score/document/period thresholds unchanged",
            "resolution": "existing source-size/upscale gate unchanged; no enlargement",
            "ocr": "existing OCR/text-heavy gate retained",
            "duplicates": "deduplicated by decoded RGB pixel hash across all beats",
        },
    }
    (out / "provenance-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    create_contact_sheet(plan, candidates_by_scene, seed_images,
                         out / "contact-sheet.jpg", rejected_by_scene,
                         unplaced_story_illustrations)

    report = [
        "# Rumpelstiltskin visual coverage report",
        "",
        f"- Beats with exact story illustrations: **{coverage['beats_with_exact_story_illustrations']}/{len(results)}**",
        f"- Story-specific candidates awaiting visual placement review: **{coverage['beats_with_story_illustration_candidates_for_human_placement']}/{len(results)}**",
        f"- Beats with suitable symbolic assets: **{coverage['beats_with_suitable_symbolic_assets']}/{len(results)}**",
        f"- Beats with any automatic candidates for human review: **{coverage['beats_with_any_automatic_candidates']}/{len(results)}**",
        f"- Beats lacking a usable visual: **{coverage['beats_lacking_usable_visual']}/{len(results)}**",
        "",
        "## Exact story illustration beats",
        *([f"- {x}" for x in coverage["exact_story_illustration_beat_ids"]] or ["- None"]),
        "",
        "## Symbolic asset beats",
        *([f"- {x}" for x in coverage["symbolic_asset_beat_ids"]] or ["- None"]),
        "",
        "## Story-identified images not assigned to a beat",
        *([f"- {x.get('title', 'Untitled')} — {', '.join(x.get('missing_scene_metadata_groups', []))}"
           for x in unplaced_story_illustrations] or ["- None"]),
        "",
        "## Uncovered beats",
        *([f"- {x}" for x in uncovered_ids] or ["- None"]),
        "",
        "No asset is approved. Do not render until a human approves relevant, provenance-complete visuals for every beat.",
        "",
        summary["recommendation"],
    ]
    (out / "coverage-report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    (out / "README.txt").write_text(
        "Review artifact only. No image is approved and no MP4 was rendered.\n"
        "Exact story illustrations require a Rumpelstiltskin story identity anchor in source metadata; beat placement is curated or CLIP-ranked and still requires human confirmation.\n"
        "Symbolic assets can be atmospheric or object-led; they must match a scene anchor, pass the illustrated-art style gate, and pass existing CLIP and technical gates.\n"
        "Unrelated, unverified, low-resolution, text-heavy, duplicate, rights-failing, or CLIP-failing material is rejected or reported as uncertain.\n"
        "See provenance-manifest.json and coverage-report.md for honest counts and source provenance.\n",
        encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = run(args.plan, args.output)
    print(json.dumps(manifest["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
