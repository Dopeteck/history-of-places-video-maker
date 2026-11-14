import json
import unittest
from pathlib import Path

from folklore_plan import validate_folklore_plan


ROOT = Path(__file__).resolve().parents[1]


class FolklorePlanTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT / "research" / "rumpelstiltskin-pilot.json").read_text(encoding="utf-8"))

    def test_pilot_is_source_bound_and_meets_short_format(self):
        result = validate_folklore_plan(self.plan)
        self.assertEqual(len(result["scenes"]), 9)
        self.assertEqual(result["narration_word_count"], 234)
        self.assertGreaterEqual(result["target_duration_seconds"], 70)
        self.assertTrue(all(scene["source_ids"] for scene in result["scenes"]))

    def test_missing_source_rejected(self):
        self.plan["scenes"][0]["source_ids"] = ["invented"]
        with self.assertRaisesRegex(ValueError, "listed source"):
            validate_folklore_plan(self.plan)

    def test_render_status_cannot_be_auto_approved(self):
        self.plan["status"] = "approved"
        with self.assertRaisesRegex(ValueError, "unapproved draft"):
            validate_folklore_plan(self.plan)

    def test_unknown_asset_status_rejected(self):
        self.plan["scenes"][0]["asset_requirement"]["status"] = "rendered"
        with self.assertRaisesRegex(ValueError, "asset status"):
            validate_folklore_plan(self.plan)

    def test_duration_and_word_count_limits(self):
        self.plan["narration"] = "Too short."
        with self.assertRaisesRegex(ValueError, "150–235"):
            validate_folklore_plan(self.plan)


if __name__ == "__main__":
    unittest.main()
