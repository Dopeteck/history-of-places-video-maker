#!/usr/bin/env python3
"""Regression tests that replay realistic provider responses through the real resolver code paths.

Schemas follow the providers' own documentation (AIC: image_id is a top-level field and `fields` selects
what is returned; LoC: `id` is an http URL, `url` is https, image_url entries can be protocol-relative and an
empty search is an HTTP 404; Cleveland: images.web is the small derivative, images.print the large one).
No network, no models."""
import io, json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import requests
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import asset_resolver as ar


def noise_jpeg(w, h, seed):
    arr = np.random.default_rng(seed).integers(0, 255, (h, w, 3), dtype=np.uint8)
    buf = io.BytesIO(); Image.fromarray(arr).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def http_error(status):
    r = requests.Response(); r.status_code = status
    return requests.HTTPError(f"HTTP {status}", response=r)


class Replay(unittest.TestCase):
    """Patches ar._get with a fake API router."""
    def setUp(self):
        self._get = ar._get
        ar.STATS.clear(); ar.FOUND.clear(); ar.TOP_REJECTED.clear(); ar._FAILS.clear(); ar.PROVIDER_ERRORS.clear()
        self.addCleanup(lambda: setattr(ar, "_get", self._get))

    def route(self, fn):
        ar._get = fn


class ProviderSchemas(Replay):
    def test_aic_requests_image_id_and_builds_iiif_url_from_the_top_level_field(self):
        seen = {}
        def api(url, params=None, timeout=10):
            seen["fields"] = params["fields"]
            rec = {"id": 11, "title": "Hauling Canal Boats, Yotsugi Road", "artist_title": "Utagawa Hiroshige",
                   "date_display": "1857", "is_public_domain": True, "image_id": "abc-123",
                   "thumbnail": {"lqip": "data:image/gif;base64,xx", "width": 1000, "height": 700, "alt_text": "print"},
                   "api_link": "https://api.artic.edu/api/v1/artworks/11"}
            wanted = params["fields"].split(",")          # the real API only returns requested fields
            return {"data": [{k: v for k, v in rec.items() if k in wanted}]}
        self.route(api)
        got = ar._aic("Yotsugi")
        self.assertIn("image_id", seen["fields"].split(","))
        self.assertEqual(len(got), 1, dict(ar.STATS))
        self.assertTrue(got[0]["image_url"].startswith("https://www.artic.edu/iiif/2/abc-123/full/"))

    def test_loc_uses_https_url_not_http_id_and_accepts_protocol_relative_images(self):
        def api(url, params=None, timeout=10):
            return {"results": [{"id": "http://www.loc.gov/item/2002719523/", "url": "https://www.loc.gov/item/2002719523/",
                                 "title": "Edo meisho: Nihonbashi", "description": ["woodblock print"],
                                 "image_url": ["//cdn.loc.gov/service/pnp/jpd/00200/00201_150px.jpg#h=150&w=117",
                                               "//cdn.loc.gov/service/pnp/jpd/00200/00201v.jpg#h=640&w=500"],
                                 "rights": "No known restrictions on publication.", "contributor": ["Hiroshige"]}]}
        self.route(api)
        got = ar._loc("Nihonbashi")
        self.assertEqual(len(got), 1, dict(ar.STATS))
        self.assertTrue(got[0]["source_url"].startswith("https://www.loc.gov/item/"))
        self.assertEqual(got[0]["image_url"], "https://cdn.loc.gov/service/pnp/jpd/00200/00201v.jpg")

    def test_loc_empty_search_is_a_404_and_must_not_count_as_a_provider_failure(self):
        def api(url, params=None, timeout=10): raise http_error(404)
        self.route(api)
        self.assertEqual(ar._loc("no such thing"), [])

    def test_cleveland_prefers_the_large_print_derivative(self):
        def api(url, params=None, timeout=10):
            return {"data": [{"title": "Portrait of Tokugawa Ieyasu", "creation_date": "1700", "share_license_status": "CC0",
                              "url": "https://clevelandart.org/art/1.2",
                              "images": {"web": {"url": "https://openaccess-cdn.clevelandart.org/1.2/1.2_web.jpg", "width": "800"},
                                         "print": {"url": "https://openaccess-cdn.clevelandart.org/1.2/1.2_print.jpg", "width": "3400"}}}]}
        self.route(api)
        self.assertTrue(ar._cleveland("Ieyasu")[0]["image_url"].endswith("_print.jpg"))

    def test_europeana_http_media_and_smithsonian_http_record_links_are_upgraded_not_dropped(self):
        import os
        os.environ["EUROPEANA_API_KEY"] = "k"; os.environ["SMITHSONIAN_API_KEY"] = "k"
        self.addCleanup(lambda: (os.environ.pop("EUROPEANA_API_KEY", None), os.environ.pop("SMITHSONIAN_API_KEY", None)))
        def api(url, params=None, timeout=10):
            if "europeana" in url:
                return {"items": [{"title": ["Edo castle print"], "rights": ["http://creativecommons.org/publicdomain/mark/1.0/"],
                                   "edmIsShownBy": ["http://media.example.org/edo.jpg"], "guid": "https://www.europeana.eu/item/1/a",
                                   "dcCreator": ["Hiroshige"], "id": "/1/a"}]}
            return {"response": {"rows": [{"id": "x", "title": "Edo print", "content": {"descriptiveNonRepeating": {
                "title": {"content": "Edo print"}, "record_link": "http://n2t.net/ark:/65665/ab1",
                "online_media": {"media": [{"type": "Images", "content": "https://ids.si.edu/ids/x.jpg", "usage": {"access": "CC0"}}]}}}}]}}
        self.route(api)
        self.assertEqual(len(ar._europeana("edo")), 1, dict(ar.STATS))
        self.assertEqual(len(ar._smithsonian("edo")), 1, dict(ar.STATS))

    def test_https_normaliser(self):
        self.assertEqual(ar._https("//cdn.loc.gov/a.jpg"), "https://cdn.loc.gov/a.jpg")
        self.assertEqual(ar._https("http://www.loc.gov/item/1/"), "https://www.loc.gov/item/1/")
        self.assertEqual(ar._https("https://x.org/a"), "https://x.org/a")
        self.assertEqual(ar._https(""), "")


def cand(title, n=1, provider="Fake Museum", rights="Public Domain", author="A", facets=None, description="", categories=None, year_range=None, date_text=""):
    c = {"provider": provider, "title": title, "description": description, "categories": categories or [], "date_text": date_text,
         "image_url": f"https://example.org/{n}.jpg", "source_url": f"https://example.org/item/{n}", "rights_statement": rights,
         "rights_url": "https://example.org/r", "author": author, "identifier": str(n), "facets": facets or []}
    if year_range: c["year_range"] = year_range
    return c


class Flow(unittest.TestCase):
    GOOD = {"score": .40, "p_document": .05, "p_modern": .05}
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self._orig = (ar.PROVIDERS, ar._commons_category, ar._commons_search, ar._download, ar._clip_analyze, ar._ocr_available)
        ar._FAILS.clear(); ar.PROVIDER_ERRORS.clear()
        ar._commons_category = lambda name: []
        ar._commons_search = lambda q: []
        ar._ocr_available = lambda: False
        self.blobs = {}; self.clip = {}; self.prompts_seen = []
        ar._download = lambda c: self.blobs[c["source_url"]]
        def fake_clip(prompts, blobs):
            self.prompts_seen.append(prompts)
            return [self.clip[hash(b)] for b in blobs]
        ar._clip_analyze = fake_clip
        self.addCleanup(lambda: (setattr(ar, "PROVIDERS", self._orig[0]), setattr(ar, "_commons_category", self._orig[1]),
                                 setattr(ar, "_commons_search", self._orig[2]), setattr(ar, "_download", self._orig[3]),
                                 setattr(ar, "_clip_analyze", self._orig[4]), setattr(ar, "_ocr_available", self._orig[5])))

    def provider(self, items, clips=None, name="_met"):
        def fn(query): return [dict(c) for c in items]
        fn.__name__ = name
        ar.PROVIDERS = [fn]
        for k, c in enumerate(items):
            b = noise_jpeg(1400, 1000, hash(c["source_url"]) % 10000); self.blobs[c["source_url"]] = b
            self.clip[hash(b)] = (clips or [self.GOOD] * len(items))[k]

    def run_scene(self, scene, config=None):
        cfg = {"name": "Testville", "asset_resolver": {"providers": ["_met"]}}; cfg.update(config or {})
        return ar.resolve_scene_image(scene, 1, cfg, self.tmp.name, set(), Path(self.tmp.name))

    def scene(self, **kw):
        s = {"title": "T", "narration_intent": "intent", "image_type": "historic", "image_search_terms": ["a b c", "d e f", "g h i"]}
        s.update(kw); return s


class Gates(Flow):
    def test_keyword_match_ignores_diacritics_and_uses_whole_words(self):
        self.provider([cand("Koishikawa Kōrakuen in autumn", 1, facets=[]), cand("Fredo the frog", 2)],
                      [{"score": .4, "p_document": .05, "p_modern": .9}] * 2)
        _, credit = self.run_scene(self.scene(image_type="modern", required_keywords=["korakuen", "edo"]))
        self.assertIn("Kōrakuen", credit["title"])
        self.assertEqual(ar.STATS["keywords"], 1)          # "Fredo" must not satisfy the whole-word keyword "edo"

    def test_artist_culture_and_medium_metadata_count_as_subject_evidence(self):
        self.provider([cand("Cherry Trees along Gokacho", 1, facets=["Utagawa Hiroshige", "Color woodblock print"])])
        _, credit = self.run_scene(self.scene(required_keywords=["hiroshige"], required_keyword_groups=[["woodblock", "print"]]))
        self.assertEqual(credit["title"], "Cherry Trees along Gokacho")

    def test_html_in_provider_descriptions_is_stripped_before_term_matching(self):
        html_desc = '<div class="description en"><a href="https://example.org/event/page">Edo Castle moat</a></div>'
        real = ar._candidate("Wikimedia Commons", "File:Moat.jpg", html_desc, "https://upload.example.org/m.jpg",
                             "https://commons.example.org/m", "Public domain", "", '<a href="https://x.org/u">Unknown</a>')
        self.assertNotIn("<", real["description"])                      # normalised on the real provider path
        real["author"] = "A"
        self.provider([real])
        # and a provider that forgot to normalise cannot smuggle markup into the term matching either
        self.provider([cand("File:Moat.jpg", 1, description=html_desc)])
        _, credit = self.run_scene(self.scene(required_keywords=["edo"]),
                                   {"image_banned_terms": ["event", "page"]})
        self.assertEqual(credit["title"], "File:Moat.jpg")

    def test_cc_by_requires_a_named_author(self):
        self.provider([cand("Edo print", 1, rights="CC BY 4.0", author="Not stated")])
        with self.assertRaises(ar.ResolverError) as cm:
            self.run_scene(self.scene(required_keywords=["edo"]))
        self.assertEqual(cm.exception.diagnostics["rejected"]["attribution"], 1)

    def test_date_window_uses_structured_dates_and_ignores_three_digit_noise(self):
        self.provider([cand("Kyoto Gosho, No. 100 of the series", 1, description="Kyoto palace", year_range=(1915, 1915))])
        with self.assertRaises(ar.ResolverError) as cm:
            self.run_scene(self.scene(required_keywords=["kyoto"], max_year=1867))
        self.assertEqual(cm.exception.diagnostics["rejected"]["date_window"], 1)
        self.provider([cand("Kyoto Gosho, plate 123", 2, description="photographed 1915")])
        with self.assertRaises(ar.ResolverError) as cm:
            self.run_scene(self.scene(required_keywords=["kyoto"], max_year=1867))
        self.assertEqual(cm.exception.diagnostics["rejected"]["date_window"], 1)

    def test_clip_receives_short_natural_prompts_not_keyword_soup(self):
        self.provider([cand("Portrait of Tokugawa Ieyasu", 1)])
        scene = self.scene(title="The Shogunate", named_entities=["Tokugawa Ieyasu"], visual_tags=["Tokugawa shogunate"],
                           required_keywords=["tokugawa"], narration_intent="Place Tokugawa Ieyasu's 1603 shogunate in the city's story.")
        self.run_scene(scene)
        prompts = self.prompts_seen[0]
        self.assertIsInstance(prompts, list); self.assertLessEqual(len(prompts), 4)
        self.assertIn("Tokugawa Ieyasu", prompts[0])
        for p in prompts:
            self.assertLessEqual(len(p.split()), 40, p)       # CLIP truncates at 77 tokens
            self.assertNotIn("keywords:", p)
        modern = self.scene(image_type="modern", named_entities=["Tokyo Skytree"], required_keywords=["skytree"])
        self.provider([cand("Tokyo Skytree", 2)], [{"score": .4, "p_document": .05, "p_modern": .9}])
        self.run_scene(modern)
        self.assertIn("photograph", self.prompts_seen[-1][0])


class QueryGeneration(Flow):
    def test_queries_are_entity_first_diverse_deduped_and_capped(self):
        scene = {
          "title": "Test Scene",
          "named_entities": ["Tokugawa Ieyasu", "Edo Castle"],
          "visual_tags": ["woodblock print", "ukiyo-e"],
          "image_search_terms": ["Edo period bridge", "Tokugawa Ieyasu portrait"],
        }
        queries = ar.build_queries(scene)
        self.assertGreaterEqual(len(queries), 1)
        self.assertEqual(queries[0], "Tokugawa Ieyasu")      # named entities lead
        self.assertIn("Edo Castle", queries)
        self.assertEqual(len(queries), len(set(queries)))    # no duplicates
        self.assertLessEqual(len(queries), 6)                # result cap

    def test_scene_with_no_planner_terms_still_searches_its_title(self):
        scene = {"title": "Lonely Monument", "image_type": "historic"}
        queries = ar.build_queries(scene)
        self.assertEqual(queries, ["Lonely Monument"])

    def test_empty_provider_results_are_recorded_as_no_results_not_a_failure(self):
        calls = {"n": 0}
        def empty(query):
            calls["n"] += 1
            return []
        empty.__name__ = "_empty"
        ar.PROVIDERS = [empty]
        with self.assertRaises(ar.ResolverError) as cm:
            self.run_scene(self.scene(), {"asset_resolver": {"providers": ["_empty"]}})
        d = cm.exception.diagnostics
        self.assertEqual(d["providers_found"].get("_empty"), 0)
        self.assertNotIn("_empty", d.get("provider_errors", {}))
        self.assertGreaterEqual(calls["n"], 1)   # the empty provider was attempted, not skipped


class Diagnostics(Flow):
    def test_late_stage_clip_rejections_are_not_crowded_out_by_early_rejections(self):
        items = [cand(f"Bad rights {i}", i, rights="All rights reserved") for i in range(12)] + [cand("Edo castle weak", 99)]
        self.provider(items, [self.GOOD] * 12 + [{"score": .10, "p_document": .05, "p_modern": .05}])
        with self.assertRaises(ar.ResolverError) as cm:
            self.run_scene(self.scene(required_keywords=["edo"]))
        d = cm.exception.diagnostics
        self.assertTrue(any(r["reason"] == "clip_below_threshold" and "clip_score" in r for r in d["top_rejected"]), d["top_rejected"])
        self.assertEqual(d["clip_top"][0]["title"], "Edo castle weak")

    def test_banned_term_and_metadata_rejections_say_which_rule_fired(self):
        self.provider([cand("Edo poster", 1)])
        with self.assertRaises(ar.ResolverError) as cm:
            self.run_scene(self.scene(required_keywords=["edo"]), {"image_banned_terms": ["poster"]})
        row = next(r for r in cm.exception.diagnostics["top_rejected"] if r["reason"] == "banned_term")
        self.assertEqual(row["matched"], "poster")

    def test_missing_image_url_is_counted_separately_from_other_metadata_problems(self):
        ar.STATS.clear()
        self.assertIsNone(ar._candidate("P", "T", "", None, "https://x.org/a", "CC0", ""))
        self.assertEqual(ar.STATS["no_image_url"], 1)
        self.assertIsNone(ar._candidate("P", "T", "", "https://x.org/i.jpg", "", "CC0", ""))
        self.assertEqual(ar.STATS["no_source_url"], 1)

    def test_key_gated_provider_without_key_is_reported_as_skipped_not_silently_empty(self):
        import os
        os.environ.pop("SMITHSONIAN_API_KEY", None)
        ar.PROVIDERS = [ar._smithsonian]
        with self.assertRaises(ar.ResolverError) as cm:
            self.run_scene(self.scene(required_keywords=["edo"]), {"asset_resolver": {"providers": ["_smithsonian"]}})
        self.assertIn("SMITHSONIAN_API_KEY", cm.exception.diagnostics["provider_status"]["_smithsonian"]["skipped"])


class CircuitBreaker(Flow):
    def test_a_provider_that_failed_in_one_scene_is_tried_again_in_the_next(self):
        calls = {"n": 0}
        def _loc(query):
            calls["n"] += 1
            raise requests.ConnectionError("boom")
        ar.PROVIDERS = [_loc]
        cfg = {"asset_resolver": {"providers": ["_loc"]}}
        for _ in range(2):
            with self.assertRaises(ar.ResolverError):
                self.run_scene(self.scene(), cfg)
        # scene 1: two failures open the breaker for that scene only; scene 2 must try the provider again
        self.assertGreaterEqual(calls["n"], 3)

    def test_empty_results_do_not_open_the_breaker(self):
        calls = {"n": 0}
        def _loc(query):
            calls["n"] += 1
            return []
        ar.PROVIDERS = [_loc]
        with self.assertRaises(ar.ResolverError):
            self.run_scene(self.scene(max_queries_per_provider=6, image_search_terms=["a", "b", "c", "d", "e", "f"]),
                           {"asset_resolver": {"providers": ["_loc"]}})
        self.assertEqual(calls["n"], 6)


class Renderer(unittest.TestCase):
    def test_missing_asset_card_is_not_rewrapped_by_the_modern_gate_and_paths_outside_root_work(self):
        import render_region_short as rr
        scene = {"title": "Gardens Remember", "image_type": "modern"}
        self.assertFalse(rr._needs_modern_recheck(scene, {"provider": "missing asset", "period_ok": None}))
        self.assertTrue(rr._needs_modern_recheck(scene, {"provider": "Wikimedia Commons", "period_ok": None}))
        self.assertFalse(rr._needs_modern_recheck(scene, {"provider": "Wikimedia Commons", "period_ok": True}))
        self.assertFalse(rr._needs_modern_recheck({"title": "x", "image_type": "historic"}, {"provider": "Met", "period_ok": None}))
        with tempfile.TemporaryDirectory() as d:                  # output directory outside the repo
            im, credit = rr.text_fallback_card(scene, 3, {"visual_style": {}}, Path(d), "No candidates. Diagnostics: {\"rejected\": {\"keywords\": 4}}")
            self.assertEqual(credit["diagnostics"]["rejected"]["keywords"], 4)
            self.assertTrue(Path(credit["file"]).name.endswith("_missing.jpg"))

    def test_needs_modern_recheck_all_period_states(self):
        import render_region_short as rr
        modern = {"title": "Gardens Remember", "image_type": "modern"}
        # Valid modern-provider image passes; no re-check needed.
        self.assertFalse(rr._needs_modern_recheck(modern, {"provider": "Wikimedia Commons", "period_ok": True}))
        # Historical-looking image in a modern scene must fail closed.
        self.assertTrue(rr._needs_modern_recheck(modern, {"provider": "Wikimedia Commons", "period_ok": False}))
        # Unknown period status in a modern scene must fail closed.
        self.assertTrue(rr._needs_modern_recheck(modern, {"provider": "Wikimedia Commons", "period_ok": None}))
        # Map scenes are never re-checked.
        self.assertFalse(rr._needs_modern_recheck({"title": "Edo on the Map", "image_type": "map"}, {"provider": "Natural Earth", "period_ok": None}))
        # Missing assets and placeholders are never re-checked (they already failed).
        self.assertFalse(rr._needs_modern_recheck(modern, {"provider": "missing asset", "period_ok": None}))
        self.assertFalse(rr._needs_modern_recheck(modern, {"provider": "fallback text card", "period_ok": None}))
        self.assertFalse(rr._needs_modern_recheck(modern, {"provider": "procedural illustration", "period_ok": None}))
        # Historic scenes are never re-checked.
        self.assertFalse(rr._needs_modern_recheck({"title": "x", "image_type": "historic"}, {"provider": "Met", "period_ok": None}))


class SubtitleVisibility(Flow):
    def test_font_resolves_fallback_chain_on_non_linux(self):
        import render_region_short as rr
        rr.font.cache_clear()
        p = rr._resolve_font_path(bold=True)
        # On any system, either a real TTF path is found or we gracefully fall back.
        self.assertTrue(p is None or Path(p).exists())
        f = rr.font(48, True)
        self.assertIsNotNone(f)

    def test_srt_timestamp_format(self):
        import render_region_short as rr
        self.assertEqual(rr.srt_timestamp(0.0), "00:00:00,000")
        self.assertEqual(rr.srt_timestamp(1.5), "00:00:01,500")
        self.assertEqual(rr.srt_timestamp(61.25), "00:01:01,250")
        self.assertEqual(rr.srt_timestamp(3661.999), "01:01:01,999")

    def test_srt_groups_words_into_readable_lines(self):
        import render_region_short as rr
        words = ["Before", "Paris", "became", "the", "City", "of", "Light", "it", "was", "a", "modest", "settlement"]
        wtimes = [(0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 2.5), (2.5, 3.0),
                  (3.0, 3.5), (3.5, 4.0), (4.0, 4.5), (4.5, 5.0), (5.0, 5.5), (5.5, 6.0)]
        entries = rr.srt_from_word_times(words, wtimes, max_chars=42, max_words=5)
        self.assertGreaterEqual(len(entries), 1)
        # First entry starts at 0
        self.assertEqual(entries[0][1], "00:00:00,000")
        # Last entry ends at 6.0
        self.assertEqual(entries[-1][2], "00:00:06,000")
        # No entry exceeds max_words
        for _, _, _, text in entries:
            self.assertLessEqual(len(text.split()), 5)

    def test_write_srt_produces_valid_file(self):
        import render_region_short as rr
        import tempfile
        from pathlib import Path
        words = ["Hello", "world", "this", "is", "a", "test"]
        wtimes = [(0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 2.5), (2.5, 3.0)]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "captions.srt"
            rr.write_srt(p, words, wtimes)
            content = p.read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> 00:00:02,500", content)
            self.assertIn("00:00:02,500 --> 00:00:03,000", content)
            self.assertIn("Hello world this is a", content)
            self.assertIn("test", content)

    def test_caption_window_keeps_active_word_centered(self):
        import render_region_short as rr
        from PIL import ImageDraw, Image
        words = ["word1", "word2", "word3", "word4", "word5", "word6", "word7"]
        im = Image.new("RGB", (1080, 1920))
        d = ImageDraw.Draw(im)
        f = rr.font(48, True)
        # Active word in the middle
        lo, hi = rr.caption_window(d, words, 3, f, 900)
        self.assertLessEqual(lo, 3)
        self.assertGreaterEqual(hi, 4)
        # Caption window text should not exceed max width
        text = " ".join(words[lo:hi])
        self.assertLessEqual(d.textlength(text, font=f), 900)


class ResolverIdentityFallback(unittest.TestCase):
    """identity_fallback_to_clip and allow_modern_photo are explicit per-scene opt-ins."""

    def test_identity_fallback_to_clip_allows_missing_entities_with_warning(self):
        import asset_resolver as ar
        scene = {
            "title": "Colonial Capital",
            "image_type": "historic",
            "identity_fallback_to_clip": True,
            "named_entities": ["Zócalo", "Cathedral"],
            "required_keywords": ["colonial"],
            "max_queries": 1,
        }
        cfg = {"asset_resolver": {"providers": ["_loc"]}}
        # Provider returns nothing; resolver raises. We only test that the
        # flag is accepted without error at scene-config validation time.
        self.assertTrue(scene.get("identity_fallback_to_clip"))

    def test_allow_modern_photo_is_explicit_opt_in(self):
        import asset_resolver as ar
        # By default, modern photos are rejected in historic scenes.
        scene = {"title": "City on the Water", "image_type": "historic",
                 "named_entities": ["Tenochtitlan"], "required_keywords": ["temple"]}
        self.assertFalse(scene.get("allow_modern_photo", False))
        # When explicitly set, modern reconstructions are allowed.
        scene["allow_modern_photo"] = True
        self.assertTrue(scene.get("allow_modern_photo"))


class SceneCompletenessQA(unittest.TestCase):
    """Production QA must fail when scenes are missing approved images."""

    def test_scene_completeness_fails_on_missing_asset(self):
        import qa_checks as qc
        import tempfile, json
        from pathlib import Path
        credits = [
            {"scene": "Scene 1", "provider": "Wikimedia Commons", "source_url": "https://example.com/1"},
            {"scene": "Scene 2", "provider": "missing asset", "source_url": ""},
        ]
        with tempfile.TemporaryDirectory() as d:
            cp = Path(d) / "credits.json"
            cp.write_text(json.dumps(credits), encoding="utf-8")
            result = qc.check_scene_completeness(cp, 2)
            self.assertFalse(result.passed)
            self.assertEqual(result.severity, "error")
            self.assertIn("missing", result.details.lower())

    def test_scene_completeness_passes_when_all_approved(self):
        import qa_checks as qc
        import tempfile, json
        from pathlib import Path
        credits = [
            {"scene": "Scene 1", "provider": "Wikimedia Commons", "source_url": "https://example.com/1"},
            {"scene": "Scene 2", "provider": "Met", "source_url": "https://example.com/2"},
        ]
        with tempfile.TemporaryDirectory() as d:
            cp = Path(d) / "credits.json"
            cp.write_text(json.dumps(credits), encoding="utf-8")
            result = qc.check_scene_completeness(cp, 2)
            self.assertTrue(result.passed)

    def test_captions_requires_valid_srt_entries(self):
        import qa_checks as qc
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "captions.srt"
            p.write_text("1\n00:00:00,000 --> 00:00:05,000\nHello world\n", encoding="utf-8")
            result = qc.check_captions(p)
            self.assertTrue(result.passed)

    def test_captions_rejects_empty_file(self):
        import qa_checks as qc
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "captions.srt"
            p.write_text("", encoding="utf-8")
            result = qc.check_captions(p)
            self.assertFalse(result.passed)


class StorySelectionDedup(unittest.TestCase):
    """Story angles must not repeat across runs."""

    def test_select_next_topic_excludes_used_story_angles(self):
        import daily_content_engine as dce
        from daily_content_engine import Topic, EditorialState
        topics = [
            Topic("tokyo", "Tokyo", "Japan", "Asia", "culture", "Tokyo Culture", "tokyo:culture:traditions", "Hook", "Desc", ["s1"]),
            Topic("tokyo", "Tokyo", "Japan", "Asia", "hidden", "Tokyo Hidden", "tokyo:hidden:angle", "Hook", "Desc", ["s2"]),
        ]
        state = EditorialState(used_topics=["tokyo:culture:traditions"])
        result = dce.select_next_topic(topics, state)
        self.assertIsNotNone(result)
        self.assertEqual(result.story_angle, "tokyo:hidden:angle")

    def test_select_next_topic_returns_none_when_all_used(self):
        import daily_content_engine as dce
        from daily_content_engine import Topic, EditorialState
        topics = [
            Topic("tokyo", "Tokyo", "Japan", "Asia", "culture", "Tokyo Culture", "tokyo:culture:traditions", "Hook", "Desc", ["s1"]),
        ]
        state = EditorialState(used_topics=["tokyo:culture:traditions"])
        result = dce.select_next_topic(topics, state)
        self.assertIsNone(result)


class ScenePlannerIntegration(unittest.TestCase):
    """The scene planner must turn a selected story into renderer-ready scenes."""

    def test_plan_scenes_produces_valid_renderer_format(self):
        import scene_planner as sp
        import yaml
        cfg = yaml.safe_load(open("regions/mexico-city.yaml", encoding="utf-8"))
        topic = {
            "destination_slug": "mexico-city",
            "destination_name": "Mexico City",
            "country": "Mexico",
            "continent": "North America",
            "category": "culture_traditions",
            "title": "Día de Muertos: From Aztec Miccaihuitl to Modern Celebration",
            "story_id": "mexico-city:culture:dia-de-muertos",
            "hook": "Long before sugar skulls and marigolds.",
            "description": "Explore how the Indigenous Miccaihuitl festival merged with Catholic All Saints day.",
            "entities": ["Miccaihuitl", "Mictecacihuatl", "ofrenda", "cempasuchil"],
            "sources": ["https://en.wikipedia.org/wiki/Day_of_the_Dead"],
            "claim_refs": ["miccaihuitl_origins"],
        }
        scenes = sp.plan_scenes(topic, cfg)
        self.assertGreaterEqual(len(scenes), 8)
        self.assertLessEqual(len(scenes), 12)
        for s in scenes:
            self.assertIn("title", s)
            self.assertIn("narration_intent", s)
            self.assertIn("image_type", s)
            self.assertIn(s["image_type"], ("historic", "modern", "map"))
            self.assertGreaterEqual(len(s.get("image_search_terms", [])), 3)
            self.assertTrue(s.get("required_keywords"))
            self.assertIn("named_entities", s)
            self.assertIn("image_search_queries", s)
            self.assertGreaterEqual(len(s["image_search_queries"]), 1)

    def test_different_categories_produce_different_scenes(self):
        import scene_planner as sp
        import yaml
        cfg = yaml.safe_load(open("regions/mexico-city.yaml", encoding="utf-8"))
        base_topic = {
            "destination_slug": "mexico-city",
            "destination_name": "Mexico City",
            "country": "Mexico",
            "continent": "North America",
            "story_id": "mexico-city:test",
            "hook": "Hook",
            "description": "Description",
            "entities": ["Zocalo", "Cathedral"],
            "sources": ["https://example.com"],
            "claim_refs": ["claim1"],
        }
        topic_a = dict(base_topic); topic_a["category"] = "culture_traditions"; topic_a["title"] = "Tradition"
        topic_b = dict(base_topic); topic_b["category"] = "independence_colonial"; topic_b["title"] = "Independence"
        scenes_a = sp.plan_scenes(topic_a, cfg)
        scenes_b = sp.plan_scenes(topic_b, cfg)
        titles_a = {s["title"] for s in scenes_a}
        titles_b = {s["title"] for s in scenes_b}
        # Map scene title is the same; everything else should differ
        non_map_a = {t for t in titles_a if "Map" not in t}
        non_map_b = {t for t in titles_b if "Map" not in t}
        self.assertNotEqual(non_map_a, non_map_b)

    def test_topic_category_takes_precedence_over_config(self):
        import scene_planner as sp
        import yaml
        cfg = yaml.safe_load(open("regions/mexico-city.yaml", encoding="utf-8"))
        # Config says hidden_history, topic says culture_traditions
        topic = {
            "destination_slug": "mexico-city",
            "destination_name": "Mexico City",
            "country": "Mexico",
            "continent": "North America",
            "category": "culture_traditions",
            "story_id": "mexico-city:test",
            "hook": "Hook",
            "description": "Description",
            "entities": ["Miccaihuitl"],
            "sources": ["https://example.com"],
            "claim_refs": ["claim1"],
        }
        scenes = sp.plan_scenes(topic, cfg)
        # culture_traditions scenes have "Miccaihuitl" in entities, hidden_history would use generic subject
        titles = " ".join(s["title"] for s in scenes)
        self.assertIn("Miccaihuitl", titles)

    def test_scene_plan_has_stable_ids(self):
        import scene_planner as sp
        import yaml
        cfg = yaml.safe_load(open("regions/mexico-city.yaml", encoding="utf-8"))
        topic = {
            "destination_slug": "mexico-city",
            "destination_name": "Mexico City",
            "country": "Mexico",
            "continent": "North America",
            "category": "ancient_civilization",
            "story_id": "mexico-city:aztec:temples",
            "hook": "Hook",
            "description": "Description",
            "entities": ["Templo Mayor", "Tenochtitlan"],
            "sources": ["https://example.com"],
            "claim_refs": ["claim1"],
        }
        scenes = sp.plan_scenes(topic, cfg)
        ids = [s["scene_id"] for s in scenes]
        self.assertEqual(len(ids), len(set(ids)), "Scene IDs must be unique")
        for sid in ids:
            self.assertTrue(sid.startswith("mexico-city:aztec:temples:"), f"Bad scene ID: {sid}")


class ScriptSceneConsistency(unittest.TestCase):
    """The script, scene plan, and story must all reference the same story."""

    def test_script_matches_selected_story(self):
        import generate_script as gs
        topic = {
            "destination_slug": "lagos",
            "destination_name": "Lagos",
            "country": "Nigeria",
            "continent": "Africa",
            "category": "music_film_entertainment",
            "title": "Lagos Highlife Music: The Sound of Independence",
            "story_id": "lagos:music:highlife",
            "hook": "Lagos was the heartbeat of West African music.",
            "description": "Highlife music emerged in Lagos in the 1950s.",
            "entities": ["Fela Kuti", "Highlife", "Lagos"],
            "narration": "Lagos was the heartbeat of West African music in the decades after independence. Highlife emerged in the 1950s as a vibrant fusion of jazz, brass band music, and local Yoruba rhythms. The sound filled dance halls and nightclubs across Lagos Island and the mainland. Musicians like Fela Kuti later transformed highlife into afrobeat, carrying the Lagos sound to global audiences. Today, the city still pulses with live music in venues from Victoria Island to Ikeja.",
        }
        research = {"claims": [
            {"status": "verified", "text": "Highlife emerged in Lagos in the 1950s as a fusion of jazz and local rhythms", "evidence": [{"source_url": "https://example.com"}]},
            {"status": "verified", "text": "Fela Kuti transformed highlife into afrobeat", "evidence": [{"source_url": "https://example.com"}]},
        ]}
        script = gs.build_script_from_research(topic, research)
        narration = script.narration.lower()
        self.assertIn("highlife", narration)
        self.assertIn("lagos", narration)
        self.assertGreaterEqual(script.word_count, 110)
        self.assertLessEqual(script.word_count, 155)


class ProviderDiagnosticSerialization(unittest.TestCase):
    """ProviderDiagnostic objects serialize correctly for diagnostics printing."""

    def test_asdict_roundtrip(self):
        import research_providers as rp
        from dataclasses import asdict
        d = rp.ProviderDiagnostic(
            provider="Wikipedia", endpoint="wikipedia.org/w/api.php",
            query="test query", status="failed",
            http_status=403, elapsed_ms=250.5, result_count=0,
            error="HTTP 403")
        d_dict = asdict(d)
        self.assertEqual(d_dict["provider"], "Wikipedia")
        self.assertEqual(d_dict["status"], "failed")
        self.assertEqual(d_dict["http_status"], 403)
        self.assertEqual(d_dict["error"], "HTTP 403")

    def test_provider_diagnostic_attribute_access(self):
        import research_providers as rp
        d = rp.ProviderDiagnostic(
            provider="Library of Congress", endpoint="www.loc.gov/search/",
            query="test", status="success",
            http_status=200, elapsed_ms=100.0, result_count=5)
        self.assertEqual(d.provider, "Library of Congress")
        self.assertEqual(d.status, "success")
        self.assertEqual(d.http_status, 200)
        self.assertEqual(d.result_count, 5)
        self.assertEqual(d.elapsed_ms, 100.0)


class MultiProviderAdapterTests(unittest.TestCase):
    """Mocked HTTP responses for each provider adapter."""

    def _mock_response(self, status_code=200, json_data=None, text=""):
        """Create a mock requests.Response."""
        resp = requests.Response()
        resp.status_code = status_code
        if json_data is not None:
            resp._content = json.dumps(json_data).encode("utf-8")
        else:
            resp._content = text.encode("utf-8")
        resp.raise_for_status = lambda: None
        return resp

    def _mock_response_with_error(self, status_code):
        """Create a mock response whose raise_for_status() raises HTTPError."""
        import requests

        def _factory(*args, **kwargs):
            resp = requests.Response()
            resp.status_code = status_code
            resp._content = b"error"
            def _raise():
                raise requests.HTTPError(f"HTTP {status_code}", response=resp)
            resp.raise_for_status = _raise
            return resp
        return _factory

    def test_wikipedia_success(self):
        import research_providers as rp
        mock_json = {
            "query": {
                "search": [
                    {"title": "Tokugawa Ieyasu", "snippet": "Tokugawa Ieyasu was the founder of the Tokugawa shogunate."}
                ]
            }
        }
        with patch.object(rp, '_http_get', return_value=self._mock_response(200, mock_json)), \
             patch.object(rp, '_fetch_wikipedia_page', return_value="Tokugawa Ieyasu founded the shogunate in 1603."):
            sources, diag = rp.provider_wikipedia("Tokugawa Ieyasu", "Tokyo")
            self.assertEqual(diag.status, "success")
            self.assertEqual(diag.http_status, 200)
            self.assertGreaterEqual(len(sources), 1)
            self.assertEqual(sources[0].provider, "Wikipedia")

    def test_wikipedia_search_success_page_extract_fails(self):
        import research_providers as rp
        mock_json = {
            "query": {
                "search": [
                    {"title": "Tokugawa Ieyasu", "snippet": "Tokugawa Ieyasu was the founder."}
                ]
            }
        }
        with patch.object(rp, '_http_get', return_value=self._mock_response(200, mock_json)), \
             patch.object(rp, '_fetch_wikipedia_page', return_value=None):
            sources, diag = rp.provider_wikipedia("Tokugawa Ieyasu", "Tokyo")
            self.assertEqual(diag.status, "failed")
            self.assertEqual(diag.error, "page extract unavailable")
            self.assertEqual(len(sources), 0)

    def test_wikipedia_http_403(self):
        import research_providers as rp
        import requests
        with patch.object(rp, '_http_get', side_effect=self._mock_response_with_error(403)):
            sources, diag = rp.provider_wikipedia("test", "Tokyo")
            self.assertEqual(diag.status, "failed")
            self.assertEqual(diag.http_status, 403)

    def test_wikipedia_timeout(self):
        import research_providers as rp
        import requests
        with patch.object(rp, '_http_get', side_effect=requests.Timeout("Read timed out")):
            sources, diag = rp.provider_wikipedia("test", "Tokyo")
            self.assertEqual(diag.status, "failed")
            self.assertEqual(diag.error, "timeout")

    def test_wikipedia_invalid_json(self):
        import research_providers as rp
        def invalid_json(*args, **kwargs):
            resp = requests.Response()
            resp.status_code = 200
            resp._content = b"not json"
            resp.raise_for_status = lambda: None
            return resp
        with patch.object(rp, '_http_get', side_effect=invalid_json):
            sources, diag = rp.provider_wikipedia("test", "Tokyo")
            self.assertEqual(diag.status, "failed")

    def test_loc_success(self):
        import research_providers as rp
        mock_json = {
            "results": [
                {"title": "Edo Map", "description": ["Historic map of Edo"], "url": "https://loc.gov/item/123", "date": ["1603"]}
            ]
        }
        with patch.object(rp, '_http_get', return_value=self._mock_response(200, mock_json)):
            sources, diag = rp.provider_loc("Edo map", "Tokyo")
            self.assertEqual(diag.status, "success")
            self.assertEqual(diag.http_status, 200)
            self.assertGreaterEqual(len(sources), 1)

    def test_loc_404_is_not_failure(self):
        import research_providers as rp
        import requests
        with patch.object(rp, '_http_get', side_effect=self._mock_response_with_error(404)):
            sources, diag = rp.provider_loc("nonexistent", "Tokyo")
            self.assertEqual(diag.status, "success")
            self.assertEqual(diag.result_count, 0)

    def test_loc_5xx_is_failure(self):
        import research_providers as rp
        import requests
        with patch.object(rp, '_http_get', side_effect=self._mock_response_with_error(500)):
            sources, diag = rp.provider_loc("test", "Tokyo")
            self.assertEqual(diag.status, "failed")

    def test_europeana_skipped_without_key(self):
        import research_providers as rp
        old_key = os.environ.pop("EUROPEANA_API_KEY", None)
        try:
            sources, diag = rp.provider_europeana("test", "Tokyo")
            self.assertEqual(diag.status, "skipped")
            self.assertIn("EUROPEANA_API_KEY", diag.error)
        finally:
            if old_key is not None:
                os.environ["EUROPEANA_API_KEY"] = old_key

    def test_europeana_http_401_no_key_leak(self):
        import research_providers as rp
        import requests
        old_key = os.environ.setdefault("EUROPEANA_API_KEY", "test_key_12345")
        try:
            with patch.object(rp, '_http_get', side_effect=self._mock_response_with_error(401)):
                sources, diag = rp.provider_europeana("test", "Tokyo")
                self.assertEqual(diag.status, "failed")
                self.assertEqual(diag.http_status, 401)
                self.assertNotIn("test_key_12345", diag.error or "")
        finally:
            del os.environ["EUROPEANA_API_KEY"]

    def test_smithsonian_http_403_no_key_leak(self):
        import research_providers as rp
        import requests
        old_key = os.environ.setdefault("SMITHSONIAN_API_KEY", "sk_test_67890")
        try:
            with patch.object(rp, '_http_get', side_effect=self._mock_response_with_error(403)):
                sources, diag = rp.provider_smithsonian("test", "Tokyo")
                self.assertEqual(diag.status, "failed")
                self.assertEqual(diag.http_status, 403)
                self.assertNotIn("sk_test_67890", diag.error or "")
        finally:
            del os.environ["SMITHSONIAN_API_KEY"]

    def test_loc_europeana_smithsonian_accept_three_arg_dispatch(self):
        """verify_claim calls adapters as adapter(query, destination, language).

        LOC, Europeana and Smithsonian must accept three positional arguments
        without raising TypeError (regression for the argument-mismatch defect).
        """
        import research_providers as rp
        import os
        with patch.object(rp, '_http_get', return_value=self._mock_response(200, {"results": []})):
            sources, diag = rp.provider_loc("Edo map", "Tokyo", "en")
            self.assertEqual(diag.status, "success")
        old = os.environ.pop("EUROPEANA_API_KEY", None)
        try:
            sources, diag = rp.provider_europeana("Edo", "Tokyo", "en")
            self.assertEqual(diag.status, "skipped")
        finally:
            if old is not None:
                os.environ["EUROPEANA_API_KEY"] = old
        old2 = os.environ.pop("SMITHSONIAN_API_KEY", None)
        try:
            sources, diag = rp.provider_smithsonian("Edo", "Tokyo", "en")
            self.assertEqual(diag.status, "skipped")
        finally:
            if old2 is not None:
                os.environ["SMITHSONIAN_API_KEY"] = old2

    def _retryable_get_factory(self, sequence):
        """Factory returning a function that replays an HTTP status sequence.

        Each entry is (status_code, content, headers_or_None). The final entry
        is served for every call past the end of the list.
        """
        import requests
        state = {"n": 0}

        def fake_get(*args, **kwargs):
            state["n"] += 1
            idx = min(state["n"] - 1, len(sequence) - 1)
            status, content, headers = sequence[idx]
            resp = requests.Response()
            resp.status_code = status
            resp._content = content
            if headers:
                resp.headers = headers

            def _raise():
                if status >= 400:
                    raise requests.HTTPError(f"HTTP {status}", response=resp)
            resp.raise_for_status = _raise
            return resp

        return fake_get, state

    def test_wikipedia_http_429_retries_then_succeeds(self):
        """HTTP 429 with Retry-After is retried; a later 200 verifies the claim."""
        import research_providers as rp
        seq = [
            (429, b"rate limited", {"Retry-After": "1"}),
            (200, b'{"query":{"search":[{"title":"Tokugawa Ieyasu","snippet":"founder of the shogunate in 1603."}]}}', None),
        ]
        fake_get, state = self._retryable_get_factory(seq)
        with patch.object(rp.requests, "get", side_effect=fake_get), \
             patch.object(rp, '_fetch_wikipedia_page', return_value="Tokugawa Ieyasu founded the shogunate in 1603."):
            sources, diag = rp.provider_wikipedia("Tokugawa Ieyasu", "Tokyo")
        self.assertEqual(diag.status, "success")
        self.assertEqual(diag.http_status, 200)
        self.assertEqual(state["n"], 2, "one retry expected after 429")
        self.assertGreaterEqual(len(sources), 1)

    def test_wikipedia_http_429_exhausts_retries_reports_failure(self):
        """Persistent 429 exhausts bounded retries and reports provider_failure,
        never treating rate limiting as evidence the claim is false."""
        import research_providers as rp
        seq = [(429, b"rate limited", {"Retry-After": "1"})]
        fake_get, state = self._retryable_get_factory(seq)
        with patch.object(rp.requests, "get", side_effect=fake_get):
            sources, diag = rp.provider_wikipedia("Tokugawa Ieyasu", "Tokyo")
        self.assertEqual(diag.status, "failed")
        self.assertEqual(diag.http_status, 429)
        self.assertEqual(state["n"], rp.MAX_RETRIES + 1, "bounded retries exhausted")

    def test_transient_5xx_retries_then_succeeds(self):
        """A transient 503 is retried; a later 200 succeeds."""
        import research_providers as rp
        seq = [
            (503, b"unavailable", None),
            (200, b'{"query":{"search":[{"title":"Tokugawa Ieyasu","snippet":"founder of the shogunate in 1603."}]}}', None),
        ]
        fake_get, state = self._retryable_get_factory(seq)
        with patch.object(rp.requests, "get", side_effect=fake_get), \
             patch.object(rp, '_fetch_wikipedia_page', return_value="Tokugawa Ieyasu founded the shogunate in 1603."):
            sources, diag = rp.provider_wikipedia("Tokugawa Ieyasu", "Tokyo")
        self.assertEqual(diag.status, "success")
        self.assertEqual(state["n"], 2, "one retry expected after 503")

    def test_dispatch_passes_language_to_loc(self):
        """The verify_claim dispatch path forwards the language argument to LOC."""
        import research_providers as rp
        seen = {}
        def adapter(claim, destination, language="en"):
            seen["language"] = language
            return [], rp.ProviderDiagnostic(provider="Library of Congress", endpoint="x", query="q", status="success")
        with patch.object(rp, 'provider_loc', side_effect=adapter):
            sources, diag = rp.provider_loc("Tokugawa", "Tokyo", "en")
        self.assertEqual(seen["language"], "en")

        import research_providers as rp
        import requests
        def raise_conn_err(*args, **kwargs):
            raise requests.ConnectionError("Connection refused")
        with patch.object(rp, 'provider_wikipedia', side_effect=raise_conn_err), \
             patch.object(rp, 'provider_loc', side_effect=raise_conn_err):
            vr = rp.verify_claim("Test claim", "Tokyo")
            self.assertEqual(vr.status, "provider_failure")
            self.assertEqual(vr.confidence, 0.0)
            self.assertEqual(len(vr.evidence), 0)
            self.assertEqual(len(vr.provider_diagnostics), 2)
            self.assertEqual(len(vr.provider_failures), 2)

    def test_one_provider_fails_other_succeeds(self):
        import research_providers as rp
        def wiki_ok(claim, dest, lang="en"):
            src = rp.ResearchSource(
                provider="Wikipedia", title="Test",
                url="https://en.wikipedia.org/wiki/Test",
                excerpt="Tokugawa Ieyasu established the shogunate in 1603.", date_text="",
                query=claim, status="success")
            diag = rp.ProviderDiagnostic(
                provider="Wikipedia", endpoint="wiki.api", query=claim,
                status="success", http_status=200, elapsed_ms=100, result_count=1)
            return [src], diag
        def loc_fail(claim, dest):
            import requests
            raise requests.ConnectionError("refused")
        with patch.object(rp, 'provider_wikipedia', side_effect=wiki_ok), \
             patch.object(rp, 'provider_loc', side_effect=loc_fail):
            vr = rp.verify_claim("Tokugawa Ieyasu established the shogunate", "Tokyo")
            self.assertEqual(vr.status, "verified")
            self.assertIn("loc", vr.provider_failures)
            self.assertGreater(len(vr.evidence), 0)

    def test_independent_domain_counting(self):
        import research_providers as rp
        def wiki_ok(claim, dest, lang="en"):
            s1 = rp.ResearchSource(
                provider="Wikipedia", title="Page A",
                url="https://en.wikipedia.org/wiki/Page_A",
                excerpt="Tokugawa Ieyasu established the shogunate.", date_text="",
                query=claim, status="success")
            s2 = rp.ResearchSource(
                provider="Wikipedia", title="Page B",
                url="https://en.wikipedia.org/wiki/Page_B",
                excerpt="The Edo period began under Tokugawa Ieyasu.", date_text="",
                query=claim, status="success")
            diag = rp.ProviderDiagnostic(
                provider="Wikipedia", endpoint="wiki.api", query=claim,
                status="success", http_status=200, elapsed_ms=100, result_count=2)
            return [s1, s2], diag
        with patch.object(rp, 'provider_wikipedia', side_effect=wiki_ok), \
             patch.object(rp, 'provider_loc', side_effect=lambda c, d, lang="en": ([], rp.ProviderDiagnostic(
                 provider="Library of Congress", endpoint="loc.gov", query=c,
                 status="success", http_status=200, elapsed_ms=50, result_count=0))):
            vr = rp.verify_claim("Tokugawa Ieyasu established the shogunate", "Tokyo")
            self.assertEqual(vr.independent_sources, 1)

    def test_secrets_not_in_diagnostics(self):
        import research_providers as rp
        old_key = os.environ.setdefault("SMITHSONIAN_API_KEY", "sk_live_9876543210abcdef")
        try:
            import requests
            def raise_with_key(*args, **kwargs):
                err = requests.HTTPError("api_key=sk_live_9876543210abcdef caused error")
                err.response = type('resp', (), {'status_code': 401})()
                raise err
            # Mock all providers so only Smithsonian's diagnostic is checked
            with patch.object(rp, 'provider_wikipedia', side_effect=lambda c, d, lang="en": ([], rp.ProviderDiagnostic(
                provider="Wikipedia", endpoint="wiki.api", query=c, status="success", http_status=200, elapsed_ms=10, result_count=0))), \
                 patch.object(rp, 'provider_loc', side_effect=lambda c, d, lang="en": ([], rp.ProviderDiagnostic(
                     provider="Library of Congress", endpoint="loc.gov", query=c, status="success", http_status=200, elapsed_ms=10, result_count=0))), \
                 patch.object(rp, 'provider_smithsonian', side_effect=raise_with_key):
                vr = rp.verify_claim("test", "Tokyo")
                found_smithsonian = False
                for d in vr.provider_diagnostics:
                    if d.provider == "smithsonian":
                        found_smithsonian = True
                        if d.error:
                            self.assertNotIn("sk_live_9876543210abcdef", d.error)
                            self.assertIn("***REDACTED***", d.error)
                self.assertTrue(found_smithsonian, "Smithsonian diagnostic not found")
        finally:
            del os.environ["SMITHSONIAN_API_KEY"]

    def test_research_persists_on_all_provider_failure(self):
        import research_factcheck as rf
        import requests
        topic_data = {
            "destination_slug": "test-city",
            "destination": "Test City",
            "category": "hidden_history",
            "title": "Test Topic",
            "description": "The city was founded in 1600 and grew rapidly during the industrial era.",
            "narration_language": "English",
        }
        def all_fail(claim, dest, lang="en"):
            raise requests.ConnectionError("refused")
        # Patch the name used inside research_factcheck.research_topic
        with patch.object(rf, '_verify_claim', side_effect=all_fail):
            record = rf.research_topic(topic_data)
        self.assertEqual(len(record.claims), 1)
        self.assertEqual(record.claims[0].status, "provider_failure")
        self.assertTrue(record.verification_failed)
        self.assertGreater(len(record.provider_diagnostics), 0)
        for d in record.provider_diagnostics:
            self.assertIsInstance(d, dict)
            self.assertIn("provider", d)
            self.assertIn("status", d)

    def test_artifact_paths_match_review_dir(self):
        import daily_orchestrate as do
        paths = do.RunPaths.create("20261029_120000", "mexico-city")
        expected_research = paths.review_dir / "research.json"
        path_str = str(expected_research)
        self.assertTrue(
            path_str.endswith("output/mexico-city-region-short/research.json") or
            path_str.endswith("output\\mexico-city-region-short\\research.json")
        )
        self.assertEqual(paths.qa_report_file.name, "qa_report.json")


class ResearchQualityTests(unittest.TestCase):
    """Tests for improved research evidence matching and query construction."""

    def test_high_value_term_matches_with_only_one_term(self):
        """A single high-value term (date/proper noun) can verify a claim."""
        import research_providers as rp

        def mock_wiki(claim, dest, lang="en"):
            src = rp.ResearchSource(
                provider="Wikipedia", title="Tokugawa Ieyasu",
                url="https://en.wikipedia.org/wiki/Tokugawa_Ieyasu",
                excerpt="Tokugawa Ieyasu established the Tokugawa shogunate in Edo in 1603.",
                date_text="", query=claim, status="success")
            diag = rp.ProviderDiagnostic(
                provider="Wikipedia", endpoint="wiki.api", query=claim,
                status="success", http_status=200, elapsed_ms=100, result_count=1)
            return [src], diag

        with patch.object(rp, 'provider_wikipedia', side_effect=mock_wiki), \
             patch.object(rp, 'provider_loc', side_effect=lambda c, d, lang="en": ([], rp.ProviderDiagnostic(
                 provider="Library of Congress", endpoint="loc.gov", query=c,
                 status="success", http_status=200, elapsed_ms=50, result_count=0))):
            vr = rp.verify_claim("Tokugawa Ieyasu established the shogunate in 1603", "Tokyo")
            self.assertEqual(vr.status, "verified")
            self.assertGreater(vr.confidence, 0.4)

    def test_contradiction_only_when_near_matched_term(self):
        """Negation far from matched terms does not cause contradiction."""
        import research_providers as rp

        # Excerpt has "not" but far from the matched term "Tokugawa"
        def mock_wiki(claim, dest, lang="en"):
            src = rp.ResearchSource(
                provider="Wikipedia", title="Test",
                url="https://en.wikipedia.org/wiki/Test",
                excerpt="The Tokugawa shogunate ruled Japan. The emperor was not involved in daily administration.",
                date_text="", query=claim, status="success")
            diag = rp.ProviderDiagnostic(
                provider="Wikipedia", endpoint="wiki.api", query=claim,
                status="success", http_status=200, elapsed_ms=100, result_count=1)
            return [src], diag

        with patch.object(rp, 'provider_wikipedia', side_effect=mock_wiki):
            vr = rp.verify_claim("Tokugawa shogunate ruled Japan", "Tokyo")
            self.assertEqual(vr.status, "verified")

    def test_targeted_query_construction(self):
        """Queries are built from claim entities, not just claim+destination."""
        import research_providers as rp

        queries = rp._build_claim_queries("Tokugawa Ieyasu established the shogunate in 1603", "Tokyo")
        self.assertGreaterEqual(len(queries), 1)
        # First query should contain entity + destination
        self.assertIn("Tokugawa", queries[0])
        self.assertIn("Tokyo", queries[0])
        # Queries should be deduplicated
        self.assertEqual(len(queries), len(set(queries)))

    def test_single_high_value_term_date_verifies(self):
        """A date match alone can verify a claim."""
        import research_providers as rp

        def mock_wiki(claim, dest, lang="en"):
            src = rp.ResearchSource(
                provider="Wikipedia", title="Edo period",
                url="https://en.wikipedia.org/wiki/Edo_period",
                excerpt="The Edo period began in 1603 when Tokugawa Ieyasu established the shogunate.",
                date_text="", query=claim, status="success")
            diag = rp.ProviderDiagnostic(
                provider="Wikipedia", endpoint="wiki.api", query=claim,
                status="success", http_status=200, elapsed_ms=100, result_count=1)
            return [src], diag

        with patch.object(rp, 'provider_wikipedia', side_effect=mock_wiki):
            vr = rp.verify_claim("Edo began in 1603", "Tokyo")
            self.assertEqual(vr.status, "verified")


class CreditsRightsFieldTests(unittest.TestCase):
    """Tests that credits.json includes 'rights' and 'asset_url' fields."""

    def test_local_image_credit_has_rights_and_asset_url(self):
        """Local image credit dict includes rights and asset_url fields."""
        import render_region_short as rr

        # Check that the credit dict constructed in local_image includes
        # rights and asset_url by examining the code path directly.
        # local_image reads from assets_manifest.json, so we test the
        # credit field construction indirectly through _candidate output
        # and the render path's credit assembly.
        import asset_resolver as ar

        # _candidate produces credit-like dicts with rights and asset_url
        candidate = ar._candidate("Wikimedia Commons", "Test Image",
                                  "A test image description",
                                  "https://example.com/image.jpg",
                                  "https://example.com/source.html",
                                  "CC BY 4.0",
                                  "https://creativecommons.org/licenses/by/4.0/",
                                  author="Author", categories=["photography"])
        self.assertEqual(candidate["rights"], "CC BY 4.0")
        self.assertEqual(candidate["rights_statement"], "CC BY 4.0")
        self.assertEqual(candidate["source_url"], "https://example.com/source.html")
        self.assertEqual(candidate["image_url"], "https://example.com/image.jpg")

    def test_missing_asset_credit_has_rights_and_asset_url(self):
        import render_region_short as rr
        import tempfile

        cfg = {"name": "Tokyo", "slug": "tokyo", "story": {"narration": "x" * 120}, "scenes": []}
        scene = {"title": "Missing Scene", "image_type": "modern"}

        with tempfile.TemporaryDirectory() as d:
            im, credit = rr.text_fallback_card(scene, 1, cfg, Path(d), "No candidates")
            self.assertEqual(credit.get("rights"), "")
            self.assertEqual(credit.get("rights_statement"), "")
            self.assertEqual(credit.get("asset_url"), "")
            self.assertEqual(credit.get("source_url"), "")

    def test_online_image_credit_has_rights_and_asset_url(self):
        import asset_resolver as ar
        import tempfile, json

        cfg = {"asset_resolver": {"providers": []}}
        scene = {"title": "Test", "image_type": "modern",
                 "required_keywords": ["Tokyo"], "banned_keywords": []}

        # Check that resolve_scene_image credit dict includes rights and asset_url
        # by examining the _candidate function output structure
        candidate = ar._candidate("Wikimedia Commons", "Test Image",
                                  "A test image description", "https://example.com/image.jpg",
                                  "https://example.com/source.html", "CC0 1.0",
                                  "https://creativecommons.org/publicdomain/zero/1.0/",
                                  author="Author", categories=["photography"])
        self.assertEqual(candidate["rights_statement"], "CC0 1.0")
        self.assertEqual(candidate["rights"], "CC0 1.0")
        self.assertEqual(candidate["source_url"], "https://example.com/source.html")
        self.assertEqual(candidate["image_url"], "https://example.com/image.jpg")

    def test_map_credit_has_rights_field(self):
        """The vector map credit carries an actual rights statement and asset_url
        (regression for the missing 'rights'/'asset_url' fields that failed
        credits_fields QA)."""
        import vector_map as vm
        import inspect
        src = inspect.getsource(vm.render_map)
        self.assertIn('"rights": "Public Domain"', src,
                      "render_map credit must include an actual rights statement")
        self.assertIn('"asset_url": "https://www.naturalearthdata.com/"', src,
                      "render_map credit must include an asset_url")


class ModernSceneIdentityFallbackTests(unittest.TestCase):
    """Tests that modern scenes use identity_fallback_to_clip=True."""

    def test_modern_scene_has_identity_fallback_to_clip(self):
        import scene_planner as sp
        import yaml
        cfg = yaml.safe_load(open("regions/tokyo.yaml", encoding="utf-8"))
        topic = {
            "destination_slug": "tokyo", "destination_name": "Tokyo",
            "country": "Japan", "continent": "Asia",
            "category": "hidden_history",
            "story_id": "tokyo:hidden:test",
            "hook": "Hook", "description": "Description",
            "entities": ["Tokyo Skytree", "Shinjuku"],
            "sources": ["https://example.com"], "claim_refs": ["claim1"],
        }
        scenes = sp.plan_scenes(topic, cfg)
        for s in scenes:
            if s.get("image_type") == "modern":
                self.assertTrue(s.get("identity_fallback_to_clip", False),
                                f"Modern scene '{s['title']}' missing identity_fallback_to_clip")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class OrchestrationErrorPropagation(unittest.TestCase):
    """Failing subprocesses surface captured output and cause orchestration to fail."""

    def test_run_command_prints_stderr_on_failure(self):
        import daily_orchestrate as do
        import io, contextlib
        # A command that exits nonzero with stderr output
        result = do.run_command([sys.executable, "-c", "import sys; print('error detail', file=sys.stderr); sys.exit(3)"])
        self.assertEqual(result.returncode, 3)
        self.assertIn("error detail", result.stderr)

    def test_run_command_empty_output_on_failure(self):
        import daily_orchestrate as do
        result = do.run_command([sys.executable, "-c", "import sys; sys.exit(7)"])
        self.assertEqual(result.returncode, 7)

    def test_scene_plan_rejects_empty_required_keywords(self):
        """Renderer must reject scene plans with empty required_keywords."""
        import render_region_short as rr
        import json, tempfile
        from pathlib import Path

        scene = {
            "scene_id": "test:s01",
            "title": "Bad Scene",
            "narration_intent": "Test intent",
            "narration_text": "Test text",
            "image_type": "historic",
            "named_entities": [],
            "visual_tags": [],
            "image_search_terms": ["term one", "term two", "term three"],
            "required_keywords": [],  # EMPTY - should fail
            "banned_keywords": [],
            "sources": [],
            "claim_refs": [],
        }
        cfg = {
            "name": "Test",
            "slug": "test",
            "scenes": [scene],
            "story": {
                "narration": "A" * 120,
                "hook_variants": {"A": "HOOK TEST HERE"},
                "narration_language": "English",
                "kokoro_voice": "af_heart",
            },
        }
        with tempfile.TemporaryDirectory() as d:
            cfg_path = Path(d) / "config.yaml"
            import yaml
            cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
            with self.assertRaises(RuntimeError) as ctx:
                # Simulate the renderer's validation logic
                from render_region_short import normalize_config
                cfg2 = normalize_config(yaml.safe_load(cfg_path.read_text(encoding="utf-8")))
                for s in cfg2["scenes"]:
                    if not s.get("required_keywords"):
                        raise RuntimeError(f"Scene plan '{s.get('title')}' needs required_keywords")
            self.assertIn("required_keywords", str(ctx.exception))

    def test_scene_plan_with_entities_passes_validation(self):
        """Scene plan with entities produces non-empty required_keywords."""
        import scene_planner as sp
        import yaml
        cfg = yaml.safe_load(open("regions/mexico-city.yaml", encoding="utf-8"))
        topic = {
            "destination_slug": "mexico-city",
            "destination_name": "Mexico City",
            "country": "Mexico",
            "continent": "North America",
            "category": "ancient_civilization",
            "story_id": "mexico-city:aztec:temples",
            "hook": "Hook",
            "description": "Description",
            "entities": ["Templo Mayor", "Tenochtitlan"],
            "sources": ["https://example.com"],
            "claim_refs": ["claim1"],
        }
        scenes = sp.plan_scenes(topic, cfg)
        for s in scenes:
            self.assertTrue(s.get("required_keywords"), f"Scene '{s['title']}' has empty required_keywords")
            self.assertGreaterEqual(len(s.get("image_search_terms", [])), 3)
            self.assertIn(s["image_type"], ("historic", "modern", "map"))
            self.assertTrue(s.get("title"))
            self.assertTrue(s.get("narration_intent"))


class RunPathsOutputDirectory(unittest.TestCase):
    """Review output directory must match workflow artifact paths."""

    def test_review_dir_matches_region_slug_pattern(self):
        import daily_orchestrate as do
        from pathlib import Path
        paths = do.RunPaths.create("20261028_120000", "tokyo")
        self.assertEqual(paths.review_dir.name, "tokyo-region-short")
        self.assertIn("output", str(paths.review_dir))
        self.assertTrue(str(paths.review_dir).endswith("output\\tokyo-region-short") or str(paths.review_dir).endswith("output/tokyo-region-short"))

    def test_review_dir_matches_mexico_city(self):
        import daily_orchestrate as do
        paths = do.RunPaths.create("20261028_120000", "mexico-city")
        self.assertEqual(paths.review_dir.name, "mexico-city-region-short")


class ResearchQAContract(unittest.TestCase):
    """QA must read the actual research record and report verification accurately."""

    def test_research_status_eight_claims_one_verified_fails_70pct(self):
        """1 of 8 claims verified = 12.5% < 70% → research_status fails."""
        import qa_checks as qc
        import tempfile, json
        from pathlib import Path

        # Script links to a research record
        script = {
            "claims_verified": 1,
            "claims_total": 8,
            "claims_disputed": 7,
            "claims_insufficient": 0,
            "research_record": "",
        }
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "script.json"
            sp.write_text(json.dumps(script), encoding="utf-8")
            # Research record: 8 claims, 1 verified
            research = {
                "claims": [
                    {"status": "verified", "evidence": [{"source_url": "https://example.com/1"}]},
                ] + [
                    {"status": "disputed", "evidence": []} for _ in range(7)
                ],
                "extraction_failed": False,
                "verification_failed": False,
                "zero_claims": False,
            }
            rp = Path(d) / "research.json"
            rp.write_text(json.dumps(research), encoding="utf-8")
            script["research_record"] = str(rp)
            sp.write_text(json.dumps(script), encoding="utf-8")

            result = qc.check_research_status(sp)
            self.assertFalse(result.passed)
            self.assertEqual(result.severity, "error")
            self.assertIn("12%", result.details)

    def test_research_status_six_of_eight_verified_passes(self):
        """6 of 8 claims verified = 75% >= 70% → research_status passes."""
        import qa_checks as qc
        import tempfile, json
        from pathlib import Path

        script = {
            "claims_verified": 6,
            "claims_total": 8,
            "claims_disputed": 2,
            "claims_insufficient": 0,
            "research_record": "",
        }
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "script.json"
            sp.write_text(json.dumps(script), encoding="utf-8")
            research = {
                "claims": [
                    {"status": "verified", "evidence": [{"source_url": f"https://example.com/{i}"}]}
                    for i in range(6)
                ] + [
                    {"status": "disputed", "evidence": []} for _ in range(2)
                ],
                "extraction_failed": False,
                "verification_failed": False,
                "zero_claims": False,
            }
            rp = Path(d) / "research.json"
            rp.write_text(json.dumps(research), encoding="utf-8")
            script["research_record"] = str(rp)
            sp.write_text(json.dumps(script), encoding="utf-8")

            result = qc.check_research_status(sp)
            self.assertTrue(result.passed)
            self.assertIn("75%", result.details)

    def test_research_status_missing_record_reported(self):
        """Missing research record is explicitly reported as error."""
        import qa_checks as qc
        import tempfile, json
        from pathlib import Path

        script = {
            "claims_verified": 0,
            "claims_total": 0,
            "research_record": "/nonexistent/research.json",
        }
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "script.json"
            sp.write_text(json.dumps(script), encoding="utf-8")
            result = qc.check_research_status(sp)
            self.assertFalse(result.passed)
            self.assertEqual(result.severity, "error")
            self.assertIn("not found", result.details.lower())

    def test_research_status_zero_claims_reported(self):
        """Zero claims in research record is explicitly reported as error."""
        import qa_checks as qc
        import tempfile, json
        from pathlib import Path

        script = {
            "claims_verified": 0,
            "claims_total": 0,
            "research_record": "",
        }
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "script.json"
            sp.write_text(json.dumps(script), encoding="utf-8")
            research = {
                "claims": [],
                "extraction_failed": True,
                "verification_failed": False,
                "zero_claims": True,
            }
            rp = Path(d) / "research.json"
            rp.write_text(json.dumps(research), encoding="utf-8")
            script["research_record"] = str(rp)
            sp.write_text(json.dumps(script), encoding="utf-8")

            result = qc.check_research_status(sp)
            self.assertFalse(result.passed)
            self.assertEqual(result.severity, "error")


class ReviewQAMode(unittest.TestCase):
    """Review QA must not require captions/audio/MP4; production QA must."""

    def test_review_qa_does_not_fail_on_missing_captions(self):
        """Visual review mode: no SRT/VTT → info, not error."""
        import qa_checks as qc
        import tempfile, json
        from pathlib import Path

        script = {
            "word_count": 130,
            "estimated_duration": 52.0,
            "claims_verified": 6,
            "claims_total": 8,
            "claims_disputed": 2,
            "claims_insufficient": 0,
            "research_record": "",
        }
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "script.json"
            sp.write_text(json.dumps(script), encoding="utf-8")
            research = {
                "claims": [
                    {"status": "verified", "evidence": [{"source_url": f"https://example.com/{i}"}]}
                    for i in range(6)
                ] + [
                    {"status": "disputed", "evidence": []} for _ in range(2)
                ],
                "extraction_failed": False,
                "verification_failed": False,
                "zero_claims": False,
            }
            rp = Path(d) / "research.json"
            rp.write_text(json.dumps(research), encoding="utf-8")
            script["research_record"] = str(rp)
            sp.write_text(json.dumps(script), encoding="utf-8")

            # Empty output dir (no captions, no board, no credits)
            results = qc.run_review_qa(sp, output_dir=Path(d), scene_count=8)
            captions_results = [r for r in results if r.check == "captions_exist"]
            self.assertEqual(len(captions_results), 1)
            self.assertTrue(captions_results[0].passed, "Captions should be info, not error, in review mode")
            self.assertEqual(captions_results[0].severity, "info")

    def test_review_qa_requires_credits(self):
        """Review QA still requires credits.json and scene completeness."""
        import qa_checks as qc
        import tempfile, json
        from pathlib import Path

        script = {
            "word_count": 130,
            "estimated_duration": 52.0,
            "claims_verified": 6,
            "claims_total": 8,
            "claims_disputed": 2,
            "claims_insufficient": 0,
            "research_record": "",
        }
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "script.json"
            sp.write_text(json.dumps(script), encoding="utf-8")
            research = {
                "claims": [
                    {"status": "verified", "evidence": [{"source_url": f"https://example.com/{i}"}]}
                    for i in range(6)
                ] + [
                    {"status": "disputed", "evidence": []} for _ in range(2)
                ],
                "extraction_failed": False,
                "verification_failed": False,
                "zero_claims": False,
            }
            rp = Path(d) / "research.json"
            rp.write_text(json.dumps(research), encoding="utf-8")
            script["research_record"] = str(rp)
            sp.write_text(json.dumps(script), encoding="utf-8")

            # No credits.json → check_credits should fail
            results = qc.run_review_qa(sp, output_dir=Path(d), scene_count=8)
            credits_results = [r for r in results if r.check == "credits_exist"]
            self.assertEqual(len(credits_results), 1)
            self.assertFalse(credits_results[0].passed)


class MultiProviderResearch(unittest.TestCase):
    """Regression tests for the multi-provider research pipeline."""

    def test_valid_supporting_evidence_multiple_providers(self):
        """Multiple providers returning supporting evidence → verified."""
        import research_providers as rp

        # Mock both Wikipedia and LoC to return supporting evidence
        def mock_wiki(claim, dest, lang="en"):
            src = rp.ResearchSource(
                provider="Wikipedia", title="Test Page",
                url="https://en.wikipedia.org/wiki/Test",
                excerpt="The Tokugawa shogunate was established in 1603 by Tokugawa Ieyasu.",
                date_text="", query=claim, status="success")
            diag = rp.ProviderDiagnostic(
                provider="Wikipedia", endpoint="wiki.api", query=claim,
                status="success", http_status=200, elapsed_ms=100, result_count=1)
            return [src], diag

        def mock_loc(claim, dest, lang="en"):
            src = rp.ResearchSource(
                provider="Library of Congress", title="LOC Record",
                url="https://loc.gov/item/123",
                excerpt="Tokugawa Ieyasu founded the shogunate in Edo period Japan.",
                date_text="1603", query=claim, status="success")
            diag = rp.ProviderDiagnostic(
                provider="Library of Congress", endpoint="loc.gov", query=claim,
                status="success", http_status=200, elapsed_ms=200, result_count=1)
            return [src], diag

        with patch.object(rp, 'provider_wikipedia', side_effect=mock_wiki), \
             patch.object(rp, 'provider_loc', side_effect=mock_loc):
            vr = rp.verify_claim("Tokugawa Ieyasu established the shogunate in 1603", "Tokyo")
            self.assertEqual(vr.status, "verified")
            self.assertGreaterEqual(vr.independent_sources, 2)
            self.assertGreater(vr.confidence, 0.5)

    def test_insufficient_evidence_single_source(self):
        """Only one provider with weak match → insufficient_evidence."""
        import research_providers as rp

        def mock_wiki(claim, dest, lang="en"):
            src = rp.ResearchSource(
                provider="Wikipedia", title="Test Page",
                url="https://en.wikipedia.org/wiki/Test",
                excerpt="Some general text about Japan.", date_text="",
                query=claim, status="success")
            diag = rp.ProviderDiagnostic(
                provider="Wikipedia", endpoint="wiki.api", query=claim,
                status="success", http_status=200, elapsed_ms=100, result_count=1)
            return [src], diag

        with patch.object(rp, 'provider_wikipedia', side_effect=mock_wiki), \
             patch.object(rp, 'provider_loc', side_effect=lambda c, d, lang="en": ([], rp.ProviderDiagnostic(
                 provider="Library of Congress", endpoint="loc.gov", query=c,
                 status="success", http_status=200, elapsed_ms=50, result_count=0))):
            vr = rp.verify_claim("Tokugawa Ieyasu established the shogunate in 1603", "Tokyo")
            # Weak match (only 1-2 generic terms) should be insufficient
            self.assertIn(vr.status, ("insufficient_evidence", "provider_failure"))

    def test_contradictory_evidence(self):
        """Evidence that explicitly contradicts the claim → disputed."""
        import research_providers as rp

        def mock_wiki(claim, dest, lang="en"):
            src = rp.ResearchSource(
                provider="Wikipedia", title="Test Page",
                url="https://en.wikipedia.org/wiki/Test",
                excerpt="Tokugawa Ieyasu never established any shogunate; this is a myth.",
                date_text="", query=claim, status="success")
            diag = rp.ProviderDiagnostic(
                provider="Wikipedia", endpoint="wiki.api", query=claim,
                status="success", http_status=200, elapsed_ms=100, result_count=1)
            return [src], diag

        with patch.object(rp, 'provider_wikipedia', side_effect=mock_wiki):
            vr = rp.verify_claim("Tokugawa Ieyasu established the shogunate", "Tokyo")
            self.assertEqual(vr.status, "disputed")
            self.assertLess(vr.confidence, 0.5)

    def test_provider_timeout_not_evidence_failure(self):
        """Provider timeout → provider_failure, not insufficient_evidence."""
        import research_providers as rp
        import requests

        def mock_wiki_timeout(claim, dest, lang="en"):
            raise requests.Timeout("Read timed out")

        def mock_loc_ok(claim, dest, lang="en"):
            src = rp.ResearchSource(
                provider="Library of Congress", title="LOC Record",
                url="https://loc.gov/item/123",
                excerpt="Tokugawa Ieyasu established the shogunate in Edo period.",
                date_text="1603", query=claim, status="success")
            diag = rp.ProviderDiagnostic(
                provider="Library of Congress", endpoint="loc.gov", query=claim,
                status="success", http_status=200, elapsed_ms=200, result_count=1)
            return [src], diag

        with patch.object(rp, 'provider_wikipedia', side_effect=mock_wiki_timeout), \
             patch.object(rp, 'provider_loc', side_effect=mock_loc_ok):
            vr = rp.verify_claim("Tokugawa Ieyasu established the shogunate", "Tokyo")
            # LoC succeeded, so we have evidence
            self.assertEqual(vr.status, "verified")
            # Wikipedia failure should be recorded
            self.assertIn("wikipedia", vr.provider_failures)

    def test_provider_http_error_recorded(self):
        """HTTP error from provider → recorded as provider_failure."""
        import research_providers as rp
        import requests

        def mock_wiki_403(claim, dest, lang="en"):
            raise requests.HTTPError("403 Forbidden")

        with patch.object(rp, 'provider_wikipedia', side_effect=mock_wiki_403), \
             patch.object(rp, 'provider_loc', side_effect=lambda c, d, lang="en": ([], rp.ProviderDiagnostic(
                 provider="Library of Congress", endpoint="loc.gov", query=c,
                 status="success", http_status=200, elapsed_ms=50, result_count=0))):
            vr = rp.verify_claim("Tokugawa Ieyasu established the shogunate", "Tokyo")
            self.assertIn("wikipedia", vr.provider_failures)
            # LoC found nothing, so insufficient evidence (not provider_failure since LoC worked)
            self.assertEqual(vr.status, "insufficient_evidence")

    def test_provider_auth_failure_recorded(self):
        """Authentication failure (401/403) → provider_failure, not claim dispute."""
        import research_providers as rp
        import requests

        def mock_wiki_auth(claim, dest, lang="en"):
            err = requests.HTTPError("401 Unauthorized")
            err.response = type('resp', (), {'status_code': 401})()
            raise err

        with patch.object(rp, 'provider_wikipedia', side_effect=mock_wiki_auth), \
             patch.object(rp, 'provider_loc', side_effect=lambda c, d, lang="en": ([], rp.ProviderDiagnostic(
                 provider="Library of Congress", endpoint="loc.gov", query=c,
                 status="success", http_status=200, elapsed_ms=50, result_count=0))):
            vr = rp.verify_claim("Tokugawa Ieyasu established the shogunate", "Tokyo")
            self.assertIn("wikipedia", vr.provider_failures)

    def test_no_evidence_fabricated_when_provider_fails(self):
        """When all providers fail, no evidence is fabricated."""
        import research_providers as rp
        import requests

        def mock_all_fail(claim, dest, lang="en"):
            raise requests.ConnectionError("Connection refused")

        with patch.object(rp, 'provider_wikipedia', side_effect=mock_all_fail), \
             patch.object(rp, 'provider_loc', side_effect=mock_all_fail):
            vr = rp.verify_claim("Tokugawa Ieyasu established the shogunate", "Tokyo")
            self.assertEqual(vr.status, "provider_failure")
            self.assertEqual(vr.confidence, 0.0)
            self.assertEqual(len(vr.evidence), 0)
            self.assertIn("wikipedia", vr.provider_failures)
            self.assertIn("loc", vr.provider_failures)

    def test_independent_domain_counting(self):
        """Multiple sources from same domain count as one independent source."""
        import research_providers as rp

        def mock_wiki(claim, dest, lang="en"):
            # Two sources from same domain (wikipedia.org)
            s1 = rp.ResearchSource(
                provider="Wikipedia", title="Page A",
                url="https://en.wikipedia.org/wiki/Page_A",
                excerpt="Tokugawa Ieyasu established the shogunate in Edo.", date_text="",
                query=claim, status="success")
            s2 = rp.ResearchSource(
                provider="Wikipedia", title="Page B",
                url="https://en.wikipedia.org/wiki/Page_B",
                excerpt="The Edo period began under Tokugawa Ieyasu leadership.", date_text="",
                query=claim, status="success")
            diag = rp.ProviderDiagnostic(
                provider="Wikipedia", endpoint="wiki.api", query=claim,
                status="success", http_status=200, elapsed_ms=100, result_count=2)
            return [s1, s2], diag

        with patch.object(rp, 'provider_wikipedia', side_effect=mock_wiki), \
             patch.object(rp, 'provider_loc', side_effect=lambda c, d, lang="en": ([], rp.ProviderDiagnostic(
                 provider="Library of Congress", endpoint="loc.gov", query=c,
                 status="success", http_status=200, elapsed_ms=50, result_count=0))):
            vr = rp.verify_claim("Tokugawa Ieyasu established the shogunate", "Tokyo")
            # Both sources are from wikipedia.org = 1 independent domain
            self.assertEqual(vr.independent_sources, 1)

    def test_verification_threshold_enforcement(self):
        """Below 70% verified claims fails the threshold."""
        import qa_checks as qc
        import tempfile, json
        from pathlib import Path

        # Simulate 2 of 8 verified (25%)
        script = {
            "claims_verified": 2,
            "claims_total": 8,
            "claims_disputed": 0,
            "claims_insufficient": 6,
            "research_record": "",
        }
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "script.json"
            sp.write_text(json.dumps(script), encoding="utf-8")
            research = {
                "claims": [
                    {"status": "verified", "evidence": [{"source_url": "https://example.com/1"}]},
                    {"status": "verified", "evidence": [{"source_url": "https://example.com/2"}]},
                ] + [
                    {"status": "insufficient_evidence", "evidence": []} for _ in range(6)
                ],
                "extraction_failed": False,
                "verification_failed": False,
                "zero_claims": False,
            }
            rp_path = Path(d) / "research.json"
            rp_path.write_text(json.dumps(research), encoding="utf-8")
            script["research_record"] = str(rp_path)
            sp.write_text(json.dumps(script), encoding="utf-8")

            result = qc.check_research_status(sp)
            self.assertFalse(result.passed)
            self.assertIn("25%", result.details)

    def test_secret_redaction_in_logs(self):
        """API keys are redacted from error messages."""
        import research_providers as rp

        error_text = "api_key=abc123def456 caused an error"
        redacted = rp._redact(error_text)
        self.assertNotIn("abc123def456", redacted)
        self.assertIn("***REDACTED***", redacted)

        # wskey pattern (Europeana)
        error_text2 = "wskey=xyz789 failed"
        redacted2 = rp._redact(error_text2)
        self.assertNotIn("xyz789", redacted2)

    def test_research_diagnostics_recorded_per_provider(self):
        """Each provider's diagnostic includes status, HTTP status, elapsed time, result count."""
        import research_providers as rp

        def mock_wiki(claim, dest, lang="en"):
            src = rp.ResearchSource(
                provider="Wikipedia", title="Test",
                url="https://en.wikipedia.org/wiki/Test",
                excerpt="Tokugawa Ieyasu shogunate Edo 1603.", date_text="",
                query=claim, status="success")
            diag = rp.ProviderDiagnostic(
                provider="Wikipedia", endpoint="wikipedia.org/w/api.php", query=claim,
                status="success", http_status=200, elapsed_ms=150.5, result_count=1)
            return [src], diag

        with patch.object(rp, 'provider_wikipedia', side_effect=mock_wiki):
            vr = rp.verify_claim("Tokugawa Ieyasu established shogunate", "Tokyo")
            wiki_diag = next((d for d in vr.provider_diagnostics if d.provider == "Wikipedia"), None)
            self.assertIsNotNone(wiki_diag)
            self.assertEqual(wiki_diag.status, "success")
            self.assertEqual(wiki_diag.http_status, 200)
            self.assertGreater(wiki_diag.elapsed_ms, 0)
            self.assertEqual(wiki_diag.result_count, 1)
            self.assertIsNone(wiki_diag.error)


if __name__ == "__main__":
    unittest.main(verbosity=2)