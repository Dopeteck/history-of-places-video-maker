import unittest

from llm import _json_schema_for_stage
from story import validate_chapter


class ChoreographyValidationTests(unittest.TestCase):
    def _base_scene(self, **overrides):
        scene = {
            "narration": "The guard ran through the gate, found the crown, and pointed at the empty throne.",
            "visual_title": "The Crown Is Missing",
            "characters_on_screen": [
                {"id": "guard", "x": 0.2, "pose": "running", "expression": "shocked"},
                {"id": "ruler", "x": 0.75, "pose": "thinking", "expression": "neutral"},
            ],
            "background": "throne",
            "camera": "push_in",
            "effects": ["pop_in", "shake"],
            "props": ["crown"],
            "emphasis_words": ["empty throne"],
            "source_ids": [],
            "uncertainty": "documented",
        }
        scene.update(overrides)
        return scene

    def _validate(self, scene):
        return validate_chapter(
            {"summary": "A crown goes missing.", "scenes": [scene]},
            {"guard": "The Guard", "ruler": "The Ruler"},
            set(), 1, 1, 1000, minimum_scenes=1,
        )["scenes"][0]

    def test_explicit_beats_are_validated_and_clamped(self):
        scene = self._base_scene(action_beats=[
            {"at": 0, "actions": [
                {"id": "guard", "action": "running", "x": -4, "emotion": "shocked"},
                {"id": "ruler", "action": "think", "x": 0.75, "emotion": "neutral"},
                {"id": "stranger", "action": "punch", "x": 0.5, "emotion": "angry"},
            ]},
            {"at": 0.45, "actions": [
                {"id": "guard", "action": "point", "x": 0.42, "emotion": "happy"},
                {"id": "ruler", "action": "shock", "x": 0.7, "emotion": "shocked"},
            ]},
            {"at": 0.82, "actions": [
                {"id": "guard", "action": "cheer", "x": 0.42, "emotion": "happy"},
                {"id": "ruler", "action": "facepalm", "x": 0.7, "emotion": "sad"},
            ]},
        ])
        result = self._validate(scene)
        self.assertEqual(result["visual_title"], "The Crown Is Missing")
        self.assertEqual([beat["at"] for beat in result["action_beats"]], [0.0, 0.45, 0.82])
        self.assertEqual(result["action_beats"][0]["actions"][0]["action"], "run")
        self.assertEqual(result["action_beats"][0]["actions"][0]["x"], 0.1)
        self.assertNotIn("stranger", {a["id"] for a in result["action_beats"][0]["actions"]})
        self.assertEqual(result["action_beats"][2]["actions"][0]["action"], "cheer")

    def test_legacy_scene_gets_three_narration_aware_action_beats(self):
        result = self._validate(self._base_scene())
        beats = result["action_beats"]
        self.assertEqual(len(beats), 3)
        self.assertEqual([beat["at"] for beat in beats], [0.0, 0.38, 0.74])
        self.assertEqual(beats[1]["actions"][0]["action"], "run")
        self.assertIn(beats[2]["actions"][0]["action"], {"point", "cheer"})

    def test_chapter_json_schema_requires_scene_titles_and_action_beats(self):
        schema = _json_schema_for_stage("chapter")
        self.assertIsNotNone(schema)
        scene_schema = schema["properties"]["scenes"]["items"]
        self.assertIn("visual_title", scene_schema["required"])
        self.assertIn("action_beats", scene_schema["required"])
        beat_schema = scene_schema["properties"]["action_beats"]["items"]
        self.assertIn("at", beat_schema["required"])
        self.assertIn("actions", beat_schema["required"])
        self.assertIn("walk", beat_schema["properties"]["actions"]["items"]["properties"]["action"]["enum"])


if __name__ == "__main__":
    unittest.main()
