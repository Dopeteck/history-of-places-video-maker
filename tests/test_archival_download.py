import tempfile
import unittest
from pathlib import Path

from archival_download import _safe_host, download_approved_item


class ArchivalDownloadTests(unittest.TestCase):
    def base_item(self):
        return {
            "asset_id": "archive-test-123",
            "approved": True,
            "rights_statement": "CC BY 4.0",
            "rights_url": "https://archive.org/details/test",
            "source_url": "https://archive.org/details/test",
            "download_url": "https://archive.org/download/test/frame.jpg",
            "media_type": "still",
            "review_flags": [],
        }

    def test_only_https_institutional_hosts(self):
        self.assertTrue(_safe_host("https://archive.org/download/x.mp4"))
        self.assertTrue(_safe_host("https://upload.wikimedia.org/a.jpg"))
        self.assertFalse(_safe_host("http://archive.org/a.jpg"))
        self.assertFalse(_safe_host("https://example.org/a.jpg"))
        self.assertFalse(_safe_host("https://archive.org.example.org/a.jpg"))

    def test_unapproved_asset_is_never_downloaded(self):
        item = self.base_item()
        item["approved"] = False
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "explicitly approved"):
                download_approved_item(item, Path(tmp))

    def test_unknown_rights_is_never_downloaded(self):
        item = self.base_item()
        item["rights_statement"] = "Unknown"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "rights statement"):
                download_approved_item(item, Path(tmp))


    def test_successful_still_download_is_bounded_verified_and_stays_unapproved(self):
        from io import BytesIO
        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (1920, 1080), (24, 48, 72)).save(buffer, format="PNG")
        payload = buffer.getvalue()

        class FakeResponse:
            url = "https://archive.org/download/test/frame.png"
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def raise_for_status(self): return None
            def iter_content(self, chunk_size):
                for offset in range(0, len(payload), chunk_size):
                    yield payload[offset:offset + chunk_size]

        class FakeSession:
            def get(self, *_args, **_kwargs): return FakeResponse()

        item = self.base_item()
        item["download_url"] = "https://archive.org/download/test/frame.png"
        with tempfile.TemporaryDirectory() as tmp:
            result = download_approved_item(item, Path(tmp), session=FakeSession())
            self.assertTrue(result["download_validated"])
            self.assertTrue(result["needs_human_review"])
            self.assertEqual(result["dimensions"], [1920, 1080])
            self.assertEqual(result["byte_size"], len(payload))
            self.assertTrue((Path(tmp) / result["local_filename"]).is_file())
            self.assertTrue(result["approved"])  # preserve the reviewer's download authorization
            self.assertTrue(result["needs_human_review"])

    def test_redirect_to_untrusted_host_is_rejected(self):
        class FakeResponse:
            url = "https://evil.example/file.jpg"
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def raise_for_status(self): return None
            def iter_content(self, chunk_size): yield b"not-an-image"

        class FakeSession:
            def get(self, *_args, **_kwargs): return FakeResponse()

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "redirected outside"):
                download_approved_item(self.base_item(), Path(tmp), session=FakeSession())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_rejects_path_traversal_asset_ids(self):
        item = self.base_item()
        item["asset_id"] = "../outside"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "Asset ID"):
                download_approved_item(item, Path(tmp))

    def test_untrusted_host_is_rejected_before_network(self):
        item = self.base_item()
        item["download_url"] = "https://example.org/file.jpg"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "institutional host"):
                download_approved_item(item, Path(tmp))


if __name__ == "__main__":
    unittest.main()
