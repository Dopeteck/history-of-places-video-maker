import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "destination-prototype"))
from assemble_symbolic_fairytale_review import assemble


class SymbolicFairytaleReviewTests(unittest.TestCase):
    def setUp(self):
        self.plan_path = Path(__file__).resolve().parents[1] / "research" / "rumpelstiltskin-pilot.json"
        self.plan = json.loads(self.plan_path.read_text(encoding="utf-8"))

    def _make_inputs(self, root: Path):
        images = root / "images"
        images.mkdir()
        for index, scene in enumerate(self.plan["scenes"][:-1], 1):
            image = Image.new("RGB", (832, 1472), ((index * 19) % 255, 55, 90))
            image.save(images / f"{index:02d}-{scene['id']}.jpg", format="JPEG", quality=80)
        identity = root / "identity.png"
        scale = root / "scale.png"
        Image.new("RGB", (510, 510), (80, 50, 30)).save(identity)
        Image.new("RGB", (510, 510), (220, 220, 210)).save(scale)
        return images, identity, scale

    def test_builds_nine_scene_unapproved_manifest_with_symbolic_finale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            images, identity, scale = self._make_inputs(root)
            manifest = assemble(self.plan_path, images, identity, scale)
            self.assertEqual(manifest["scene_count"], 9)
            self.assertFalse(manifest["approved"])
            self.assertFalse(manifest["published"])
            self.assertEqual(manifest["generation_mode"], "partial_daily_allocation_symbolic_completion")
            final = manifest["images"][-1]
            self.assertEqual(final["scene_id"], "beat-09-foot-revelation")
            self.assertTrue(final["derived_symbolically"])
            self.assertEqual(final["symbolic_source_scene"], "beat-08-forest-fire")
            self.assertEqual(final["model"], "derived_symbolic_crop")
            self.assertFalse(final["approved"])
            self.assertTrue((images / final["file"]).is_file())
            with Image.open(images / final["file"]) as image:
                self.assertEqual(image.size, (700, 1240))
            saved = json.loads((images / "generation-manifest.json").read_text())
            self.assertEqual(saved["scene_count"], 9)
            self.assertFalse(saved["approved"])

    def test_refuses_to_invent_any_of_the_first_eight_scenes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            images, identity, scale = self._make_inputs(root)
            next(images.glob("*-beat-02-boast.jpg")).unlink()
            with self.assertRaisesRegex(ValueError, "Missing existing generated scene"):
                assemble(self.plan_path, images, identity, scale)

    def test_rejects_reference_images_at_or_above_model_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            images, identity, scale = self._make_inputs(root)
            Image.new("RGB", (512, 510)).save(identity)
            with self.assertRaisesRegex(ValueError, "strictly smaller than 512x512"):
                assemble(self.plan_path, images, identity, scale)


if __name__ == "__main__":
    unittest.main()
