import unittest
from destination_history import validate_asset, validate_destination_plan, credits_text


def plan():
    kinds = ["still_kenburns", "map", "icon_card", "split_compare",
             "still_kenburns", "icon_card", "still_kenburns", "icon_card",
             "still_kenburns", "map", "icon_card", "still_kenburns",
             "split_compare", "icon_card", "still_kenburns", "icon_card"]
    return {"title":"Tokyo", "layout":"framed", "aspect_ratio":"9:16",
            "scenes":[{"id":f"s{i}", "type":kind, "duration_seconds":3,
                       "narration":"A sourced narration beat.", "source_ids":["source"]}
                      for i,kind in enumerate(kinds)]}


class DestinationHistoryTests(unittest.TestCase):
    def test_plan_validates_and_defaults_framed(self):
        result=validate_destination_plan(plan())
        self.assertEqual(result["layout"], "framed")
        self.assertEqual(result["duration_seconds"], 48)
        self.assertEqual(result["status"], "needs_human_review")

    def test_unknown_layout_rejected(self):
        data=plan(); data["layout"]="cinema"
        with self.assertRaises(ValueError): validate_destination_plan(data)

    def test_invalid_duration_rejected(self):
        data=plan(); data["scenes"][0]["duration_seconds"]=1
        with self.assertRaises(ValueError): validate_destination_plan(data)

    def test_unknown_license_rejected(self):
        asset={"source_url":"https://example.org/image","rights_statement":"CC BY 4.0","rights_url":"https://example.org/rights","author":"A",
               "license":"unknown","date_retrieved":"2026-10-17",
               "local_filename":"x.jpg","scene_id":"s1"}
        with self.assertRaises(ValueError): validate_asset(asset)

    def test_cc_by_requires_credit_and_review_flags_surface(self):
        asset={"source_url":"https://example.org/image","rights_statement":"CC BY 4.0","rights_url":"https://example.org/rights","author":"A",
               "license":"CC BY 4.0","date_retrieved":"2026-10-17",
               "local_filename":"x.jpg","scene_id":"s1","review_flags":["face"]}
        result=validate_asset(asset)
        self.assertTrue(result["credit_required"])
        self.assertTrue(result["needs_human_review"])
        self.assertIn("CC BY 4.0", credits_text([asset]))

    def test_public_domain_does_not_require_attribution(self):
        asset={"source_url":"https://example.org/image","rights_statement":"Public Domain","rights_url":"https://example.org/rights","author":"US agency",
               "license":"Public Domain","date_retrieved":"2026-10-17",
               "local_filename":"x.jpg","scene_id":"s1"}
        self.assertFalse(validate_asset(asset)["credit_required"])


if __name__ == "__main__":
    unittest.main()
