import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import scenes


class LocalBackgroundPlateTests(unittest.TestCase):
    def test_background_plate_in_assets_root_is_loaded_and_resized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            assets=root/'assets'
            assets.mkdir()
            Image.new('RGB',(900,600),'#C0392B').save(assets/'Mali_Empire_throne_room_interior.jpg')
            with patch.object(scenes,'__file__',str(root/'scenes.py')):
                image=scenes._asset_backdrop('throne',(320,180))
            self.assertIsNotNone(image)
            self.assertEqual(image.size,(320,180))
            self.assertLessEqual(max(abs(a-b) for a,b in zip(image.getpixel((160,90)),(192,57,43))),2)

    def test_explicit_background_folder_has_priority_on_equal_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'assets'/'backgrounds').mkdir(parents=True)
            Image.new('RGB',(320,180),'#1FA2A6').save(root/'assets'/'backgrounds'/'palace.png')
            Image.new('RGB',(320,180),'#C0392B').save(root/'assets'/'palace.jpg')
            with patch.object(scenes,'__file__',str(root/'scenes.py')):
                image=scenes._asset_backdrop('palace',(320,180))
            self.assertEqual(image.getpixel((160,90)),(31,162,166))

    def test_props_are_not_selected_as_backgrounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            assets=root/'assets'
            assets.mkdir()
            Image.new('RGB',(320,180),'#C0392B').save(assets/'Golden_crown_illustration.jpg')
            with patch.object(scenes,'__file__',str(root/'scenes.py')):
                self.assertIsNone(scenes._asset_backdrop('throne',(320,180)))

    def test_procedural_backdrop_remains_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(scenes,'__file__',str(Path(tmp)/'scenes.py')):
                self.assertIsNone(scenes._asset_backdrop('palace',(320,180)))
                self.assertEqual(scenes.backdrop('palace',(320,180)).size,(320,180))


if __name__=='__main__':
    unittest.main()
