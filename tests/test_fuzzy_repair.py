"""Fuzz-style regression corpus for resilient LLM output repair."""
import copy
import unittest

from llm import extract_json
from story import (
    ACCESSORIES, ACCENTS, BACKGROUNDS, CAMERAS, EFFECTS, EXPRESSIONS, POSES, PROPS,
    validate_chapter, validate_outline, validate_saved_story,
)


def outline_base():
    return {
        "title": "The Mali Empire",
        "hook": "A journey reshaped a kingdom's reputation.",
        "logline": "A ruler's pilgrimage becomes a global story.",
        "cast": [{
            "id": "mansa_musa", "name": "Mansa Musa", "role": "ruler",
            "trait": "ambitious", "accessory": "crown", "accent": "#1FA2A6",
        }],
        "chapters": [{
            "title": "The journey", "purpose": "Explain the journey",
            "key_events": ["He travelled"],
        }],
        "ending": "The journey echoed beyond Mali.",
    }


def chapter_base():
    scene = {
        "narration": "Mansa Musa begins a remarkable journey.",
        "characters_on_screen": [{
            "id": "mansa_musa", "x": 0.4, "pose": "standing", "expression": "neutral",
        }],
        "background": "palace",
        "camera": "push_in",
        "effects": ["cut"],
        "props": ["coin"],
        "emphasis_words": [],
        "source_ids": [],
        "uncertainty": "unverified",
    }
    return {"summary": "The journey begins.", "scenes": [copy.deepcopy(scene) for _ in range(4)]}


class OutlineFuzzCorpusTests(unittest.TestCase):
    def test_sloppy_outline_corpus_is_repaired_without_key_errors(self):
        cases = []

        missing = outline_base()
        for key in ("title", "hook", "logline", "cast", "chapters", "ending"):
            missing.pop(key, None)
        cases.append(missing)

        wrong_types = outline_base()
        wrong_types.update({"title": [], "hook": None, "logline": 17, "cast": "not-a-list",
                            "chapters": {"unexpected": "extra"}, "ending": {}})
        cases.append(wrong_types)

        malformed_character = outline_base()
        malformed_character["cast"] = [
            None,
            {"name": [], "role": 99, "trait": None, "accessory": ["headscarf"],
             "accent": ["teal"], "extra_field": True},
        ]
        malformed_character["chapters"] = [{"title": None, "purpose": [], "key_events": "event"}]
        cases.append(malformed_character)

        aliases_and_colour_names = outline_base()
        aliases_and_colour_names.pop("cast")
        aliases_and_colour_names["characters"] = [{
            "character_id": "bad id", "character_name": "Amina",
            "function": "traveller", "personality": "curious",
            "wearing": ["headscarf"], "colour": "charcoal/black", "extra": "ignored",
        }]
        aliases_and_colour_names.pop("chapters")
        aliases_and_colour_names["chapter_list"] = [
            {"heading": "Departure", "summary": "Start the story", "events": ["Leaves home"]}
        ]
        cases.append(aliases_and_colour_names)

        cases.extend([
            {"cast": [{"accent": "TEAL"}], "chapters": []},
            {"cast": [{"accent": "mustard/yellow/gold", "accessory": {"wrong": "type"}}],
             "chapters": [{"key_events": None}]},
            {"cast": [{"accent": "#ffffff"}], "chapters": [{"key_events": ["a", None, 2]}],
             "unknown_root_key": "preserve nothing unexpected"},
            {"cast": [{"accent": "chartreuse"}], "chapters": ["A string chapter", None]},
        ])

        for index, payload in enumerate(cases):
            with self.subTest(corpus_case=index):
                try:
                    result = validate_outline(payload, 1)
                except KeyError as exc:
                    self.fail(f"Repair layer leaked KeyError for corpus case {index}: {exc}")
                except ValueError as exc:
                    self.fail(f"Repairable outline raised ValueError in case {index}: {exc}")
                self.assertTrue(result["title"])
                self.assertEqual(len(result["chapters"]), 1)
                self.assertGreaterEqual(len(result["cast"]), 1)
                for character in result["cast"]:
                    self.assertIn(character["accent"], ACCENTS)
                    self.assertIn(character["accessory"], ACCESSORIES)
                    self.assertIsInstance(character["id"], str)
                self.assertTrue(result["hook"] and result["logline"] and result["ending"])

    def test_accent_normalisation_never_rejects_provider_values(self):
        values = ["#1fa2a6", "#2b2b2b", "#c0392b", "#e0a526", "teal",
                  "charcoal", "black", "charcoal/black", "red", "mustard",
                  "yellow", "gold", "#ffffff", "unknown-colour", [], None, 12]
        for value in values:
            with self.subTest(value=value):
                payload = outline_base()
                payload["cast"][0]["accent"] = value
                repaired = validate_outline(payload, 1)
                self.assertIn(repaired["cast"][0]["accent"], ACCENTS)


class ChapterFuzzCorpusTests(unittest.TestCase):
    def run_repair(self, payload):
        return validate_chapter(
            payload, {"mansa_musa": "Mansa Musa"}, set(), 1, 1, 5000,
        )

    def test_sloppy_chapter_corpus_repairs_or_raises_value_error_only(self):
        baseline = chapter_base()
        cases = [copy.deepcopy(baseline)]

        missing_uncertainty = copy.deepcopy(baseline)
        for scene in missing_uncertainty["scenes"]:
            scene.pop("uncertainty", None)
            scene.pop("source_ids", None)
        cases.append(missing_uncertainty)

        alternate_scene_key = copy.deepcopy(baseline)
        alternate_scene_key["scene_list"] = alternate_scene_key.pop("scenes")
        cases.append(alternate_scene_key)

        wrong_types = copy.deepcopy(baseline)
        wrong_types["summary"] = []
        for scene in wrong_types["scenes"]:
            scene.update({
                "narration": None, "characters_on_screen": {"name": "Mansa Musa", "x": "8.1",
                                                               "pose": "talkng", "expression": "surprisd"},
                "background": "palacc", "camera": "push-in", "effects": "fadz",
                "props": ["Staff", {"prop": "unknown"}, "coin"],
                "source_ids": [], "emphasis_words": 7,
                "extra": "ignored",
            })
        cases.append(wrong_types)

        missing_fields = copy.deepcopy(baseline)
        missing_fields["scenes"] = [{}, None, "not-an-object", {"text": "Short"}]
        missing_fields.pop("summary", None)
        cases.append(missing_fields)

        invalid_numeric_values = copy.deepcopy(baseline)
        for scene in invalid_numeric_values["scenes"]:
            scene["characters_on_screen"] = [
                {"id": "missing", "x": 200, "pose": "runing", "expression": "mad"},
                {"id": "mansa_musa", "x": float("nan"), "pose": "standing", "expression": "neutral"},
            ]
            scene["background"] = "not-a-background"
            scene["camera"] = "not-a-camera"
            scene["effects"] = ["shake_camera", "cut"]
            scene["uncertainty_level"] = "likely"
        cases.append(invalid_numeric_values)

        for index, payload in enumerate(cases):
            with self.subTest(corpus_case=index):
                try:
                    result = self.run_repair(payload)
                except KeyError as exc:
                    self.fail(f"Repair layer leaked KeyError for corpus case {index}: {exc}")
                except ValueError as exc:
                    self.fail(f"Repairable chapter raised ValueError in case {index}: {exc}")
                self.assertGreaterEqual(len(result["scenes"]), 4)
                for scene in result["scenes"]:
                    self.assertIn(scene["background"], BACKGROUNDS)
                    self.assertIn(scene["camera"], CAMERAS)
                    self.assertIn(scene["uncertainty"], {"documented", "disputed", "legend", "unverified", "medium"})
                    self.assertTrue(set(scene["effects"]).issubset(EFFECTS))
                    self.assertTrue(set(scene["props"]).issubset(PROPS))
                    for actor in scene["characters_on_screen"]:
                        self.assertIn(actor["pose"], POSES)
                        self.assertIn(actor["expression"], EXPRESSIONS)
                        self.assertGreaterEqual(actor["x"], 0.1)
                        self.assertLessEqual(actor["x"], 0.9)

    def test_missing_or_empty_scene_collection_raises_clear_value_error(self):
        for payload in ({}, {"scenes": []}, {"scenes": None}, {"scene_list": ""}):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "scenes/scene_list"):
                    self.run_repair(payload)

    def test_enum_and_missing_uncertainty_defaults(self):
        payload = chapter_base()
        for scene in payload["scenes"]:
            scene.pop("uncertainty")
            scene["source_ids"] = []
            scene["characters_on_screen"][0]["pose"] = "talkng"
            scene["characters_on_screen"][0]["expression"] = "neutral"
        result = self.run_repair(payload)
        self.assertEqual(result["scenes"][0]["uncertainty"], "medium")
        self.assertEqual(result["scenes"][0]["characters_on_screen"][0]["pose"], "talking")

    def test_unknown_source_reference_is_a_clear_value_error(self):
        payload = chapter_base()
        for scene in payload["scenes"]:
            scene["source_ids"] = ["invented_source_id"]
            scene["uncertainty"] = "documented"
        with self.assertRaisesRegex(ValueError, "source not supplied"):
            self.run_repair(payload)

    def test_saved_story_scene_alias_does_not_leak_key_error(self):
        outline = outline_base()
        outline["chapters"] = [
            {"title": f"Part {index}", "purpose": "Advance the story",
             "key_events": ["Continue the journey"]}
            for index in range(1, 4)
        ]
        chapter_rows = []
        words_per_scene = 6
        for number in range(1, 4):
            chapter = chapter_base()
            chapter["number"] = number
            chapter["title"] = f"Part {number}"
            chapter["word_count"] = words_per_scene * len(chapter["scenes"])
            for scene_index, scene in enumerate(chapter["scenes"], 1):
                scene["id"] = f"c{number:02d}s{scene_index:03d}"
            if number == 1:
                chapter["scene_list"] = chapter.pop("scenes")
            chapter_rows.append(chapter)
        saved = {
            "schema_version": 1, "mode": "long", "sources": [], "outline": outline,
            "chapters": chapter_rows, "word_count": 3 * words_per_scene * 4,
        }
        self.assertIs(validate_saved_story(saved), saved)
        broken = copy.deepcopy(saved)
        broken["chapters"][0].pop("scene_list")
        with self.assertRaisesRegex(ValueError, "scenes/scene_list"):
            validate_saved_story(broken)


class RawJSONFuzzCorpusTests(unittest.TestCase):
    def test_empty_text_and_text_around_json_are_safe(self):
        corpus = [
            ('prefix text {"ok": true} suffix text', {"ok": True}),
            ('json marker: {"ok": true}', {"ok": True}),
            ('<think>ignored</think>{"ok": true}', {"ok": True}),
            ('{"outer":{"inner":1}} trailing', {"outer": {"inner": 1}}),
        ]
        for text, expected in corpus:
            with self.subTest(text=text):
                try:
                    self.assertEqual(extract_json(text), expected)
                except KeyError as exc:
                    self.fail(f"JSON extractor leaked KeyError: {exc}")
        for text in ("", "   ", "not JSON", "[]", "null"):
            with self.subTest(empty_or_invalid=text):
                with self.assertRaises(ValueError):
                    extract_json(text)


if __name__ == "__main__":
    unittest.main()
