import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import scenes


class FlowBackgroundAssetTests(unittest.TestCase):
    def test_matching_local_plate_is_loaded_and_resized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'assets' / 'backgrounds'
            folder.mkdir(parents=True)
            Image.new('RGB', (900, 600), '#C0392B').save(folder / 'Mali_Empire_throne_room_interior.jpg')
            with patch.object(scenes, '__file__', str(root / 'scenes.py')):
                image = scenes._asset_backdrop('throne', (320, 180))
            self.assertEqual(image.size, (320, 180))
            self.assertEqual(image.getpixel((160, 90)), (192, 57, 43))

    def test_missing_plate_returns_none_for_procedural_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(scenes, '__file__', str(Path(tmp) / 'scenes.py')):
                self.assertIsNone(scenes._asset_backdrop('palace', (320, 180)))

    def test_unmatched_asset_is_not_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'assets' / 'backgrounds'
            folder.mkdir(parents=True)
            Image.new('RGB', (320, 180), '#C0392B').save(folder / 'unrelated_scene.jpg')
            with patch.object(scenes, '__file__', str(Path(tmp) / 'scenes.py')):
                self.assertIsNone(scenes._asset_backdrop('throne', (320, 180)))


if __name__ == '__main__':
    unittest.main()
