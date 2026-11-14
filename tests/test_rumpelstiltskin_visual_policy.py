import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DESTINATION_PATH = str(ROOT / "destination-prototype")
sys.path.insert(0, DESTINATION_PATH)

import asset_resolver as ar
import rumpelstiltskin_asset_review as asset_review
import rumpelstiltskin_visual_policy as policy

# Do not leave the prototype directory ahead of the repository root: these
# tests also run with the root scene_planner module in the same unittest pass.
while DESTINATION_PATH in sys.path:
    sys.path.remove(DESTINATION_PATH)


def candidate(title, description="", categories=None, clip_score=0.99, role=None,
              review_status="candidate_passed_automatic_gates"):
    return {
        "title": title,
        "description": description,
        "visual_metadata": {"categories": categories or [], "facets": []},
        "clip_score": clip_score,
        "p_fairytale_illustration": 0.9,
        "approved": False,
        "review_status": review_status,
        "decoded_pixels_sha256": "same-pixels",
        "source_url": "https://example.org/item",
    }


class StoryIdentityTests(unittest.TestCase):
    def test_edition_category_is_exact_story_provenance(self):
        item = candidate(
            "Page illustration",
            categories=["Grimm's Goblins/Rumpel-Stilts-Kin"],
        )
        self.assertEqual(policy.classify_candidate(item, "beat-03-first-bargains"),
                         "exact_story_illustration")

    def test_alternative_historical_spellings_identify_the_story(self):
        for title in ("Rumpelstilzchen woodcut", "Rumpel-Stilts-Kin engraving",
                      "Rumpelstiltzkin illustration"):
            with self.subTest(title=title):
                self.assertEqual(policy.classify_candidate(candidate(title), "beat-01-threat"),
                                 "exact_story_illustration")

    def test_unrelated_royal_religious_portrait_and_bird_art_are_not_exact(self):
        unrelated = [
            "Portrait of King Lear",
            "Queen Victoria with her children",
            "Madonna and Child",
            "The Virgin Mary with the Christ Child",
            "An eagle in flight",
            "A peacock in a garden",
        ]
        for title in unrelated:
            with self.subTest(title=title):
                self.assertEqual(policy.classify_candidate(candidate(title), "beat-06-claim"),
                                 "unrelated_contextual_art")

    def test_high_clip_score_alone_never_proves_story_identity(self):
        item = candidate("A steam locomotive at a railway station", clip_score=0.999)
        self.assertEqual(policy.classify_candidate(item, "beat-04-king-demands"),
                         "unrelated_contextual_art")

    def test_historical_photographs_do_not_count_as_storybook_symbolic_art(self):
        photo = candidate(
            "Albert Kunuiakea with Kamehameha III and Queen Kalama, about 1853",
            description="Prince Albert as an infant; published in The Pacific Commercial Advertiser",
            categories=["Queen Kalama", "The Pacific Commercial Advertiser"],
        )
        self.assertEqual(policy.classify_candidate(photo, "beat-06-claim"),
                         "unrelated_contextual_art")

    def test_naeke_spinning_wheel_image_is_not_the_fathers_boast_beat(self):
        item = candidate("Rumpelstiltskin Appearing to the Miller's Daughter")
        status, _ = policy.exact_scene_match_status(item, "beat-02-boast")
        self.assertEqual(status, "story_identity_only_unplaced")
        correct = candidate("Rumpelstiltskin at the Spinning-Wheel and the Dwarf")
        status, missing = policy.exact_scene_match_status(correct, "beat-03-first-bargains")
        self.assertEqual(status, "metadata_scene_match")
        self.assertEqual(missing, [])

    def test_symbolic_asset_can_use_one_relevant_atmospheric_anchor(self):
        object_led = candidate("Antique straw bundle engraving")
        mood_led = candidate("A candlelit room")
        unrelated = candidate("A steam locomotive at a railway station")
        self.assertEqual(policy.classify_candidate(object_led, "beat-01-threat"),
                         "symbolic_contextual_asset")
        self.assertEqual(policy.classify_candidate(mood_led, "beat-01-threat"),
                         "symbolic_contextual_asset")
        self.assertEqual(policy.classify_candidate(unrelated, "beat-01-threat"),
                         "unrelated_contextual_art")

    def test_symbolic_assets_fail_closed_without_illustration_style_evidence(self):
        item = candidate("Antique straw bundle engraving")
        item.pop("p_fairytale_illustration")
        self.assertEqual(policy.classify_candidate(item, "beat-01-threat"),
                         "unrelated_contextual_art")

    def test_weak_clip_match_cannot_turn_a_metadata_word_into_a_visual_match(self):
        item = candidate(
            "File:Brother of Chekan with his wife Maria Miller",
            clip_score=0.252,
        )
        self.assertEqual(policy.classify_candidate(item, "beat-02-boast"),
                         "unrelated_contextual_art")

    def test_museum_sculptures_and_archaeological_objects_are_not_storybook_art(self):
        item = candidate("Infant Bacchus with Grapes", clip_score=0.32)
        item["visual_metadata"]["facets"] = [
            "Sculpture", "Statuette", "Terracotta; marble", "Bacchus",
        ]
        self.assertEqual(policy.classify_candidate(item, "beat-06-claim"),
                         "unrelated_contextual_art")

    def test_incidental_description_words_do_not_validate_unrelated_objects(self):
        item = candidate(
            "Brick stamped with cartouches of Asetemkheb",
            description="Asetemkheb was the daughter of king Psusennes I.",
            clip_score=0.32,
        )
        item["visual_metadata"]["facets"] = ["Ancient Egyptian object", "Brick"]
        self.assertEqual(policy.classify_candidate(item, "beat-04-king-demands"),
                         "unrelated_contextual_art")

    def test_curated_story_sources_resolve_to_their_actual_beat(self):
        met = candidate("Rumpelstiltskin Appearing to the Miller's Daughter")
        met["visual_metadata"]["identifier"] = "394497"
        blue_fairy = candidate("File:The Blue Fairy Book, p. 97.jpg")
        self.assertEqual(policy.curated_beat_for_candidate(met), "beat-03-first-bargains")
        self.assertEqual(policy.curated_beat_for_candidate(blue_fairy), "beat-03-first-bargains")

    def test_story_identity_takes_precedence_over_symbolic_terms(self):
        item = candidate("Rumpelstiltskin and the spinning wheel with straw")
        self.assertEqual(policy.classify_candidate(item, "beat-03-first-bargains"),
                         "exact_story_illustration")

    def test_exact_search_is_hard_identity_and_category_constrained(self):
        scene = policy.build_review_scene(
            {"id": "beat-03-first-bargains", "title": "First bargains",
             "asset_requirement": {"must_show": "little man and spinning wheel"}},
            3, "exact",
        )
        self.assertEqual(scene["named_entities"], ["Rumpelstiltskin"])
        self.assertFalse(scene["identity_fallback_to_clip"])
        self.assertTrue(scene["commons_categories"])
        self.assertEqual(scene["identity_aliases"], list(policy.STORY_ALIASES[1:]))
        self.assertFalse(scene["required_keywords"])

    def test_exact_story_searches_reach_providers_before_generic_scene_tags(self):
        scene = policy.build_review_scene(
            {"id": "beat-03-first-bargains", "title": "First bargains",
             "asset_requirement": {"must_show": "little man and spinning wheel",
                                   "search_terms": ["Rumpelstiltskin spinning wheel dwarf scene"]}},
            3, "exact",
        )
        queries = ar.build_queries(scene, {}, scene["max_queries"])
        self.assertEqual(queries[:4], policy.EXACT_SEARCH_TERMS[:4])
        self.assertTrue(scene["search_terms_first"])
        self.assertEqual(scene["max_queries_per_provider"], 4)

    def test_symbolic_search_uses_scene_groups_without_claiming_story_identity(self):
        scene = policy.build_review_scene(
            {"id": "beat-01-threat", "title": "Threat",
             "asset_requirement": {"must_show": "straw and spinning wheel"}},
            1, "symbolic",
        )
        self.assertNotIn("named_entities", scene)
        self.assertEqual(scene["required_keyword_groups"],
                         policy.SYMBOLIC_REQUIREMENTS["beat-01-threat"])
        self.assertTrue(scene["banned_keywords"])

    def test_symbolic_provider_queries_keep_authored_scene_prompts_first(self):
        scene = policy.build_review_scene(
            {"id": "beat-01-threat", "title": "Threat",
             "asset_requirement": {"must_show": "straw and spinning wheel"}},
            1, "symbolic",
        )
        queries = ar.build_queries(scene, {}, scene["max_queries"])
        authored = policy.SYMBOLIC_SEARCH_TERMS["beat-01-threat"]
        self.assertEqual(queries[:len(authored)], authored)
        self.assertEqual(scene["max_queries_per_provider"], 4)
        self.assertTrue(scene["search_terms_first"])


    def test_story_identity_without_beat_metadata_is_not_beat_coverage(self):
        source_only = candidate(
            "The Blue Fairy Book, page 97",
            categories=["Blue Fairy Book/Rumpelstiltzkin"],
        )
        self.assertEqual(policy.classify_candidate(source_only, "beat-03-first-bargains"),
                         "exact_story_illustration")
        status, missing = policy.exact_scene_match_status(source_only, "beat-03-first-bargains")
        self.assertEqual(status, "story_identity_only_unplaced")
        self.assertTrue(missing)

    def test_scene_specific_story_title_can_be_assigned_to_correct_beat(self):
        spinning_wheel = candidate(
            "Rumpelstiltskin at the Spinning-Wheel and the Dwarf MET DP827694",
        )
        status, missing = policy.exact_scene_match_status(spinning_wheel, "beat-03-first-bargains")
        self.assertEqual(status, "curated_story_beat_match")
        self.assertEqual(missing, [])
        threat_status, _ = policy.exact_scene_match_status(spinning_wheel, "beat-01-threat")
        self.assertEqual(threat_status, "story_identity_only_unplaced")

    def test_curated_story_illustrations_are_mapped_to_their_actual_story_beats(self):
        met = candidate("Rumpelstiltskin Appearing to the Miller's Daughter")
        met["identifier"] = "394497"
        self.assertEqual(
            policy.exact_scene_match_status(met, "beat-03-first-bargains")[0],
            "curated_story_beat_match",
        )
        blue_fairy = candidate("File:The Blue Fairy Book, p. 97.jpg")
        self.assertEqual(
            policy.exact_scene_match_status(blue_fairy, "beat-03-first-bargains")[0],
            "curated_story_beat_match",
        )
        final_scene = candidate("File:GrimmsGoblins-140-Rumpelstiltskin.jpg")
        self.assertEqual(
            policy.exact_scene_match_status(final_scene, "beat-09-foot-revelation")[0],
            "curated_story_beat_match",
        )

        blue_fairy_entrance = candidate(
            "File:Andrew Lang - The Blue Fairy Book p.116.png",
            categories=["Blue Fairy Book/Rumpelstiltzkin"],
        )
        self.assertEqual(
            policy.exact_scene_match_status(blue_fairy_entrance, "beat-03-first-bargains")[0],
            "curated_story_beat_match",
        )
        self.assertEqual(
            policy.curated_beat_for_candidate(blue_fairy_entrance),
            "beat-03-first-bargains",
        )

    def test_photographic_miller_name_match_is_rejected_by_illustration_style_gate(self):
        photo = candidate(
            "A Miller daughter in a historical family portrait",
            description="A named couple photographed in Paris",
        )
        photo["p_fairytale_illustration"] = 0.08
        self.assertEqual(policy.classify_candidate(photo, "beat-02-boast"),
                         "unrelated_contextual_art")
        illustration = candidate("An antique illustrated miller and daughter")
        illustration["p_fairytale_illustration"] = 0.92
        self.assertEqual(policy.classify_candidate(illustration, "beat-02-boast"),
                         "symbolic_contextual_asset")

    def test_fairytale_style_gate_requires_illustration_probability(self):
        self.assertTrue(ar._fairytale_style_ok(0.81))
        self.assertFalse(ar._fairytale_style_ok(0.19))
        self.assertFalse(ar._fairytale_style_ok(None))

class CoverageTests(unittest.TestCase):
    def test_clip_ranked_story_image_is_counted_as_a_review_candidate_not_auto_approved(self):
        image = {
            **candidate("Rumpelstiltskin Appearing to the Miller's Daughter"),
            "visual_role": "exact_story_illustration",
            "scene_match_status": "clip_ranked_human_review",
        }
        beat = {"scene_id": "beat-03-first-bargains", "candidates": [image]}
        result = policy.summarize_visual_coverage([beat])
        self.assertEqual(result["beats_with_story_illustration_candidates_for_human_placement"], 1)
        self.assertEqual(result["story_illustration_candidate_beat_ids"], ["beat-03-first-bargains"])
        self.assertEqual(result["beats_with_any_automatic_candidates"], 1)
        self.assertFalse(image.get("approved", False))

    def test_coverage_never_counts_context_as_exact_or_duplicate_as_usable(self):
        beats = [
            {"scene_id": "beat-01", "candidates": [
                candidate("Rumpelstiltskin illustration", categories=["Rumpelstilzchen"]),
            ]},
            {"scene_id": "beat-02", "candidates": [
                {**candidate("Antique straw spinning wheel"), "visual_role": "symbolic_contextual_asset"},
            ]},
            {"scene_id": "beat-03", "candidates": [
                {**candidate("Portrait of King Lear"), "visual_role": "unrelated_contextual_art"},
            ]},
            {"scene_id": "beat-04", "candidates": [
                {**candidate("The Blue Fairy Book page 97", categories=["Rumpelstiltzkin"]),
                 "visual_role": "exact_story_illustration", "scene_match_status": "story_identity_only_unplaced"},
            ]},
            {"scene_id": "beat-05", "candidates": [
                {**candidate("Duplicate Rumpelstiltskin image", categories=["Rumpelstiltskin"]),
                 "visual_role": "exact_story_illustration", "scene_match_status": "metadata_scene_match",
                 "review_status": "rejected_duplicate"},
            ]},
        ]
        # The first candidate is explicitly classified as exact to model the
        # review script's accepted candidate record.
        beats[0]["candidates"][0]["visual_role"] = "exact_story_illustration"
        beats[0]["candidates"][0]["scene_match_status"] = "metadata_scene_match"
        result = policy.summarize_visual_coverage(beats)
        self.assertEqual(result["beats_with_exact_story_illustrations"], 1)
        self.assertEqual(result["beats_with_suitable_symbolic_assets"], 1)
        self.assertEqual(result["beats_with_any_automatic_candidates"], 2)
        self.assertEqual(result["beats_lacking_usable_visual"], 3)
        self.assertEqual(result["story_identified_illustrations_without_verified_beat_placement"], 1)
        self.assertEqual(result["unplaced_story_illustration_titles"], ["The Blue Fairy Book page 97"])
        self.assertNotIn("beat-03", result["exact_story_illustration_beat_ids"])
        self.assertNotIn("beat-04", result["exact_story_illustration_beat_ids"])
        self.assertNotIn("beat-05", result["exact_story_illustration_beat_ids"])


class ExistingGuardrailTests(unittest.TestCase):
    def test_commons_seed_fetch_keeps_edition_category_provenance(self):
        payload = {"query": {"pages": {"1": {
            "pageid": 1,
            "title": "File:The Blue Fairy Book, p. 97.jpg",
            "imageinfo": [{
                "url": "https://upload.wikimedia.org/example/p97.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:The_Blue_Fairy_Book,_p._97.jpg",
                "width": 1602,
                "height": 2151,
                "extmetadata": {
                    "ImageDescription": {"value": "Illustration from The Blue Fairy Book"},
                    "LicenseShortName": {"value": "Public domain"},
                    "LicenseUrl": {"value": "https://creativecommons.org/publicdomain/mark/1.0/"},
                    "Artist": {"value": "Henry Justice Ford"},
                },
            }],
            "categories": [{"title": "Category:Blue Fairy Book/Rumpelstiltzkin"}],
        }}}}
        with patch.object(ar, "_get", return_value=payload) as mocked:
            record = ar._commons_file_candidate("The Blue Fairy Book, p. 97.jpg")
        self.assertIsNotNone(record)
        self.assertIn("Blue Fairy Book/Rumpelstiltzkin", record["categories"])
        self.assertEqual(record["width"], 1602)
        params = mocked.call_args.args[1]
        self.assertIn("categories", params["prop"])
        self.assertEqual(params["cllimit"], 30)

    def test_rights_and_source_resolution_gates_remain_strict(self):
        self.assertTrue(ar._rights_ok("Public Domain"))
        self.assertTrue(ar._rights_ok("CC0 1.0"))
        self.assertFalse(ar._rights_ok("CC BY-NC 4.0"))
        self.assertFalse(ar._rights_ok("CC BY-ND 4.0"))
        self.assertFalse(ar._rights_ok("unknown rights"))
        config = {"min_short_side": 1080, "min_long_side": 1920, "max_upscale": 1.25}
        self.assertFalse(ar._size_ok(Image.new("RGB", (900, 1400)), config))
        self.assertTrue(ar._size_ok(Image.new("RGB", (1400, 2000)), config))

    def test_ocr_size_rights_and_duplicate_gates_still_exist_in_core_resolver(self):
        source = (ROOT / "destination-prototype" / "asset_resolver.py").read_text(encoding="utf-8")
        self.assertIn("if not _rights_ok(c[\"rights_statement\"])", source)
        self.assertIn("if not _size_ok(im, rcfg)", source)
        self.assertIn("if use_ocr and _text_heavy(im)", source)
        self.assertIn("if digest in used or c[\"source_url\"] in used", source)

    def test_review_run_still_deduplicates_candidate_pixel_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            plan_path = tmp / "plan.json"
            plan = {
                "series": "test",
                "title": "test episode",
                "version_scope": "test source",
                "scenes": [{
                    "id": "beat-03-first-bargains",
                    "title": "First bargains",
                    "evidence": "test",
                    "asset_requirement": {"must_show": "spinning wheel"},
                }],
            }
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            output = tmp / "review"
            file_path = output / "candidate-files" / "beat-03-exact" / "candidate.jpg"
            first = candidate("Rumpelstiltskin at the spinning wheel with the dwarf",
                              categories=["Rumpelstilzchen (1812, Grimm)"])
            first.update({
                "candidate_id": "first", "file": str(file_path),
                "scene_id": "beat-03-first-bargains-exact",
                "dimensions": [1602, 2151], "decoded_pixels_sha256": "pixel-duplicate",
                "visual_metadata": {"categories": ["Rumpelstilzchen (1812, Grimm)"], "facets": []},
            })
            second = {**first, "candidate_id": "second", "source_url": "https://example.org/second"}
            fake_result = {
                "review_candidates": [first, second],
                "diagnostics": {
                    "provider_source_urls": [], "rights_pass_source_urls": [],
                    "size_pass_source_urls": [], "clip_pass_source_urls": [],
                },
            }
            with patch.object(asset_review, "SEEDS", []), \
                 patch.object(asset_review.ar, "resolve_scene_image",
                              return_value=(Image.new("RGB", (100, 100)), fake_result)) as resolver_mock, \
                 patch.object(asset_review, "create_contact_sheet"):
                manifest = asset_review.run(plan_path, output)
            self.assertEqual(resolver_mock.call_count, 2, "exact and symbolic searches must both run for a beat")
            self.assertEqual(manifest["summary"]["unique_candidate_visual_files_for_human_review"], 1)
            self.assertEqual(manifest["summary"]["beats_with_exact_story_illustrations"], 1)
            self.assertEqual(manifest["summary"]["unrelated_or_uncertain_search_results_rejected"], 1)


if __name__ == "__main__":
    unittest.main()
