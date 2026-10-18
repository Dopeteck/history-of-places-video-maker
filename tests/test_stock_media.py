import os
import unittest
from unittest.mock import patch

import stock_media


class StockMediaProviderTests(unittest.TestCase):
    def test_query_turns_hyphenated_asset_tags_into_search_words(self):
        self.assertEqual(
            stock_media._query({"asset_tags": ["edo-tokyo-historical-map", "old-city"]}),
            "edo tokyo historical map old city",
        )

    def test_unsplash_video_is_not_supported(self):
        with patch.dict(os.environ, {"UNSPLASH_ACCESS_KEY": "test-key"}):
            self.assertEqual(stock_media._candidates("unsplash", "video", "Tokyo"), [])

    def test_missing_provider_key_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "PEXELS_API_KEY"):
                stock_media._candidates("pexels", "photo", "Tokyo")

    def test_untrusted_download_host_is_rejected_before_network_request(self):
        from pathlib import Path
        with self.assertRaisesRegex(ValueError, "Untrusted stock media download host"):
            stock_media._download("https://example.com/image.jpg", Path("image.jpg"), None)

    def test_provider_license_labels_are_explicit(self):
        self.assertEqual(stock_media.LICENSES["pixabay"], "Pixabay Content License")
        self.assertEqual(stock_media.LICENSES["unsplash"], "Unsplash License")


if __name__ == "__main__":
    unittest.main()
