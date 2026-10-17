import unittest
from unittest.mock import patch

import archival_footage as af


class ArchivalFootageTests(unittest.TestCase):
    def test_explicit_allowed_rights(self):
        for rights in ("Public Domain", "CC0 1.0", "CC BY 4.0", "CC BY-SA 4.0", "No known copyright restrictions"):
            self.assertTrue(af.rights_allowed(rights), rights)

    def test_unknown_or_noncommercial_rights_rejected(self):
        for rights in ("", "Unknown", "CC BY-NC 4.0", "CC BY-ND 4.0", "All rights reserved", "Permission required"):
            self.assertFalse(af.rights_allowed(rights), rights)

    def test_candidate_keeps_exact_rights_and_url_and_is_unapproved(self):
        item = af._candidate("test", "id", "Title", "https://example.org/item", "https://example.org/file.mp4",
                             "CC BY 4.0", "video", "Creator", extra={"rights_url":"https://example.org/rights"})
        self.assertIsNotNone(item)
        self.assertEqual(item["rights_statement"], "CC BY 4.0")
        self.assertEqual(item["rights_url"], "https://example.org/rights")
        self.assertFalse(item["approved"])

    def test_candidate_rejects_unknown_rights(self):
        self.assertIsNone(af._candidate("test", "id", "Title", "https://example.org/item",
                                       "https://example.org/file.mp4", "Unknown", "video"))

    def test_whitespace_is_normalized(self):
        self.assertTrue(af.rights_allowed("  CC   BY  4.0  "))

    def test_fallback_order(self):
        self.assertEqual(af.choose_fallback({"ai_reconstruction": True, "site_today": True}), "site_today")
        self.assertEqual(af.choose_fallback({"archival_still": True, "map": True}), "archival_still")
        self.assertEqual(af.choose_fallback({}), "unresolved")

    def test_resolver_uses_requested_order_and_continues_after_failure(self):
        with patch.object(af, "RESOLVERS", [
            ("first", lambda q, n: (_ for _ in ()).throw(RuntimeError("offline"))),
            ("second", lambda q, n: [{"asset_id":"ok"}]),
        ]):
            # RuntimeError is a resolver outage too; don't let one endpoint block the chain.
            self.assertEqual(af.resolve_archival("test"), [{"asset_id":"ok"}])


if __name__ == "__main__":
    unittest.main()
