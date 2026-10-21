#!/usr/bin/env python3
"""Regression tests that replay realistic provider responses through the real resolver code paths.

Schemas follow the providers' own documentation (AIC: image_id is a top-level field and `fields` selects
what is returned; LoC: `id` is an http URL, `url` is https, image_url entries can be protocol-relative and an
empty search is an HTTP 404; Cleveland: images.web is the small derivative, images.print the large one).
No network, no models."""
import io, sys, tempfile, unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main(verbosity=2)