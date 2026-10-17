import tempfile
import unittest
from pathlib import Path

from destination_asset_resolver import choose_scene_asset


class DestinationAssetResolverTests(unittest.TestCase):
    def test_unapproved_asset_does_not_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.jpg"
            p.write_bytes(b"x")
            manifest = [{"scene_id":"s1","approved":False,"download_validated":True,
                         "local_filename":"a.jpg","media_type":"photo","needs_human_review":False}]
            result = choose_scene_asset({"id":"s1"}, manifest, Path(tmp))
            self.assertEqual(result["status"], "unresolved")

    def test_approved_validated_asset_resolves_after_review_flag_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.jpg"
            p.write_bytes(b"x")
            manifest = [{"scene_id":"s1","approved":True,"download_validated":True,
                         "local_filename":"a.jpg","media_type":"photo","needs_human_review":False}]
            result = choose_scene_asset({"id":"s1"}, manifest, Path(tmp))
            self.assertEqual(result["status"], "selected")
            self.assertEqual(result["fallback"], "archival_still")


    def test_path_traversal_outside_asset_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            p = Path(outside) / "secret.jpg"
            p.write_bytes(b"x")
            manifest = [{"scene_id":"s1","approved":True,"download_validated":True,
                         "local_filename":str(p),"media_type":"photo","needs_human_review":False}]
            result = choose_scene_asset({"id":"s1"}, manifest, Path(tmp))
            self.assertEqual(result["status"], "unresolved")

    def test_missing_asset_blocks_scene(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = [{"scene_id":"s1","approved":True,"download_validated":True,
                         "local_filename":"missing.jpg","media_type":"photo","needs_human_review":False}]
            result = choose_scene_asset({"id":"s1"}, manifest, Path(tmp))
            self.assertEqual(result["status"], "unresolved")
            self.assertEqual(result["fallback_order"][0], "archival_still")


if __name__ == "__main__":
    unittest.main()
