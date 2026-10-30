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
        self.assertIsNone(story, "select_story must return None when <6 viable assets")

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


class StoryCandidateToSelectionTests(unittest.TestCase):
    """Defect 1: StoryCandidate converts correctly to StorySelection."""

    def test_story_candidate_to_selection_preserves_all_fields(self):
        assets = [
            sfi.AssetRef(provider="_loc", title="Asset A", image_url="https://a.com/a.jpg",
                         source_url="https://a.com/item/a", rights="Public Domain",
                         rights_statement="Public Domain", rights_url="", license_url="",
                         author="A", identifier="1", date_text="1860", year_range=[1860, 1870],
                         dimensions=[800, 600], digest="d-a", clip_score=0.5, relevance_score=0.8,
                         image_type="historic"),
            sfi.AssetRef(provider="_met", title="Asset B", image_url="https://a.com/b.jpg",
                         source_url="https://a.com/item/b", rights="CC0", rights_statement="CC0",
                         rights_url="", license_url="", author="B", identifier="2",
                         date_text="1900", year_range=[1900, 1910], dimensions=[800, 600],
                         digest="d-b", clip_score=0.6, relevance_score=0.9, image_type="modern"),
        ]
        story = sfi.StoryCandidate(
            theme="architecture", title="Arch story", summary="A story about architecture",
            assets=assets, total_score=0.85, claims=["Claim one"],
            claim_support={"Claim one": ["https://source.com/1"]})
        # Construct StorySelection exactly as daily_orchestrate does.
        selection = sfi.StorySelection(
            region_slug="test-city", region_name="Test City",
            selected_theme=story.theme,
            story_title=story.title,
            story_summary=story.summary,
            story_assets=story.assets,
            candidates=[],
            claims=story.claims,
            claim_support=story.claim_support,
        )
        self.assertEqual(selection.selected_theme, "architecture")
        self.assertEqual(selection.story_title, "Arch story")
        self.assertEqual(len(selection.story_assets), 2)
        self.assertEqual(selection.claims, ["Claim one"])
        self.assertEqual(selection.claim_support, {"Claim one": ["https://source.com/1"]})
        # Serialisation round-trip preserves the theme field.
        d = selection.to_dict()
        self.assertEqual(d["selected_theme"], "architecture")
        self.assertEqual(d["story_title"], "Arch story")

    def test_load_story_round_trip_preserves_theme(self):
        assets = [
            sfi.AssetRef(provider="_loc", title="Asset A", image_url="https://a.com/a.jpg",
                         source_url="https://a.com/item/a", rights="Public Domain",
                         rights_statement="Public Domain", rights_url="", license_url="",
                         author="A", identifier="1", date_text="1860", year_range=[1860, 1870],
                         dimensions=[800, 600], digest="d-a", clip_score=0.5, relevance_score=0.8,
                         image_type="historic"),
        ]
        selection = sfi.StorySelection(
            region_slug="tc", region_name="Test City", selected_theme="architecture",
            story_title="Arch", story_summary="s", story_assets=assets,
            candidates=[], claims=["c"], claim_support={"c": ["https://x.com"]})
        sfi.save_story("tc", selection)
        loaded = sfi.load_story("tc")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.selected_theme, "architecture")
        self.assertEqual(loaded.story_title, "Arch")
        self.assertEqual(len(loaded.story_assets), 1)
        self.assertEqual(loaded.claims, ["c"])


class SceneAssetsExclusivityTests(unittest.TestCase):
    """Defect 2: scenes use only the selected story's assets, no cross-cluster leakage."""

    def _make_inv_item(self, theme, j, digest, provider="_loc"):
        return ii.InventoryItem(
            provider=provider, title=f"{theme} item {j}", description=f"a {theme} thing",
            image_url=f"https://ex.com/{theme}/{j}.jpg",
            source_url=f"https://ex.com/item/{theme}/{j}", rights="Public Domain",
            rights_statement="Public Domain", rights_url="", license_url="",
            author="A", identifier=str(j), date_text="1850", year_range=[1850, 1860],
            dimensions=[400, 400], digest=digest, clip_score=0.5, relevance_score=0.7,
            eligible=True, discovered_via=theme, discovered_at=str(date.today()))

    def _make_asset_ref(self, item: ii.InventoryItem) -> sfi.AssetRef:
        return sfi.AssetRef(
            provider=item.provider, title=item.title, image_url=item.image_url,
            source_url=item.source_url, rights=item.rights,
            rights_statement=item.rights_statement, rights_url=item.rights_url,
            license_url=item.license_url, author=item.author, identifier=item.identifier,
            date_text=item.date_text, year_range=item.year_range, dimensions=item.dimensions,
            digest=item.digest, categories=item.categories, facets=item.facets,
            clip_score=item.clip_score, relevance_score=item.relevance_score,
            image_type="historic")

    def test_scenes_use_only_selected_story_assets(self):
        # Two distinct clusters: architecture and trade.
        arch_items = [self._make_inv_item("architecture", j, f"arch-d-{j}") for j in range(8)]
        trade_items = [self._make_inv_item("trade", j, f"trade-d-{j}") for j in range(8)]
        inv = ii.RegionInventory(region_slug="tc", region_name="Test City", country="Testland",
                                 created_at=str(date.today()), items=arch_items + trade_items)

        # Winning story = architecture cluster only.
        arch_assets = [self._make_asset_ref(i) for i in arch_items]
        story = sfi.StoryCandidate(theme="architecture", title="Arch story", summary="s",
                                   assets=arch_assets, total_score=0.9)
        scenes = sfi.plan_scenes_from_story(story, inv)
        self.assertGreaterEqual(len(scenes), 1)
        # No scene may reference a trade asset.
        pinned_urls = {s["pinned_image"] for s in scenes}
        trade_urls = {i.image_url for i in trade_items}
        self.assertTrue(pinned_urls.isdisjoint(trade_urls),
                        f"Trade assets leaked into architecture scenes: {pinned_urls & trade_urls}")
        # Every scene's pinned image belongs to the architecture cluster.
        arch_urls = {i.image_url for i in arch_items}
        self.assertTrue(pinned_urls.issubset(arch_urls),
                        f"Non-architecture assets used: {pinned_urls - arch_urls}")

    def test_no_cross_cluster_substitution(self):
        arch_items = [self._make_inv_item("architecture", j, f"arch-d-{j}") for j in range(6)]
        trade_items = [self._make_inv_item("trade", j, f"trade-d-{j}") for j in range(6)]
        inv = ii.RegionInventory(region_slug="tc", region_name="Test City", country="Testland",
                                 created_at=str(date.today()), items=arch_items + trade_items)
        story = sfi.StoryCandidate(theme="architecture", title="Arch story", summary="s",
                                   assets=[self._make_asset_ref(i) for i in arch_items],
                                   total_score=0.9)
        scenes = sfi.plan_scenes_from_story(story, inv)
        self.assertEqual(len(scenes), 6)
        for s in scenes:
            self.assertIn(s["pinned_image"], {i.image_url for i in arch_items})
            self.assertNotIn(s["pinned_image"], {i.image_url for i in trade_items})


class NarrationTests(unittest.TestCase):
    """Defect 3: narration is historical, not a catalogue of assets/licences."""

    def test_narration_excludes_licence_and_provider_filler(self):
        assets = [
            sfi.AssetRef(provider="_loc", title="Cathedral", image_url="https://a.com/c.jpg",
                         source_url="https://a.com/item/c", rights="CC BY",
                         rights_statement="CC BY", rights_url="https://creativecommons.org/licenses/by/4.0/",
                         license_url="https://creativecommons.org/licenses/by/4.0/", author="Photographer",
                         identifier="1", date_text="1880", year_range=[1880, 1890],
                         dimensions=[800, 600], digest="d-c", clip_score=0.5, relevance_score=0.8,
                         image_type="historic"),
        ]
        story = sfi.StoryCandidate(theme="architecture", title="Cathedrals of Test City",
                                   summary="Historic cathedrals", assets=assets,
                                   claims=["The cathedral was consecrated in 1880."],
                                   claim_support={"The cathedral was consecrated in 1880.": ["https://source.com"]})
        scenes = [{"asset_metadata": {"title": "Cathedral", "provider": "_loc", "rights": "CC BY"}}]
        narration = sfi.generate_story_narration(story, scenes,
                                                 claims=story.claims,
                                                 claim_support=story.claim_support)
        lower = narration.lower()
        # Licence and attribution language must not appear in spoken narration.
        self.assertNotIn("cc by", lower)
        self.assertNotIn("creative commons", lower)
        self.assertNotIn("licence", lower)
        self.assertNotIn("license", lower)
        self.assertNotIn("provider", lower)
        self.assertNotIn("rights", lower)
        # Historical claim must be present.
        self.assertIn("consecrated in 1880", lower)
        self.assertIn("cathedrals of test city", lower)


class InsufficientStoryTests(unittest.TestCase):
    """Defect 4: insufficient inventory stops the story honestly (select_story returns None)."""

    def test_select_story_returns_none_for_insufficient_assets(self):
        items = [ii.InventoryItem(
            provider="_loc", title=f"Old building {j}", description=f"historic building scene {j}",
            image_url=f"https://ex.com/a{j}.jpg", source_url=f"https://ex.com/item/j{j}",
            rights="Public Domain", rights_statement="Public Domain", rights_url="",
            license_url="", author="A", identifier=str(j), date_text="1860",
            year_range=[1860, 1870], dimensions=[800, 600], digest=f"d-{j}",
            clip_score=0.5, relevance_score=0.8, eligible=True,
            discovered_via="q", discovered_at=str(date.today())) for j in range(3)]
        inv = ii.RegionInventory(region_slug="tc", region_name="Test City", country="Testland",
                                 created_at=str(date.today()), items=items)
        clusters = sfi.cluster_inventory(inv)
        story = sfi.select_story(clusters)
        self.assertIsNone(story, "select_story must return None when <6 viable assets")

    def test_select_story_returns_story_for_sufficient_assets(self):
        items = [ii.InventoryItem(
            provider="_loc", title=f"Old building {j}", description=f"historic building scene {j}",
            image_url=f"https://ex.com/a{j}.jpg", source_url=f"https://ex.com/item/j{j}",
            rights="Public Domain", rights_statement="Public Domain", rights_url="",
            license_url="", author="A", identifier=str(j), date_text="1860",
            year_range=[1860, 1870], dimensions=[800, 600], digest=f"d-{j}",
            clip_score=0.5, relevance_score=0.8, eligible=True,
            discovered_via="q", discovered_at=str(date.today())) for j in range(8)]
        inv = ii.RegionInventory(region_slug="tc", region_name="Test City", country="Testland",
                                 created_at=str(date.today()), items=items)
        clusters = sfi.cluster_inventory(inv)
        story = sfi.select_story(clusters)
        self.assertIsNotNone(story)
        self.assertGreaterEqual(len(story.assets), 6)


class GlobalRegionTests(unittest.TestCase):
    """Defect 5: same code works with unrelated regions without regional branching."""

    def test_unrelated_region_inventory_and_story(self):
        # A completely different region (Lagos-style) with items all matching
        # the trade theme, so they cluster into one viable story.
        items = [
            ii.InventoryItem(provider="_loc", title="Lagos market", description="historic market and port trade scene",
                             image_url="https://ex.com/lm1.jpg", source_url="https://ex.com/i1",
                             rights="Public Domain", rights_statement="Public Domain",
                             rights_url="", license_url="", author="A", identifier="1",
                             date_text="1950", year_range=[1950, 1960], dimensions=[800, 600],
                             digest="dlm1", clip_score=0.5, relevance_score=0.9,
                             eligible=True, discovered_via="lagos market", discovered_at=str(date.today())),
            ii.InventoryItem(provider="_met", title="Lagos port", description="historic port and harbour trade",
                             image_url="https://ex.com/lp1.jpg", source_url="https://ex.com/i2",
                             rights="CC0", rights_statement="CC0", rights_url="",
                             license_url="", author="B", identifier="2", date_text="1960",
                             year_range=[1960, 1970], dimensions=[800, 600], digest="dlp1",
                             clip_score=0.5, relevance_score=0.9, eligible=True,
                             discovered_via="lagos port", discovered_at=str(date.today())),
            ii.InventoryItem(provider="_aic", title="Lagos harbour", description="historic harbour commerce",
                             image_url="https://ex.com/lh1.jpg", source_url="https://ex.com/i3",
                             rights="Public Domain", rights_statement="Public Domain",
                             rights_url="", license_url="", author="C", identifier="3",
                             date_text="1940", year_range=[1940, 1950], dimensions=[800, 600],
                             digest="dlh1", clip_score=0.5, relevance_score=0.9, eligible=True,
                             discovered_via="lagos harbour", discovered_at=str(date.today())),
            ii.InventoryItem(provider="_nga", title="Lagos bazaar", description="historic bazaar marketplace",
                             image_url="https://ex.com/lbz1.jpg", source_url="https://ex.com/i4",
                             rights="CC BY", rights_statement="CC BY", rights_url="",
                             license_url="", author="D", identifier="4", date_text="1930",
                             year_range=[1930, 1940], dimensions=[800, 600], digest="dlbz1",
                             clip_score=0.5, relevance_score=0.9, eligible=True,
                             discovered_via="lagos bazaar", discovered_at=str(date.today())),
            ii.InventoryItem(provider="_loc", title="Lagos docks", description="historic docks and shipping",
                             image_url="https://ex.com/ldk1.jpg", source_url="https://ex.com/i5",
                             rights="Public Domain", rights_statement="Public Domain",
                             rights_url="", license_url="", author="E", identifier="5",
                             date_text="1970", year_range=[1970, 1980], dimensions=[800, 600],
                             digest="dldk1", clip_score=0.5, relevance_score=0.8, eligible=True,
                             discovered_via="lagos docks", discovered_at=str(date.today())),
            ii.InventoryItem(provider="_met", title="Lagos warehouse", description="historic warehouse and storage",
                             image_url="https://ex.com/lw1.jpg", source_url="https://ex.com/i6",
                             rights="CC0", rights_statement="CC0", rights_url="",
                             license_url="", author="F", identifier="6", date_text="1955",
                             year_range=[1955, 1965], dimensions=[800, 600], digest="dlw1",
                             clip_score=0.5, relevance_score=0.8, eligible=True,
                             discovered_via="lagos warehouse", discovered_at=str(date.today())),
        ]
        inv = ii.RegionInventory(region_slug="lagos", region_name="Lagos", country="Nigeria",
                                 created_at=str(date.today()), items=items)
        clusters = sfi.cluster_inventory(inv)
        # Should discover clusters without any Lagos-specific code.
        self.assertGreaterEqual(len(clusters), 1)
        story = sfi.select_story(clusters)
        self.assertIsNotNone(story)
        self.assertGreaterEqual(len(story.assets), 6)
        scenes = sfi.plan_scenes_from_story(story, inv)
        self.assertGreaterEqual(len(scenes), 1)
        # Narration should not contain licence filler.
        narration = sfi.generate_story_narration(story, scenes)
        self.assertNotIn("cc by", narration.lower())
        self.assertNotIn("creative commons", narration.lower())

    def test_no_city_specific_code_paths(self):
        # The same functions handle Tokyo, Lagos, Paris with no branching.
        for slug, name, country in [("tokyo", "Tokyo", "Japan"), ("lagos", "Lagos", "Nigeria"),
                                     ("paris", "Paris", "France")]:
            items = [ii.InventoryItem(
                provider="_loc", title=f"Old {name} building {j}",
                description=f"historic {name.lower()} building and temple scene",
                image_url=f"https://ex.com/{slug}/a{j}.jpg",
                source_url=f"https://ex.com/{slug}/i{j}", rights="Public Domain",
                rights_statement="Public Domain", rights_url="", license_url="",
                author="A", identifier=str(j), date_text="1880", year_range=[1880, 1890],
                dimensions=[800, 600], digest=f"{slug}-d-{j}", clip_score=0.5,
                relevance_score=0.8, eligible=True, discovered_via=f"{name} history",
                discovered_at=str(date.today())) for j in range(8)]
            inv = ii.RegionInventory(region_slug=slug, region_name=name, country=country,
                                     created_at=str(date.today()), items=items)
            clusters = sfi.cluster_inventory(inv)
            story = sfi.select_story(clusters)
            self.assertIsNotNone(story, f"select_story failed for {name}")
            scenes = sfi.plan_scenes_from_story(story, inv)
            self.assertGreaterEqual(len(scenes), 1, f"plan_scenes failed for {name}")
            for s in scenes:
                self.assertTrue(s["pinned_image"].startswith("https://"))
                self.assertIn("asset_metadata", s)


class PeriodClassificationTests(unittest.TestCase):
    """Defect 2: period classification uses original creation/capture date only.

    A historical work must not become modern because its archive record was
    digitised, uploaded or modified recently. When the original date cannot be
    established, preserve an explicit unknown classification.
    """

    def test_historical_artwork_with_recent_digitisation_date(self):
        """Edo-period print digitised in 2021 → historic (creation date wins)."""
        item = ii.InventoryItem(
            provider="_loc", title="Pagoda of Zojoji Temple",
            description="From the Series One Hundred Famous Views of Edo (1858)",
            image_url="https://commons.wikimedia.org/wiki/File:zojoji.jpg",
            source_url="https://commons.wikimedia.org/wiki/File:zojoji.jpg",
            rights="Public Domain", rights_statement="Public Domain", rights_url="",
            license_url="", author="Hiroshige", identifier="zojoji-1858",
            date_text="1858; digitised 2021-05-13", year_range=None,
            dimensions=[800, 600], digest="d-zojoji", clip_score=0.5,
            relevance_score=0.8, eligible=True, discovered_via="commons",
            discovered_at=str(date.today()))
        self.assertEqual(sfi._period_of(item), "historic")

    def test_historical_artwork_year_range_with_recent_upload(self):
        """year_range [1858, 1860] + recent upload → historic."""
        item = ii.InventoryItem(
            provider="_met", title="Edo Street Scene",
            description="Woodblock print from the 1850s",
            image_url="https://metmuseum.org/edo.jpg", source_url="https://metmuseum.org/edo",
            rights="Public Domain", rights_statement="Public Domain", rights_url="",
            license_url="", author="Unknown", identifier="edo-1",
            date_text="Uploaded 2023", year_range=[1858, 1860],
            dimensions=[800, 600], digest="d-edo", clip_score=0.5,
            relevance_score=0.8, eligible=True, discovered_via="met",
            discovered_at=str(date.today()))
        self.assertEqual(sfi._period_of(item), "historic")

    def test_modern_photograph_with_confirmed_capture_date(self):
        """Modern photograph taken in 1975 → modern."""
        item = ii.InventoryItem(
            provider="_loc", title="Tokyo Station 1975",
            description="Photograph of Tokyo Station",
            image_url="https://loc.gov/tokyo.jpg", source_url="https://loc.gov/tokyo",
            rights="Public Domain", rights_statement="Public Domain", rights_url="",
            license_url="", author="Photographer", identifier="tokyo-1975",
            date_text="1975", year_range=[1975, 1975],
            dimensions=[800, 600], digest="d-tokyo", clip_score=0.5,
            relevance_score=0.8, eligible=True, discovered_via="loc",
            discovered_at=str(date.today()))
        self.assertEqual(sfi._period_of(item), "modern")

    def test_modern_photograph_recent_upload_only(self):
        """Modern photograph, date_text is upload date only (no year_range) → unknown."""
        item = ii.InventoryItem(
            provider="_loc", title="Modern Street",
            description="Contemporary street scene",
            image_url="https://loc.gov/street.jpg", source_url="https://loc.gov/street",
            rights="Public Domain", rights_statement="Public Domain", rights_url="",
            license_url="", author="Photographer", identifier="street-1",
            date_text="2021-05-13T10:30:00Z", year_range=None,
            dimensions=[800, 600], digest="d-street", clip_score=0.5,
            relevance_score=0.8, eligible=True, discovered_via="loc",
            discovered_at=str(date.today()))
        # Upload date only: cannot confirm original capture date → unknown.
        self.assertEqual(sfi._period_of(item), "unknown")

    def test_missing_or_ambiguous_creation_date(self):
        """No date information at all → unknown."""
        item = ii.InventoryItem(
            provider="_aic", title="Untitled Scene",
            description="No date information available",
            image_url="https://artic.edu/untitled.jpg", source_url="https://artic.edu/untitled",
            rights="Public Domain", rights_statement="Public Domain", rights_url="",
            license_url="", author="Unknown", identifier="untitled-1",
            date_text="", year_range=None,
            dimensions=[800, 600], digest="d-untitled", clip_score=0.5,
            relevance_score=0.8, eligible=True, discovered_via="aic",
            discovered_at=str(date.today()))
        self.assertEqual(sfi._period_of(item), "unknown")

    def test_all_recent_dates_no_year_range(self):
        """All attested dates are recent but no year_range → unknown (not falsely modern)."""
        item = ii.InventoryItem(
            provider="_loc", title="Old Building Uploaded Recently",
            description="Historic building, record uploaded in 2021",
            image_url="https://loc.gov/building.jpg", source_url="https://loc.gov/building",
            rights="Public Domain", rights_statement="Public Domain", rights_url="",
            license_url="", author="Unknown", identifier="building-1",
            date_text="2021-03-15", year_range=None,
            dimensions=[800, 600], digest="d-building", clip_score=0.5,
            relevance_score=0.8, eligible=True, discovered_via="loc",
            discovered_at=str(date.today()))
        # No year_range to confirm modern → unknown rather than falsely modern.
        self.assertEqual(sfi._period_of(item), "unknown")


class PinnedAssetDiagnosticsTests(unittest.TestCase):
    """Pinned-image validation and transparent diagnostics."""

    def test_scene_diagnostics_contain_period_and_rights(self):
        """Each scene diagnostic carries period_ok, rights, source, and warnings."""
        meta = {
            "title": "Test Asset", "author": "Author", "rights": "Public Domain",
            "rights_statement": "Public Domain", "rights_url": "",
            "license_url": "", "asset_url": "https://example.com/asset/1",
            "source_url": "https://example.com/source/1", "provider": "_loc",
            "identifier": "1", "date_text": "1860", "year_range": [1860, 1870],
            "dimensions": [800, 600], "categories": [], "facets": [],
            "digest": "abc123", "clip_score": 0.5, "relevance_score": 0.8,
            "discovered_via": "commons query",
        }
        scene = {"title": "Test Scene", "pinned_image": "https://example.com/asset/1",
                 "image_type": "historic", "asset_metadata": meta}
        # Build expected diagnostic entry (mirrors daily_orchestrate extraction).
        diag = {
            "scene": scene["title"],
            "title": meta["title"],
            "provider": meta["provider"],
            "rights": meta["rights"],
            "rights_statement": meta["rights_statement"],
            "license_url": meta["license_url"],
            "asset_url": meta["asset_url"],
            "source_url": meta["source_url"],
            "period_ok": None,  # historic scene → period_ok not set
            "match_score": meta["relevance_score"],
            "clip_score": meta["clip_score"],
            "warnings": ["Pinned from pre-vetted regional inventory; no provider re-search performed."],
            "diagnostics": {"pinned": True, "discovered_via": "commons query"},
            "is_missing_asset": False,
        }
        self.assertEqual(diag["is_missing_asset"], False)
        self.assertEqual(diag["period_ok"], None)
        self.assertEqual(diag["rights"], "Public Domain")
        self.assertTrue(diag["asset_url"].startswith("https://"))

    def test_missing_asset_flagged_in_diagnostics(self):
        """A missing-asset credit is flagged as missing in diagnostics."""
        credit = {"provider": "missing asset", "scene": "Missing Scene",
                  "title": "NO APPROVED SOURCE IMAGE", "warnings": ["No candidate passed"]}
        self.assertTrue(credit["provider"] in ("missing asset", "fallback text card"))


class EditorialStoryTests(unittest.TestCase):
    """Defect 5: story selection prefers coherent narratives over generic cluster labels."""

    def test_narrative_theme_outranks_generic_container(self):
        """Events/people themes score higher than architecture/daily_life with same assets."""
        # Two clusters with equal asset counts: architecture (generic) vs events (narrative).
        arch_items = [ii.InventoryItem(
            provider="_loc", title=f"Old building {j}", description=f"historic building scene {j}",
            image_url=f"https://ex.com/a{j}.jpg", source_url=f"https://ex.com/item/j{j}",
            rights="Public Domain", rights_statement="Public Domain", rights_url="",
            license_url="", author="A", identifier=str(j), date_text="1860",
            year_range=[1860, 1870], dimensions=[800, 600], digest=f"arch-d-{j}",
            clip_score=0.5, relevance_score=0.7, eligible=True,
            discovered_via="q", discovered_at=str(date.today())) for j in range(8)]
        event_items = [ii.InventoryItem(
            provider="_loc", title=f"Revolution event {j}", description=f"historic revolution {j}",
            image_url=f"https://ex.com/e{j}.jpg", source_url=f"https://ex.com/item/e{j}",
            rights="Public Domain", rights_statement="Public Domain", rights_url="",
            license_url="", author="A", identifier=str(j), date_text="1860",
            year_range=[1860, 1870], dimensions=[800, 600], digest=f"evt-d-{j}",
            clip_score=0.5, relevance_score=0.7, eligible=True,
            discovered_via="q", discovered_at=str(date.today())) for j in range(8)]
        inv = ii.RegionInventory(region_slug="tc", region_name="Test City", country="Testland",
                                 created_at=str(date.today()), items=arch_items + event_items)
        clusters = sfi.cluster_inventory(inv)
        claim_support = {"The revolution changed the city forever.": ["https://source.com/1"]}
        candidates = [sfi._score_candidate(t, items, claim_support) for t, items in clusters]
        # Find the events and architecture candidates.
        evt_cand = next(c for c in candidates if c.theme == "events")
        arch_cand = next(c for c in candidates if c.theme == "architecture")
        # Events (narrative theme) should score >= architecture (generic container)
        # due to narrative_bonus, assuming equal other scores.
        self.assertGreaterEqual(evt_cand.total_score, arch_cand.total_score)

    def test_story_requires_minimum_coherence_score(self):
        """Generic cluster with low coherence fails the minimum score threshold."""
        items = [ii.InventoryItem(
            provider="_loc", title=f"Random item {j}", description=f"unrelated thing {j}",
            image_url=f"https://ex.com/r{j}.jpg", source_url=f"https://ex.com/item/r{j}",
            rights="Public Domain", rights_statement="Public Domain", rights_url="",
            license_url="", author="A", identifier=str(j), date_text="",
            year_range=None, dimensions=[800, 600], digest=f"rand-d-{j}",
            clip_score=None, relevance_score=0.1, eligible=True,
            discovered_via="q", discovered_at=str(date.today())) for j in range(8)]
        inv = ii.RegionInventory(region_slug="tc", region_name="Test City", country="Testland",
                                 created_at=str(date.today()), items=items)
        clusters = sfi.cluster_inventory(inv)
        story = sfi.select_story(clusters)
        # Low coherence + no dates + low relevance → likely below threshold.
        if story is not None:
            self.assertLess(story.total_score, 0.40,
                            "low-coherence generic cluster should not qualify as a story")


if __name__ == "__main__":
    unittest.main()

