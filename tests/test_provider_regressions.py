"""Provider-response regression tests."""
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import llm
from llm import JsonLLM, provider_call


class ProviderRegressionTests(unittest.TestCase):
    def test_groq_catalogue_is_not_part_of_the_call_path(self):
        self.assertFalse(hasattr(llm, "_groq_models"))
        self.assertFalse(hasattr(llm.JsonLLM, "_model_available"))

    def test_groq_gpt_oss_retries_json_validate_failed_without_response_format(self):
        api_key = "sk-" + ("Q" * 28)
        error_body = {
            "error": {
                "code": "json_validate_failed",
                "message": "Generated JSON failed validation",
                "failed_generation": "",
                "api_key": api_key,
            }
        }

        class FakeAPIError(Exception):
            status_code = 400
            body = error_body

        response = types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(
                content='The JSON result is below:\n{"summary":"Recovered","scenes":[]} trailing text'
            ))]
        )
        requests = []

        class FakeCompletions:
            def create(self, **kwargs):
                requests.append(kwargs)
                if len(requests) == 1:
                    raise FakeAPIError("json_validate_failed")
                return response

        fake_client = types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=FakeCompletions())
        )

        class FakeOpenAI:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
            def __enter__(self):
                return fake_client
            def __exit__(self, *args):
                return False

        fake_openai_module = types.ModuleType("openai")
        fake_openai_module.OpenAI = FakeOpenAI
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            try:
                with patch.dict(sys.modules, {"openai": fake_openai_module}), \
                     patch.dict(os.environ, {"GROQ_API_KEY": api_key}):
                    raw = provider_call(
                        {"name": "groq", "type": "openai", "env_key": "GROQ_API_KEY",
                         "base_url": "https://api.groq.com/openai/v1", "json_mode": True},
                        "openai/gpt-oss-120b", "system",
                        json.dumps({"task": "chapter", "schema": {"scenes": []}}),
                    )
                self.assertEqual(llm.extract_json(raw)["summary"], "Recovered")
                self.assertEqual(len(requests), 2)
                first = requests[0]
                second = requests[1]
                self.assertEqual(first["max_completion_tokens"], 3000)
                self.assertEqual(first["reasoning_effort"], "low")
                self.assertEqual(first["response_format"]["type"], "json_schema")
                self.assertTrue(first["response_format"]["json_schema"]["strict"])
                self.assertNotIn("response_format", second)
                self.assertEqual(second["max_completion_tokens"], 3000)
                self.assertEqual(second["reasoning_effort"], "low")
                debug_file = os.path.join(temp_dir, "debug", "chapter_groq_1.txt")
                self.assertTrue(os.path.exists(debug_file))
                with open(debug_file, encoding="utf-8") as handle:
                    debug_text = handle.read()
                self.assertIn("json_validate_failed", debug_text)
                self.assertNotIn(api_key, debug_text)
            finally:
                os.chdir(original_cwd)

    def test_groq_20b_uses_structured_outputs_and_low_reasoning(self):
        requests = []
        response = types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='{"ok":true}'))]
        )

        class FakeCompletions:
            def create(self, **kwargs):
                requests.append(kwargs)
                return response

        fake_client = types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=FakeCompletions())
        )

        class FakeOpenAI:
            def __init__(self, **kwargs): pass
            def __enter__(self): return fake_client
            def __exit__(self, *args): return False

        fake_openai_module = types.ModuleType("openai")
        fake_openai_module.OpenAI = FakeOpenAI
        with patch.dict(sys.modules, {"openai": fake_openai_module}), \
             patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            result = provider_call(
                {"name": "groq", "type": "openai", "env_key": "GROQ_API_KEY",
                 "base_url": "https://api.groq.com/openai/v1"},
                "openai/gpt-oss-20b", "system",
                json.dumps({"task": "outline", "schema": {}}),
            )
        self.assertEqual(llm.extract_json(result), {"ok": True})
        self.assertEqual(requests[0]["reasoning_effort"], "low")
        self.assertEqual(requests[0]["max_completion_tokens"], 2200)
        self.assertEqual(requests[0]["response_format"]["type"], "json_schema")

    def test_gemini_has_60_second_timeout_and_response_schema(self):
        options = []
        configs = []
        response = types.SimpleNamespace(text='{"ok": true}')

        class FakeModels:
            def generate_content(self, **kwargs):
                return response

        class FakeClient:
            models = FakeModels()
            def close(self): pass

        client = FakeClient()
        genai = types.ModuleType("google.genai")
        genai.Client = lambda **kwargs: client
        sdk_types = types.ModuleType("google.genai.types")
        sdk_types.HttpOptions = lambda **kwargs: options.append(kwargs) or kwargs
        sdk_types.GenerateContentConfig = lambda **kwargs: configs.append(kwargs) or kwargs
        google = types.ModuleType("google")
        google.genai = genai
        with patch.dict(sys.modules, {
            "google": google, "google.genai": genai, "google.genai.types": sdk_types,
        }), patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            result = provider_call(
                {"name": "gemini", "type": "gemini", "env_key": "GEMINI_API_KEY"},
                "gemini-2.5-flash", "system",
                json.dumps({"task": "outline", "schema": {}}),
            )
        self.assertEqual(llm.extract_json(result), {"ok": True})
        self.assertEqual(options, [{"timeout": 360000}])
        self.assertIn("response_schema", configs[0])
        self.assertEqual(configs[0]["response_schema"]["type"], "OBJECT")


if __name__ == "__main__":
    unittest.main()
