"""Quota-safe chapter extension, resumable draft progress, and dry-run tests."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm import JsonLLM
from story import build_story, words
from story_cli import main as story_cli_main


def scene(text, character_id="test_narrator"):
    return {
        "narration": text,
        "characters_on_screen": [{
            "id": character_id, "x": 0.5, "pose": "talking", "expression": "neutral",
        }],
        "background": "room", "camera": "static", "effects": ["cut"],
        "props": [], "emphasis_words": [], "source_ids": [], "uncertainty": "medium",
    }


def counted_text(n):
    return " ".join(f"word{i}" for i in range(n))


class FakeStoryLLM:
    def __init__(self, draft_cache_dir=None):
        self.draft_cache_dir = draft_cache_dir
        self.calls = []

    def generate(self, system, prompt, validate):
        task = prompt["task"]
        self.calls.append({"task": task, "prompt": prompt})
        if task == "outline":
            count = int(prompt["count"])
            raw = {
                "title": "Quota Safety Test",
                "hook": "A small decision changes what happens next.",
                "logline": "A test story used to prove resumable generation.",
                "cast": [{
                    "id": "test_narrator", "name": "Test Narrator", "role": "narrator",
                    "trait": "curious", "accessory": "none", "accent": "#1FA2A6",
                }],
                "chapters": [{
                    "title": f"Chapter {i}", "purpose": "Test continuity",
                    "key_events": ["A decision", "A consequence"],
                } for i in range(1, count + 1)],
                "ending": "The test completes without a provider call on resume.",
            }
        elif task == "chapter":
            chapter_number = prompt["chapter"]["number"]
            short = chapter_number == 1
            per_scene = 25 if short else 65
            raw = {
                "summary": f"Running summary {chapter_number}",
                "scenes": [scene(counted_text(per_scene)) for _ in range(4)],
            }
        elif task == "chapter_extend":
            raw = {
                "summary": "The added scenes deepen the existing chapter.",
                "scenes": [scene(counted_text(50)) for _ in range(4)],
            }
        elif task == "claim_review":
            raw = {"scenes": [
                {"scene_id": s["scene_id"], "claims": []} for s in prompt["scenes"]
            ]}
        else:
            raise AssertionError(f"Unexpected stage in test: {task}")
        return validate(raw)


class QuotaSafetyTests(unittest.TestCase):
    def test_short_chapter_gets_one_append_only_extend_call(self):
        llm = FakeStoryLLM()
        story = build_story(llm, "Quota test topic", "long", 8, 4, [])
        tasks = [entry["task"] for entry in llm.calls]
        self.assertEqual(tasks.count("chapter_extend"), 1)
        extend = next(x["prompt"] for x in llm.calls if x["task"] == "chapter_extend")
        self.assertIn("existing_chapter", extend)
        self.assertGreater(extend["word_shortfall"], 0)
        self.assertNotIn("outline", next(x["prompt"] for x in llm.calls if x["task"] == "chapter"))
        first = story["chapters"][0]
        self.assertGreater(first["word_count"], 100)
        self.assertEqual(len(first["scenes"]), 8)
        self.assertEqual(first["scenes"][0]["narration"], counted_text(25))
        self.assertEqual(len({s["id"] for s in first["scenes"]}), len(first["scenes"]))

    def test_groq_429_retries_same_model_after_stated_delay(self):
        class RateLimitError(Exception):
            status_code = 429
            body = {"error": {"message": "Rate limit reached. Please try again in 3.5s."}}

        calls, sleeps = [], []

        def fake_call(provider, model, system, user):
            calls.append(model)
            if len(calls) == 1:
                raise RateLimitError("HTTP 429 rate limit")
            return '{"ok": true}'

        provider = {
            "name": "groq", "type": "openai", "env_key": "TEST_GROQ_API_KEY",
            "models": ["openai/gpt-oss-120b", "openai/gpt-oss-20b"],
        }
        original = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                with patch.dict(os.environ, {"TEST_GROQ_API_KEY": "test-token"}):
                    llm = JsonLLM(call=fake_call, sleep=sleeps.append,
                                  providers=[provider], min_interval=10.0)
                    self.assertEqual(llm.generate("system", {"task": "outline"}, lambda d: d), {"ok": True})
            finally:
                os.chdir(original)
        self.assertEqual(calls, ["openai/gpt-oss-120b", "openai/gpt-oss-120b"])
        self.assertGreaterEqual(len(sleeps), 2)
        self.assertAlmostEqual(sleeps[0], 4.5, places=1)
        self.assertGreaterEqual(sleeps[1], 9.0)

    def test_draft_cache_resumes_outline_and_all_accepted_chapters(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "state" / "draft_cache"
            first_llm = FakeStoryLLM(cache)
            first = build_story(first_llm, "Persistent topic", "long", 8, 4, [])
            self.assertEqual(len(first["chapters"]), 4)
            self.assertEqual([x["task"] for x in first_llm.calls].count("outline"), 1)
            self.assertTrue(list(cache.rglob("outline.json")))
            self.assertEqual(len(list(cache.rglob("chapter_*.json"))), 4)

            second_llm = FakeStoryLLM(cache)
            second = build_story(second_llm, "Persistent topic", "long", 8, 4, [])
            self.assertEqual(second_llm.calls, [])
            self.assertEqual(second["word_count"], first["word_count"])
            self.assertEqual([x["title"] for x in second["chapters"]],
                             [x["title"] for x in first["chapters"]])

    def test_dry_run_cli_end_to_end_without_provider_calls(self):
        import llm
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "drafts"
            state = root / "state"
            args = [
                "--dry-run", "--topic", "Offline quota safety test",
                "--mode", "long", "--minutes", "8", "--parts", "4",
                "--output", str(output), "--state-dir", str(state),
            ]
            with patch.object(llm, "provider_call", side_effect=AssertionError("API call attempted")) as provider:
                self.assertEqual(story_cli_main(args), 0)
                provider.assert_not_called()
            draft_dirs = [path for path in output.iterdir() if path.is_dir()]
            self.assertEqual(len(draft_dirs), 1)
            draft = draft_dirs[0]
            for name in ("story.json", "fact_check.json", "review.template.json", "script.md"):
                self.assertTrue((draft / name).is_file(), name)
            saved = json.loads((draft / "story.json").read_text(encoding="utf-8"))
            self.assertEqual(len(saved["chapters"]), 4)
            self.assertTrue(list((state / "draft_cache").rglob("outline.json")))
            self.assertTrue(list((state / "draft_cache").rglob("chapter_*.json")))


if __name__ == "__main__":
    unittest.main()
