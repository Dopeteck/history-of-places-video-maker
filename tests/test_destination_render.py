import tempfile
import unittest
from pathlib import Path

from PIL import Image

from destination_render import render_scene_frame, validate_render_inputs


class DestinationRenderTests(unittest.TestCase):
    def test_icon_card_renders_without_character_assets(self):
        frame = render_scene_frame({
            "id": "card", "type": "icon_card", "layout": "framed",
            "title": "TOKYO", "labels": ["EDO TO TOKYO"],
            "narration": "A city with a layered history."
        }, width=270, height=480)
        self.assertEqual(frame.size, (270, 480))

    def test_photo_scene_rejects_missing_manifest_asset(self):
        from destination_history import validate_destination_plan
        plan = {
            "title": "test", "layout": "framed", "aspect_ratio": "9:16",
            "scenes": [
                {"id": f"s{i}", "type": "still_kenburns", "duration_seconds": 3,
                 "narration": "A short sourced narration beat.", "source_ids": ["source"]}
                for i in range(16)
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "no manifest-backed image"):
                validate_render_inputs(validate_destination_plan(plan), [], Path(tmp))

    def test_photo_dimensions_require_1920_long_side_and_1080_short_side(self):
        from destination_render import render_scene_frame
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "small.jpg"
            Image.new("RGB", (1600, 1200), "navy").save(path)
            scene = {"id": "photo", "type": "still_kenburns", "layout": "full-bleed"}
            with self.assertRaisesRegex(ValueError, "long side of at least 1920"):
                render_scene_frame(scene, image_path=path, width=270, height=480)


if __name__ == "__main__":
    unittest.main()
