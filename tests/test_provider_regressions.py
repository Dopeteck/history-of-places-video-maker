"""Regression tests for recent provider and outline-validation failures."""
import os
import sys
import types
import unittest
from unittest.mock import patch

import llm
from llm import JsonLLM, ProviderHTTPError, provider_call
from story import validate_outline


class ProviderRegressionTests(unittest.TestCase):
    def test_groq_forbidden_catalogue_is_not_retried(self):
        provider = {
            "name": "groq", "type": "openai", "env_key": "GROQ_API_KEY",
            "models": ["model-a", "model-b"], "verify_models": True,
        }
        client = JsonLLM(providers=[provider])
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}), \
             patch.object(llm, "_groq_models", side_effect=ProviderHTTPError(
                 403, '{"error":"browser_signature_banned"}', "catalogue forbidden"
             )) as catalogue:
            with self.assertRaises(ProviderHTTPError):
                client._model_available(provider, "model-a")
            self.assertFalse(client._model_available(provider, "model-b"))
            catalogue.assert_called_once()

    def test_gemini_client_is_closed_without_context_manager(self):
        response = types.SimpleNamespace(text='{"ok": true}')
        class FakeModels:
            def generate_content(self, **kwargs):
                return response
        class FakeClient:
            def __init__(self):
                self.models = FakeModels()
                self.closed = False
            def close(self):
                self.closed = True
        client = FakeClient()
        genai = types.ModuleType("google.genai")
        genai.Client = lambda **kwargs: client
        sdk_types = types.ModuleType("google.genai.types")
        sdk_types.HttpOptions = lambda **kwargs: object()
        sdk_types.GenerateContentConfig = lambda **kwargs: object()
        google = types.ModuleType("google")
        google.genai = genai
        with patch.dict(sys.modules, {
            "google": google, "google.genai": genai, "google.genai.types": sdk_types,
        }), patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), \
             patch.object(llm.config, "PROVIDER_TIMEOUT_SECONDS", 1):
            result = provider_call(
                {"type": "gemini", "env_key": "GEMINI_API_KEY"},
                "test-model", "system", "user",
            )
        self.assertEqual(result, '{"ok": true}')
        self.assertTrue(client.closed)

    def test_outline_list_values_raise_schema_error_not_type_error(self):
        outline = {
            "title": "A short title",
            "hook": "A hook",
            "logline": "A logline",
            "cast": [{
                "id": "mansa", "name": "Mansa Musa", "role": "ruler",
                "trait": "ambitious", "accessory": ["crown"], "accent": "#1FA2A6",
            }],
            "chapters": [{
                "title": "The journey", "purpose": "Explain the journey",
                "key_events": ["He travelled"],
            }],
            "ending": "An ending",
        }
        with self.assertRaisesRegex(ValueError, "Cast accessory"):
            validate_outline(outline, 1)


if __name__ == "__main__":
    unittest.main()
