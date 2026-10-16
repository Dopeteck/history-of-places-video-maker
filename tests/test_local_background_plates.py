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
            scenes.backdrop.cache_clear()
            with patch.object(scenes,'__file__',str(root/'scenes.py')):
                image=scenes.backdrop('throne',(320,180))
            scenes.backdrop.cache_clear()
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

    def test_full_storyboard_stills_are_not_used_as_backgrounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            assets=root/'assets';assets.mkdir()
            Image.new('RGB',(320,180),'#C0392B').save(assets/'Mansa_Musa_enters_Cairo_marketplace.jpg')
            with patch.object(scenes,'__file__',str(root/'scenes.py')):
                self.assertIsNone(scenes._asset_backdrop('market',(320,180)))

    def test_props_are_not_selected_as_backgrounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            assets=root/'assets'
            assets.mkdir()
            Image.new('RGB',(320,180),'#C0392B').save(assets/'Ancient_parchment_scroll_prop.jpg')
            with patch.object(scenes,'__file__',str(root/'scenes.py')):
                self.assertIsNone(scenes._asset_backdrop('throne',(320,180)))

    def test_magenta_keyed_prop_uses_local_illustration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            assets=root/'assets';assets.mkdir()
            src=Image.new('RGB',(80,80),(255,0,255))
            for x in range(25,55):
                for y in range(25,55):src.putpixel((x,y),(230,190,20))
            src.save(assets/'Ancient_parchment_scroll_prop.jpg',quality=100)
            scenes._prop_asset.cache_clear()
            with patch.object(scenes,'__file__',str(root/'scenes.py')):
                image=scenes._prop_asset('scroll',80)
            self.assertIsNotNone(image)
            self.assertEqual(image.mode,'RGBA')
            self.assertEqual(image.getpixel((5,5))[3],0)
            self.assertEqual(image.getpixel((40,40))[3],255)
            scenes._prop_asset.cache_clear()

    def test_procedural_backdrop_remains_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            scenes.backdrop.cache_clear()
            with patch.object(scenes,'__file__',str(Path(tmp)/'scenes.py')):
                self.assertIsNone(scenes._asset_backdrop('palace',(320,180)))
                self.assertEqual(scenes.backdrop('palace',(320,180)).size,(320,180))
            scenes.backdrop.cache_clear()


if __name__=='__main__':
    unittest.main()
