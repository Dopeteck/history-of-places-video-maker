"""Offline tests for scene-plan validation, image quality gates and cache fallback."""
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from scene_assets import ImageProviderChain, image_sha1, procedural_illustration, validate_image
from scene_planner import (make_review_template, plan_scene_cards, validate_scene_plan,
                           validate_scene_plan_review)


def valid_plan():
    return {"title": "A careful explainer", "hook": "What the evidence says",
            "scenes": [{"id": f"scene_{i:02d}", "kind": "illustration",
                        "title": f"Scene {i}", "narration": "The source describes this event.",
                        "visual_prompt": "Editorial illustration, no lettering or text.",
                        "background": "ivory", "source_ids": ["source_1"],
                        "labels": [], "claims": [], "transition": "crossfade"}
                       for i in range(1, 7)]}


class ScenePlannerTests(unittest.TestCase):
    def test_valid_plan_is_normalized(self):
        result = validate_scene_plan(valid_plan(), {"source_1"})
        self.assertEqual(len(result["scenes"]), 6)
        self.assertEqual(result["status"], "needs_human_review")

    def test_duplicate_scene_ids_rejected(self):
        data = valid_plan()
        data["scenes"][1]["id"] = data["scenes"][0]["id"]
        with self.assertRaises(ValueError):
            validate_scene_plan(data, {"source_1"})

    def test_unknown_source_rejected(self):
        data = valid_plan()
        data["scenes"][0]["source_ids"] = ["missing"]
        with self.assertRaises(ValueError):
            validate_scene_plan(data, {"source_1"})

    def test_missing_source_rejected(self):
        data = valid_plan()
        data["scenes"][0]["source_ids"] = []
        with self.assertRaises(ValueError):
            validate_scene_plan(data, {"source_1"})

    def test_planner_runs_existing_json_validator_and_factcheck(self):
        class FakeLLM:
            def generate(self, system, prompt, validate):
                data = {
                    "title": "Evidence-led explainer",
                    "hook": "Here is what the record shows",
                    "scenes": [{
                        "id": f"scene_{i:02d}", "kind": "illustration",
                        "title": f"Scene {i}", "narration": "The source describes this event.",
                        "visual_prompt": "Editorial illustration with no text.",
                        "background": "ivory", "source_ids": ["source_1"],
                        "labels": [], "claims": [{
                            "claim": f"Checkable claim number {i}.",
                            "source_ids": ["source_1"], "confidence": 0.8,
                            "what_to_verify": "Confirm this statement in the original source."
                        }]
                    } for i in range(1, 7)]
                }
                return validate(data)

        plan = plan_scene_cards(
            FakeLLM(), "A sourced topic",
            [{"id": "source_1", "title": "Source", "url": "https://example.org/source",
              "excerpt": "A relevant source excerpt."}], min_scenes=6, max_scenes=6)
        self.assertEqual(len(plan["review"]["claims"]), 6)
        self.assertTrue(all(c["status"] == "needs_human_review" for c in plan["review"]["claims"]))
        review = make_review_template(plan)
        review["reviewer"] = "Human reviewer"
        review["full_script_reviewed"] = True
        review["sources_checked"] = ["source_1"]
        for row in review["claims"]:
            row["decision"] = "verified"
            row["source_ids"] = ["source_1"]
            row["note"] = "Compared with the original source."
        validate_scene_plan_review(plan, review)

    def test_review_rejects_duplicate_decisions(self):
        class FakeLLM:
            def generate(self, system, prompt, validate):
                return validate({
                    "title": "Evidence-led explainer", "hook": "What we know",
                    "scenes": [{
                        "id": f"scene_{i:02d}", "kind": "illustration",
                        "title": f"Scene {i}", "narration": "The source describes this event.",
                        "visual_prompt": "Editorial illustration with no text.",
                        "source_ids": ["source_1"], "claims": [{
                            "claim": f"Claim {i}", "source_ids": ["source_1"],
                            "confidence": 0.8, "what_to_verify": "Check the source."
                        }]
                    } for i in range(1, 7)]
                })
        plan = plan_scene_cards(
            FakeLLM(), "A sourced topic",
            [{"id": "source_1", "title": "Source", "url": "https://example.org/source",
              "excerpt": "A relevant source excerpt."}], min_scenes=6, max_scenes=6)
        review = make_review_template(plan)
        review.update({"reviewer": "Reviewer", "full_script_reviewed": True,
                       "sources_checked": ["source_1"]})
        for row in review["claims"]:
            row.update({"decision": "verified", "source_ids": ["source_1"], "note": "Checked."})
        review["claims"][-1] = dict(review["claims"][0])
        with self.assertRaises(ValueError):
            validate_scene_plan_review(plan, review)


class ImageAssetsTests(unittest.TestCase):
    def test_sha1_is_stable_and_prompt_sensitive(self):
        a = image_sha1("castle", "fal", "model", (1280, 720))
        self.assertEqual(a, image_sha1("castle", "fal", "model", (1280, 720)))
        self.assertNotEqual(a, image_sha1("river", "fal", "model", (1280, 720)))

    def test_quality_gate_rejects_blank_image(self):
        with self.assertRaises(ValueError):
            validate_image(Image.new("RGB", (1280, 720), "white"))

    def test_procedural_image_passes_gate(self):
        image = procedural_illustration("city buildings and people", (640, 360))
        self.assertEqual(validate_image(image, (640, 360)).size, (640, 360))

    def test_provider_failure_uses_and_caches_procedural_fallback(self):
        def fail(prompt, size, timeout):
            raise RuntimeError("offline")
        with tempfile.TemporaryDirectory() as tmp:
            chain = ImageProviderChain(Path(tmp), providers=[("offline", fail)], retries=0)
            first, meta1 = chain.generate("a student in a library")
            second, meta2 = chain.generate("a student in a library")
            self.assertEqual(first.size, (1280, 720))
            self.assertEqual(meta1["provider"], "procedural")
            self.assertTrue(meta2["cached"])
            self.assertEqual(meta1["sha1"], meta2["sha1"])


if __name__ == "__main__":
    unittest.main()
