import unittest
from pathlib import Path
import yaml

from long_form_config import LongFormConfigError, validate_long_form_config


class LongFormConfigTests(unittest.TestCase):
    def valid_config(self):
        chapter_texts = [
            " ".join([f"Chapter{i} tells the history of this place and its people."] * 10)
            for i in range(1, 5)
        ]
        narration = " ".join(chapter_texts)
        chapters = [
            {"title": f"Chapter {i}", "narration": text}
            for i, text in enumerate(chapter_texts, 1)
        ]
        scenes = []
        for i in range(20):
            scenes.append({
                "title": f"Scene {i + 1}",
                "chapter": f"Chapter {(i // 5) + 1}",
                "narration_intent": "Illustrate a verified part of the chapter story",
                "image_type": "historic" if i % 5 else "map",
                "image_search_terms": ["historical place", "historic streets", "local history image"],
                "required_keywords": ["history", "place"],
            })
        return {
            "name": "Pilot place",
            "country": "Japan",
            "story": {"narration": narration},
            "chapters": chapters,
            "scenes": scenes,
            "sources": ["https://example.org/history"],
            "claim_sources": {
                f"Historical claim {i}": [f"https://example.org/claim-{i}"]
                for i in range(1, 6)
            },
        }

    def test_real_tokyo_long_form_config(self):
        config_path = Path(__file__).resolve().parents[1] / "regions" / "tokyo-long-form.yaml"
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        result = validate_long_form_config(cfg)
        self.assertEqual(result["scene_count"], 20)
        self.assertEqual(result["chapter_count"], 4)
        self.assertGreaterEqual(result["word_count"], 350)
        self.assertLessEqual(result["word_count"], 500)

    def test_valid_three_minute_pilot_config(self):
        result = validate_long_form_config(self.valid_config())
        self.assertEqual(result["chapter_count"], 4)
        self.assertEqual(result["scene_count"], 20)
        self.assertGreaterEqual(result["word_count"], 350)
        self.assertLessEqual(result["word_count"], 500)

    def test_rejects_missing_claim_sources(self):
        cfg = self.valid_config()
        cfg["claim_sources"] = {}
        with self.assertRaisesRegex(LongFormConfigError, "At least five factual claims"):
            validate_long_form_config(cfg)

    def test_rejects_scene_without_search_variants(self):
        cfg = self.valid_config()
        cfg["scenes"][0]["image_search_terms"] = ["only one"]
        with self.assertRaisesRegex(LongFormConfigError, "three image search variants"):
            validate_long_form_config(cfg)

    def test_rejects_chapter_order_mismatch(self):
        cfg = self.valid_config()
        cfg["chapters"][0]["narration"] = "Different narration"
        with self.assertRaisesRegex(LongFormConfigError, "concatenate"):
            validate_long_form_config(cfg)

    def test_rejects_unknown_scene_chapter(self):
        cfg = self.valid_config()
        cfg["scenes"][0]["chapter"] = "Unknown"
        with self.assertRaisesRegex(LongFormConfigError, "known chapter"):
            validate_long_form_config(cfg)

    def test_rejects_non_https_claim_source(self):
        cfg = self.valid_config()
        cfg["claim_sources"]["Historical claim 1"] = ["http://example.org"]
        with self.assertRaisesRegex(LongFormConfigError, "HTTPS"):
            validate_long_form_config(cfg)


if __name__ == "__main__":
    unittest.main()
