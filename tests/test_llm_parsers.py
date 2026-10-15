"""Regression tests for empty, malformed and provider-error LLM responses."""
import unittest
from types import SimpleNamespace

from llm import extract_json, parse_gemini_response, parse_openai_response


class ProviderParserTests(unittest.TestCase):
    def test_gemini_rejects_none_empty_and_malformed(self):
        for response in (
            None,
            SimpleNamespace(text=None, candidates=None),
            SimpleNamespace(text="", candidates=[]),
            SimpleNamespace(text="{not valid JSON", candidates=None),
            SimpleNamespace(text='{"error":{"message":"quota exceeded"}}', candidates=None),
        ):
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    parse_gemini_response(response)

    def test_gemini_accepts_valid_json(self):
        self.assertEqual(parse_gemini_response(SimpleNamespace(text='{"ok": true}')), '{"ok": true}')

    def test_openai_rejects_none_empty_missing_choices_and_malformed(self):
        for response in (
            None,
            {},
            {"choices": []},
            {"choices": [{"message": {"content": None}}]},
            {"choices": [{"message": {"content": ""}}]},
            {"choices": [{"message": {"content": "{not valid JSON"}}]},
            {"error": {"message": "provider quota exceeded"}},
        ):
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    parse_openai_response(response)

    def test_openai_accepts_valid_json(self):
        response = {"choices": [{"message": {"content": '{"ok": true}'}}]}
        self.assertEqual(parse_openai_response(response), '{"ok": true}')

    def test_extract_json_rejects_empty_and_malformed(self):
        for raw in (None, "", "not json", "{broken", '{"error":{"message":"bad request"}}'):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    extract_json(raw)


if __name__ == "__main__":
    unittest.main()
