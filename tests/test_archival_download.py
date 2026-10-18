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

    def test_untrusted_host_is_rejected_before_network(self):
        item = self.base_item()
        item["download_url"] = "https://example.org/file.jpg"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "institutional host"):
                download_approved_item(item, Path(tmp))


if __name__ == "__main__":
    unittest.main()
