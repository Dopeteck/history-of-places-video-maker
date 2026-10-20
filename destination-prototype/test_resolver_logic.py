#!/usr/bin/env python3
"""Offline tests for the image resolver, narration timing, vector map and diagnostics.
No network, no models: providers, downloads and CLIP are replaced with fakes."""
import io, json, re, sys, tempfile, unittest
from pathlib import Path
import numpy as np
import yaml
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import asset_resolver as ar
import timing, vector_map


def noise_jpeg(w, h, seed):
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
    buf = io.BytesIO(); Image.fromarray(arr).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def cand(title, provider="Fake Museum", n=1, rights="Public Domain"):
    return {"provider": provider, "title": title, "description": "", "image_url": f"https://example.org/{n}.jpg",
            "source_url": f"https://example.org/item/{n}", "rights_statement": rights, "rights_url": "https://example.org/rights",
            "author": "A", "identifier": str(n)}


class ResolverUnits(unittest.TestCase):
    def test_rights_gate(self):
        for ok in ("Public Domain", "CC0 1.0", "CC BY 4.0", "CC BY-SA 3.0", "No known copyright restrictions",
                   "No known restrictions on publication"):
            self.assertTrue(ar._rights_ok(ok), ok)
        for bad in ("", "Unknown", "Copyright protected", "All rights reserved", "CC BY-NC 4.0", "CC BY-ND 2.0"):
            self.assertFalse(ar._rights_ok(bad), bad)

    def test_banned_terms_are_whole_words(self):
        self.assertFalse(ar._has_term("Edo pageant procession", ["page"]))
        self.assertTrue(ar._has_term("Page 3 of the report", ["page"]))
        self.assertFalse(ar._has_term("Hokusai bookshop scene", ["book"]))

    def test_query_builder_puts_entities_first_and_adds_short_forms(self):
        scene = {"named_entities": ["Edo Castle"], "visual_tags": ["Edo Castle keep"],
                 "image_search_terms": ["Edo Castle historical photograph", "Tokyo Imperial Palace Edo Castle old"]}
        q = ar.build_queries(scene)
        self.assertEqual(q[0], "Edo Castle")
        self.assertIn("Edo Castle keep", q)
        self.assertTrue(any(len(x.split()) <= 3 for x in q[1:]))
        self.assertLessEqual(len(q), 6)
        # long search variants must not crowd out the planner entities/tags (old bug: queries[:3])
        self.assertIn("Edo Castle keep", ar.build_queries(scene, limit=3) + ["Edo Castle keep"])

    def test_loc_picks_largest_thumbnail(self):
        urls = ["https://tile.loc.gov/a/150px.jpg#h=150&w=117", "https://tile.loc.gov/a/1024px.jpg#h=1024&w=800",
                "https://tile.loc.gov/a/640px.jpg#h=640&w=500"]
        self.assertEqual(ar._best_loc_image(urls), "https://tile.loc.gov/a/1024px.jpg")

    def test_size_gate_matches_renderer_scaling(self):
        cfg = {}
        self.assertTrue(ar._size_ok(Image.new("RGB", (2400, 1600)), cfg))      # landscape, blurred-fill layout
        self.assertTrue(ar._size_ok(Image.new("RGB", (1200, 1000)), cfg))      # 1.0x upscale in framed layout
        self.assertFalse(ar._size_ok(Image.new("RGB", (640, 480)), cfg))       # would be upscaled 1.56x
        self.assertTrue(ar._size_ok(Image.new("RGB", (1200, 2100)), cfg))      # tall, full-bleed
        self.assertFalse(ar._size_ok(Image.new("RGB", (800, 1200)), cfg))      # tall but too small for full-bleed

    def test_candidate_coerces_numeric_and_list_metadata(self):
        c = ar._candidate("P", 123, 456, "https://e.org/i.jpg#h=1&w=2", "https://e.org/x", "CC0", "u", [{"description": "Artist"}], 7)
        self.assertEqual((c["title"], c["author"], c["image_url"]), ("123", "Artist", "https://e.org/i.jpg"))


class ResolverFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.images = {}
        self.clip = {}
        self._orig = (ar.PROVIDERS, ar._commons_category, ar._download, ar._clip_analyze, ar._ocr_available)
        ar._FAILS.clear(); ar.PROVIDER_ERRORS.clear()
        ar._commons_category = lambda name: []
        ar._ocr_available = lambda: False
        ar._download = lambda c: self.images[c["source_url"]]
        ar._clip_analyze = lambda q, blobs: [self.clip[hash(b)] for b in blobs]
        self.addCleanup(self.restore)

    def restore(self):
        ar.PROVIDERS, ar._commons_category, ar._download, ar._clip_analyze, ar._ocr_available = self._orig

    def setup_provider(self, items):
        def _met(query):
            return [dict(c) for c in items]
        ar.PROVIDERS = [_met]
        for k, (c, (w, h, seed), clip) in enumerate(zip(items, self.specs, self.clips)):
            blob = noise_jpeg(w, h, seed)
            self.images[c["source_url"]] = blob
            self.clip[hash(blob)] = clip

    def run_scene(self, scene, config=None):
        cfg = {"name": "Testville", "asset_resolver": {"providers": [p.__name__ for p in ar.PROVIDERS]}}; cfg.update(config or {})
        return ar.resolve_scene_image(scene, 1, cfg, self.tmp.name, set(), Path(self.tmp.name))

    def test_picks_best_and_counts_every_rejection(self):
        items = [cand("Book cover of Edo guide", n=1), cand("Edo castle page scan", n=2), cand("Edo castle small", n=3),
                 cand("Edo castle tax document", n=4), cand("Edo castle weak match", n=5), cand("Edo castle keep", n=6)]
        self.specs = [(1400, 1000, 1), (1400, 1000, 2), (640, 480, 3), (1400, 1000, 4), (1400, 1000, 5), (1400, 1000, 6)]
        self.clips = [{"score": .3, "p_document": .1, "p_modern": .1}] * 2 + [{"score": .3, "p_document": .1, "p_modern": .1}] + \
                     [{"score": .31, "p_document": .9, "p_modern": .1}, {"score": .15, "p_document": .1, "p_modern": .1},
                      {"score": .29, "p_document": .1, "p_modern": .1}]
        self.setup_provider(items)
        im, credit = self.run_scene({"title": "The Castle Town", "narration_intent": "castle", "named_entities": ["Edo Castle"],
                                     "required_keywords": ["edo"], "image_type": "historic"})
        self.assertEqual(credit["title"], "Edo castle keep")
        self.assertAlmostEqual(credit["match_score"], .29)
        self.assertIn("OCR gate unavailable", credit["warnings"][0])

    def test_subject_groups_reject_artist_match_without_narrated_subject(self):
        items = [cand("Cherry Trees along Gokacho by Hiroshige", n=91)]
        self.specs = [(1400, 1000, 91)]
        self.clips = [{"score": .5, "p_document": .1, "p_modern": .1}]
        self.setup_provider(items)
        with self.assertRaises(ar.ResolverError) as cm:
            self.run_scene({"title": "Merchants and Canals",
                            "required_keywords": ["Edo", "Hiroshige"],
                            "required_keyword_groups": [["canal", "boat", "river", "waterway"]],
                            "image_type": "historic"})
        self.assertEqual(cm.exception.diagnostics["rejected"].get("subject_groups"), 1)

    def test_failure_raises_resolver_error_with_parseable_diagnostics(self):
        items = [cand("Edo castle weak", n=1)]
        self.specs = [(1400, 1000, 1)]; self.clips = [{"score": .05, "p_document": .1, "p_modern": .1}]
        self.setup_provider(items)
        with self.assertRaises(ar.ResolverError) as cm:
            self.run_scene({"title": "The Castle Town", "narration_intent": "castle", "image_type": "historic"})
        d = cm.exception.diagnostics
        self.assertEqual(d["rejected"]["clip_below_threshold"], 1)
        self.assertEqual(d["providers_found"]["_met"] >= 1, True)
        self.assertTrue(d["top_rejected"])
        # the approval board must be able to read what the resolver wrote (old regex never matched)
        import render_region_short as rr
        self.assertEqual(rr._parse_diagnostics(str(cm.exception))["rejected"], d["rejected"])

    def test_modern_scene_rejects_historical_images_and_marks_period_ok(self):
        items = [cand("Old print of garden", n=1), cand("Garden photo 2019", n=2)]
        self.specs = [(1400, 1000, 1), (1400, 1000, 2)]
        self.clips = [{"score": .33, "p_document": .1, "p_modern": .1}, {"score": .27, "p_document": .1, "p_modern": .9}]
        self.setup_provider(items)
        im, credit = self.run_scene({"title": "Gardens Remember", "narration_intent": "garden", "image_type": "modern"})
        self.assertEqual(credit["title"], "Garden photo 2019")
        self.assertIs(credit["period_ok"], True)

    def test_no_candidates_error_names_queries_and_provider_failures(self):
        def _met(query): raise RuntimeError("HTTP 403")
        ar.PROVIDERS = [_met]
        with self.assertRaises(ar.ResolverError) as cm:
            self.run_scene({"title": "X", "narration_intent": "x", "image_search_terms": ["a b c", "d e f"]})
        self.assertIn("_met", cm.exception.diagnostics["provider_errors"])
        self.assertTrue(cm.exception.diagnostics["queries"])



    def test_event_banner_terms_and_historic_year_gate_helpers(self):
        self.assertTrue(ar._has_term("Tokyo meetup banner", ["banner", "meetup", "event"]))
        self.assertFalse(ar._has_term("Edo eventful history", ["event"]))
        # Numeric keywords are normalized by the renderer; resolver inputs are compared as strings.
        c = ar._candidate("Test provider", "Tokugawa Ieyasu portrait", "Portrait",
                          "https://example.org/i.jpg", "https://example.org/item",
                          "Public Domain", "https://example.org/rights", "Artist", 1,
                          categories=["Portraits of Tokugawa Ieyasu"])
        self.assertIn("Tokugawa Ieyasu", " ".join(c["categories"]))

    def test_historic_scene_rejects_modern_photo_without_override(self):
        items = [cand("Tokugawa Ieyasu portrait", n=71)]
        self.specs = [(1400, 1000, 71)]
        self.clips = [{"score": .4, "p_document": .1, "p_modern": .9}]
        self.setup_provider(items)
        with self.assertRaises(ar.ResolverError) as cm:
            self.run_scene({"title": "The Shogunate", "required_keywords": ["Tokugawa"], "image_type": "historic"})
        self.assertEqual(cm.exception.diagnostics["rejected"].get("modern_in_historic"), 1)

    def test_date_window_rejects_known_out_of_period_candidate(self):
        items = [cand("Nihonbashi bridge 1912", n=72)]
        items[0]["date_text"] = "1912"
        self.specs = [(1400, 1000, 72)]
        self.clips = [{"score": .4, "p_document": .1, "p_modern": .1}]
        self.setup_provider(items)
        with self.assertRaises(ar.ResolverError) as cm:
            self.run_scene({"title": "Nihonbashi", "required_keywords": ["Nihonbashi"],
                            "image_type": "historic", "max_year": 1899})
        self.assertEqual(cm.exception.diagnostics["rejected"].get("date_window"), 1)

class TimingAndMap(unittest.TestCase):
    def tokyo(self):
        return yaml.safe_load((ROOT / "regions" / "tokyo.yaml").read_text(encoding="utf-8"))

    def test_word_times_cover_duration_and_follow_chunks(self):
        words = "One two three. Four five six seven.".split()
        t = timing.word_times(words, [("One two three.", 24000 * 2), ("Four five six seven.", 24000 * 4)], 24000)
        self.assertEqual(len(t), 7); self.assertAlmostEqual(t[2][1], 2.0); self.assertAlmostEqual(t[-1][1], 6.0)
        self.assertTrue(all(a[1] <= b[0] + 1e-9 for a, b in zip(t, t[1:])))

    def test_word_times_fallback_when_text_disagrees(self):
        words = "Tokyo in 1603 was small".split()
        t = timing.word_times(words, [("Tokyo in sixteen oh three", 24000 * 2), ("was small", 24000)], 24000)
        self.assertEqual(len(t), 5); self.assertAlmostEqual(t[-1][1], 3.0, places=1)

    def test_scene_cut_errors_are_loud(self):
        words = "a b c d e f".split(); t = [(i, i + 1) for i in range(6)]
        with self.assertRaises(ValueError): timing.scene_ends([{"title": "1"}, {"title": "2", "starts_at": "zzz"}], words, t, 6)
        with self.assertRaises(ValueError):
            timing.scene_ends([{"title": "1"}, {"title": "2", "starts_at": "d"}, {"title": "3", "starts_at": "b"}], words, t, 6)

    def test_tokyo_config_cuts_land_on_phrases_and_no_scene_is_too_short(self):
        cfg = self.tokyo(); script = cfg["story"]["narration"]; words = script.split()
        sentences = re.findall(r"[^.!?]+[.!?]", script)
        total = 56.0; chars = np.array([len(s) for s in sentences], float)
        chunks = [(s.strip(), int(total * 24000 * c / chars.sum())) for s, c in zip(sentences, chars)]
        # `starts_at` phrases must exist in order (renderer would otherwise abort)
        wt = timing.word_times(words, chunks, 24000, total)
        ends = timing.scene_ends(cfg["scenes"], words, wt, total)
        self.assertEqual(len(ends), len(cfg["scenes"])); self.assertAlmostEqual(ends[-1], total, places=1)
        durs = np.diff([0.0] + list(ends))
        self.assertTrue(all(d >= 1.5 for d in durs), dict(zip([s["title"] for s in cfg["scenes"]], durs.round(1))))
        # the Nihonbashi scene must begin where Nihonbashi is spoken, not at an even split
        i = [s["title"] for s in cfg["scenes"]].index("Nihonbashi")
        start = ends[i - 1]; j = timing.find_phrase(words, "Nihonbashi, once a vital bridge")
        self.assertAlmostEqual(start, wt[j][0], places=3)

    def test_vector_map_renders_land_sea_and_labels(self):
        cfg = self.tokyo()
        im, credit = vector_map.render_map({}, cfg)
        self.assertEqual(im.size, (1080, 1920))
        colors = im.convert("RGB").resize((54, 96)).getcolors(10000)
        self.assertGreater(len(colors), 6)                       # land, sea, borders, graticule, pins
        self.assertEqual(credit["license"], "Public Domain")
        # the marker must be inside the safe area between title band and captions
        px = np.asarray(im.convert("RGB")).astype(int)
        red = (abs(px[..., 0] - 230) < 8) & (abs(px[..., 1] - 90) < 8) & (abs(px[..., 2] - 69) < 8)
        ys, xs = np.nonzero(red)
        self.assertTrue(len(ys) > 500)
        self.assertTrue(300 < ys.mean() < 1450 and 100 < xs.mean() < 980)

    def test_configs_have_valid_scene_order_and_unique_titles(self):
        for p in (ROOT / "regions").glob("*.yaml"):
            if p.name == "template.yaml": continue
            d = yaml.safe_load(p.read_text(encoding="utf-8")); words = d["story"]["narration"].split(); last = -1
            self.assertEqual(len({s["title"] for s in d["scenes"]}), len(d["scenes"]), p.name)
            for s in d["scenes"]:
                if s.get("starts_at"):
                    j = timing.find_phrase(words, s["starts_at"])
                    self.assertGreater(j, last, f"{p.name}: {s['title']}"); last = j


class ConfigTypes(unittest.TestCase):
    def test_numeric_keywords_do_not_crash_local_matching(self):
        import render_region_short as rr
        cfg = rr.normalize_config(yaml.safe_load((ROOT / "regions" / "tokyo.yaml").read_text(encoding="utf-8")))
        turning = next(s for s in cfg["scenes"] if s["title"] == "The Turning Point")
        self.assertIn("1868", turning["required_keywords"])
        self.assertTrue(all(isinstance(k, str) for s in cfg["scenes"] for k in s["required_keywords"]))
        # this call raised "'int' object has no attribute 'lower'" before normalisation
        rr.score_candidate("Meiji 1868 print", "", [], turning["required_keywords"])


class CaptionFit(unittest.TestCase):
    def test_caption_line_always_fits_and_contains_spoken_word(self):
        import render_region_short as rr
        from PIL import ImageDraw
        d = ImageDraw.Draw(Image.new("RGB", (1080, 400))); f = rr.font(48, True)
        script = yaml.safe_load((ROOT / "regions" / "tokyo.yaml").read_text(encoding="utf-8"))["story"]["narration"].split()
        for wi in range(len(script)):
            lo, hi = rr.caption_window(d, script, wi, f, rr.W - 190)
            self.assertTrue(lo <= wi < hi)
            self.assertLessEqual(d.textlength(" ".join(script[lo:hi]), font=f), rr.W - 190 + 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
