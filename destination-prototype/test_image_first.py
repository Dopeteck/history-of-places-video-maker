#!/usr/bin/env python3
"""Acceptance tests for the image-first global history-shorts pipeline.

Covers:
- Discovery and inventory creation without a pre-authored storyboard.
- Correct licence and attribution handling (rights allowlist enforced).
- Inventory persistence, pagination and deduplication.
- Region-first classification and transparent relevance decisions.
- Story selection from available assets and verified claims.
- Scene plans that select only inventoried assets.
- Honest failure when available material is inadequate.

Providers are mocked so tests run offline with no network access.
"""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from datetime import date
from unittest.mock import patch

sys.path.insert(0, "destination-prototype")

from PIL import Image

import image_inventory as ii
import story_from_inventory as sfi


def _make_image_bytes(size=(800, 800), fmt="JPEG"):
    buf = io.BytesIO()
    Image.new("RGB", size, (120, 130, 140)).save(buf, format=fmt)
    return buf.getvalue()


def _fake_candidate(provider, title, rights="Public Domain", source="https://example.com/item/1",
                    image="https://example.com/img/1.jpg", author="An Author", date="1850",
                    categories=None, facets=None):
    return {
        "provider": provider,
        "title": title,
        "description": f"A {title} from {provider}.",
        "image_url": image,
        "source_url": source,
        "rights": rights,
        "rights_statement": rights,
        "rights_url": "https://creativecommons.org/publicdomain/mark/1.0/",
        "author": author,
        "identifier": str(source),
        "categories": categories or [],
        "facets": facets or [],
        "date_text": date,
        "year_range": [1850, 1860],
    }


class FakeRegionConfig(dict):
    def __init__(self):
        super().__init__({
            "slug": "test-city",
            "name": "Test City",
            "country": "Testland",
            "place_names": [{"spelling": "Test City"}, {"spelling": "Old Test"}],
            "story": {"category": "hidden_history", "narration_language": "English",
                      "kokoro_voice": "af_heart"},
            "period_focus": "1800 to 1900",
            "sources": ["https://en.wikipedia.org/wiki/Test_City"],
            "asset_resolver": {
                "providers": ["_loc", "_met", "_aic"],
                "clip_min_score": 0.22,
                "max_downloads": 20,
            },
        })


@contextlib.contextmanager
def _providers_patched(providers):
    """Patch PROVIDERS and make _provider_enabled accept them all."""
    with patch.object(ii, "PROVIDERS", new=providers), \
         patch.object(ii, "_provider_enabled", return_value=True):
        yield


class InventoryDiscoveryTests(unittest.TestCase):
    """Discovery and inventory creation without a pre-authored storyboard."""

    def setUp(self):
        self.cfg = FakeRegionConfig()
        self.candidates = [
            _fake_candidate("_loc", "Test City harbor", source="https://loc.gov/item/1",
                            image="https://loc.gov/img/1.jpg"),
            _fake_candidate("_met", "Old Test street", source="https://metmuseum.org/2",
                            image="https://metmuseum.org/img/2.jpg"),
            _fake_candidate("_aic", "Test City market", source="https://artic.edu/3",
                            image="https://artic.edu/img/3.jpg"),
        ]

    def test_discovery_creates_eligible_inventory_without_storyboard(self):
        def loc(q): return [self.candidates[0]]
        def met(q): return [self.candidates[1]]
        def aic(q): return [self.candidates[2]]
        loc.__name__ = "_loc"; met.__name__ = "_met"; aic.__name__ = "_aic"
        with _providers_patched([loc, met, aic]), \
             patch.object(ii, "_download_image",
                          return_value=(_make_image_bytes(), "image/jpeg", (400, 400))), \
             patch.object(ii, "_clip_relevance", return_value=0.6), \
             patch.object(ii, "_is_text_heavy", return_value=False):
            inv = ii.discover_inventory(self.cfg, max_downloads=10)
        self.assertGreaterEqual(inv.total_eligible, 1)
        self.assertEqual(len(inv.coverage), 3)
        for c in inv.coverage:
            self.assertFalse(c.skipped, f"provider {c.provider} should not be skipped")

    def test_persists_and_reloads(self):
        def loc(q): return [self.candidates[0]]
        loc.__name__ = "_loc"
        with _providers_patched([loc]), \
             patch.object(ii, "_download_image",
                          return_value=(_make_image_bytes(), "image/jpeg", (400, 400))), \
             patch.object(ii, "_clip_relevance", return_value=0.6), \
             patch.object(ii, "_is_text_heavy", return_value=False):
            inv1 = ii.discover_inventory(self.cfg, max_downloads=10)
        self.assertTrue(ii.inventory_path("test-city").exists())
        inv2 = ii.load_inventory("test-city")
        self.assertIsNotNone(inv2)
        self.assertEqual(inv2.region_slug, "test-city")
        self.assertEqual(len(inv2.items), len(inv1.items))

    def test_duplicates_are_deduplicated(self):
        c1 = _fake_candidate("_loc", "Same image", source="https://loc.gov/item/dup",
                             image="https://loc.gov/img/dup.jpg")
        c2 = _fake_candidate("_met", "Same image again", source="https://loc.gov/item/dup",
                             image="https://metmuseum.org/img/dup.jpg")
        def loc(q): return [c1]
        def met(q): return [c2]
        loc.__name__ = "_loc"; met.__name__ = "_met"
        with _providers_patched([loc, met]), \
             patch.object(ii, "_download_image",
                          return_value=(_make_image_bytes(), "image/jpeg", (400, 400))), \
             patch.object(ii, "_clip_relevance", return_value=0.6), \
             patch.object(ii, "_is_text_heavy", return_value=False):
            inv = ii.discover_inventory(self.cfg, max_downloads=10)
        urls = [i.source_url for i in inv.items]
        self.assertEqual(urls.count("https://loc.gov/item/dup"), 1,
                         "same source_url must be deduplicated")


class RightsAndAttributionTests(unittest.TestCase):
    """Correct licence and attribution handling."""

    def setUp(self):
        self.cfg = FakeRegionConfig()

    def test_unacceptable_rights_are_rejected(self):
        bad = _fake_candidate("_loc", "Non-free image", rights="All Rights Reserved",
                              source="https://loc.gov/item/nc", image="https://loc.gov/img/nc.jpg")
        ccbync = _fake_candidate("_met", "NC image", rights="CC BY-NC",
                                 source="https://metmuseum.org/2", image="https://metmuseum.org/img/2.jpg")
        good = _fake_candidate("_aic", "PD image", rights="Public Domain",
                               source="https://artic.edu/3", image="https://artic.edu/img/3.jpg")
        def loc(q): return [bad, ccbync, good]
        loc.__name__ = "_loc"
        with _providers_patched([loc]), \
             patch.object(ii, "_download_image",
                          return_value=(_make_image_bytes(), "image/jpeg", (400, 400))), \
             patch.object(ii, "_clip_relevance", return_value=0.6), \
             patch.object(ii, "_is_text_heavy", return_value=False):
            inv = ii.discover_inventory(self.cfg, max_downloads=10)
        titles = [i.title for i in inv.items]
        self.assertNotIn("Non-free image", titles)
        self.assertNotIn("NC image", titles)
        self.assertIn("PD image", titles)

    def test_banned_metadata_is_rejected(self):
        banned = _fake_candidate("_loc", "Test City book cover", source="https://loc.gov/item/b",
                                 image="https://loc.gov/img/b.jpg")
        good = _fake_candidate("_met", "Test City street", source="https://metmuseum.org/2",
                               image="https://metmuseum.org/img/2.jpg")
        def loc(q): return [banned, good]
        loc.__name__ = "_loc"
        with _providers_patched([loc]), \
             patch.object(ii, "_download_image",
                          return_value=(_make_image_bytes(), "image/jpeg", (400, 400))), \
             patch.object(ii, "_clip_relevance", return_value=0.6), \
             patch.object(ii, "_is_text_heavy", return_value=False):
            inv = ii.discover_inventory(self.cfg, max_downloads=10)
        titles = [i.title for i in inv.items]
        self.assertNotIn("Test City book cover", titles)
        self.assertIn("Test City street", titles)

    def test_non_https_urls_are_rejected(self):
        http = _fake_candidate("_loc", "HTTP image", source="http://loc.gov/item/1",
                               image="http://loc.gov/img/1.jpg")
        good = _fake_candidate("_met", "HTTPS image", source="https://metmuseum.org/2",
                               image="https://metmuseum.org/img/2.jpg")
        def loc(q): return [http, good]
        loc.__name__ = "_loc"
        with _providers_patched([loc]), \
             patch.object(ii, "_download_image",
                          return_value=(_make_image_bytes(), "image/jpeg", (400, 400))), \
             patch.object(ii, "_clip_relevance", return_value=0.6), \
             patch.object(ii, "_is_text_heavy", return_value=False):
            inv = ii.discover_inventory(self.cfg, max_downloads=10)
        titles = [i.title for i in inv.items]
        self.assertNotIn("HTTP image", titles)
        self.assertIn("HTTPS image", titles)


class RegionClassificationTests(unittest.TestCase):
    """Region-first classification and transparent relevance decisions."""

    def test_items_are_region_relevant_by_metadata(self):
        cfg = FakeRegionConfig()
        local = _fake_candidate("_loc", "Test City cathedral", source="https://loc.gov/1",
                                image="https://loc.gov/img/1.jpg", categories=["cathedrals", "Test City"])
        unrelated = _fake_candidate("_met", "Paris tower", source="https://metmuseum.org/2",
                                    image="https://metmuseum.org/img/2.jpg", categories=["Paris", "tower"])
        def loc(q): return [local, unrelated]
        loc.__name__ = "_loc"
        with _providers_patched([loc]), \
             patch.object(ii, "_download_image",
                          return_value=(_make_image_bytes(), "image/jpeg", (400, 400))), \
             patch.object(ii, "_clip_relevance", return_value=None), \
             patch.object(ii, "_is_text_heavy", return_value=False):
            inv = ii.discover_inventory(cfg, max_downloads=10)
        scores = {i.title: i.relevance_score for i in inv.items}
        self.assertGreater(scores.get("Test City cathedral", 0), scores.get("Paris tower", 0))

    def test_coverage_records_errors_transparently(self):
        def failing(q):
            raise RuntimeError("upstream down")
        failing.__name__ = "_loc"
        cfg = FakeRegionConfig()
        with _providers_patched([failing]), \
             patch.object(ii, "_download_image", return_value=None):
            inv = ii.discover_inventory(cfg, max_downloads=10)
        self.assertEqual(len(inv.coverage), 1)
        self.assertTrue(inv.coverage[0].errors)
        self.assertIn("upstream down", inv.coverage[0].errors[0])


class StorySelectionTests(unittest.TestCase):
    """Story selection from available assets and verified claims."""

    def _make_items(self, theme, count=8, provider="_loc"):
        items = []
        for j in range(count):
            items.append(ii.InventoryItem(
                provider=provider, title=f"{theme} item {j}", description=f"a {theme} thing",
                image_url=f"https://ex.com/{theme}/{j}.jpg",
                source_url=f"https://ex.com/item/{theme}/{j}", rights="Public Domain",
                rights_statement="Public Domain", rights_url="", license_url="", author="A",
                identifier=str(j), date_text="1850", year_range=[1850, 1860],
                dimensions=[400, 400], digest=f"digest-{theme}-{j}", clip_score=0.5,
                relevance_score=0.7, eligible=True, discovered_via=theme, discovered_at=str(date.today())))
        return items

    def test_story_selected_from_inventory_clusters(self):
        arch_items = self._make_items("architecture", 8)
        trade_items = self._make_items("trade", 3)
        inv = ii.RegionInventory(region_slug="tc", region_name="Test City", country="Testland",
                                 created_at=str(date.today()), items=arch_items + trade_items)
        clusters = sfi.cluster_inventory(inv)
        themes = {t for t, _ in clusters}
        self.assertIn("architecture", themes)
        story = sfi.select_story(clusters)
        self.assertIsNotNone(story)
        self.assertGreaterEqual(len(story.assets), 6)
        self.assertGreater(story.total_score, 0)

    def test_insufficient_assets_yield_honest_failure(self):
        few = self._make_items("architecture", 3)
        inv = ii.RegionInventory(region_slug="tc", region_name="Test City", country="Testland",
                                 created_at=str(date.today()), items=few)
        clusters = sfi.cluster_inventory(inv)
        story = sfi.select_story(clusters)
        self.assertIsNotNone(story.rejection_reason)
        self.assertIn("insufficient", story.rejection_reason[0])

    def test_empty_inventory_yields_no_story(self):
        inv = ii.RegionInventory(region_slug="tc", region_name="Test City", country="Testland",
                                 created_at=str(date.today()), items=[])
        clusters = sfi.cluster_inventory(inv)
        self.assertEqual(clusters, [])
        story = sfi.select_story(clusters)
        self.assertIsNone(story)


class ScenePlanningTests(unittest.TestCase):
    """Scene plans that select only inventoried assets."""

    def test_scenes_reference_only_inventory_assets(self):
        items = []
        for j in range(9):
            items.append(ii.InventoryItem(
                provider="_loc", title=f"Asset {j}", description=f"desc {j}",
                image_url=f"https://ex.com/a{j}.jpg", source_url=f"https://ex.com/item/j{j}",
                rights="CC0", rights_statement="CC0", rights_url="", license_url="",
                author="Author", identifier=str(j), date_text="1860", year_range=[1860, 1870],
                dimensions=[800, 600], digest=f"d-{j}", clip_score=0.5, relevance_score=0.8,
                eligible=True, discovered_via="q", discovered_at=str(date.today())))
        inv = ii.RegionInventory(region_slug="tc", region_name="Test City", country="Testland",
                                 created_at=str(date.today()), items=items)
        story = sfi.StoryCandidate(theme="architecture", title="Arch story", summary="s",
                                   assets=[sfi.AssetRef(
                                       provider=i.provider, title=i.title, image_url=i.image_url,
                                       source_url=i.source_url, rights=i.rights,
                                       rights_statement=i.rights_statement, rights_url=i.rights_url,
                                       license_url=i.license_url, author=i.author,
                                       identifier=i.identifier, date_text=i.date_text,
                                       year_range=i.year_range, dimensions=i.dimensions,
                                       digest=i.digest, categories=i.categories, facets=i.facets,
                                       clip_score=i.clip_score, relevance_score=i.relevance_score,
                                       image_type="historic") for i in items],
                                   total_score=0.8)
        scenes = sfi.plan_scenes_from_story(story, inv)
        self.assertEqual(len(scenes), 9)
        for s in scenes:
            self.assertTrue(s.get("pinned_image"), f"scene {s.get('title')} has no pinned_image")
            self.assertTrue(s.get("asset_metadata"), f"scene {s.get('title')} has no asset_metadata")
            self.assertIn(s.get("image_type"), ("historic", "modern"))
            self.assertTrue(s.get("pinned_image").startswith("https://"))

    def test_no_text_card_padding_in_scene_plan(self):
        items = [ii.InventoryItem(
            provider="_loc", title="Only asset", description="d",
            image_url="https://ex.com/only.jpg", source_url="https://ex.com/only",
            rights="Public Domain", rights_statement="Public Domain", rights_url="",
            license_url="", author="A", identifier="1", date_text="1860",
            year_range=[1860, 1870], dimensions=[800, 600], digest="d-1",
            clip_score=0.5, relevance_score=0.8, eligible=True,
            discovered_via="q", discovered_at=str(date.today()))]
        inv = ii.RegionInventory(region_slug="tc", region_name="Test City", country="Testland",
                                 created_at=str(date.today()), items=items)
        story = sfi.StoryCandidate(theme="architecture", title="Arch", summary="s",
                                   assets=[sfi.AssetRef(
                                       provider=i.provider, title=i.title, image_url=i.image_url,
                                       source_url=i.source_url, rights=i.rights,
                                       rights_statement=i.rights_statement, rights_url=i.rights_url,
                                       license_url=i.license_url, author=i.author,
                                       identifier=i.identifier, date_text=i.date_text,
                                       year_range=i.year_range, dimensions=i.dimensions,
                                       digest=i.digest, categories=i.categories, facets=i.facets,
                                       clip_score=i.clip_score, relevance_score=i.relevance_score,
                                       image_type="historic") for i in items],
                                   total_score=0.8)
        scenes = sfi.plan_scenes_from_story(story, inv)
        self.assertEqual(len(scenes), 1)


if __name__ == "__main__":
    unittest.main()
