import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import fairytale_daily
from fairytale_planner import initial_state, validate_episode_plan


def sample_story(story_id="test-story"):
    return {
        "id": story_id, "title": "The Test Tale",
        "synopsis": "A heroine faces a forest danger, solves a puzzle and returns home safely.",
        "key_beats": ["She leaves", "A warning arrives", "Danger blocks the way", "She solves the puzzle", "She returns"],
        "source": {"title": "Test Collection", "author": "Traditional",
                   "url": "https://www.gutenberg.org/ebooks/11027",
                   "edition_note": "Test source edition."},
    }


def sample_plan(story):
    characters = [
        {"id": "hero", "name": "Mira", "role": "heroine",
         "visual_identity": "Young woman with a green cloak and long dark braid."},
        {"id": "helper", "name": "Rowan", "role": "helper",
         "visual_identity": "Elderly traveller with a grey beard and brown coat."},
    ]
    lines = [
        "Mira left her village before sunrise, carrying one small bag and a promise she hoped she could keep. She had never travelled alone.",
        "At the forest edge she heard a voice calling her name from behind an old stone wall. Someone seemed to know her.",
        "An elderly traveller stepped out and warned her that a dangerous creature guarded the narrow bridge ahead, especially after sunset when the last daylight disappeared behind the hills.",
        "Mira listened carefully and noticed a hidden path beneath the tangled roots of an ancient tree. It led towards the bridge.",
        "She followed the path, but the creature heard her footsteps and blocked the bridge with a roar. There was nowhere else to cross.",
        "The traveller returned and showed her how to ring the bell hanging from the wooden bridge post. Its sound would frighten the creature.",
        "The sound startled the creature long enough for Mira to cross and reach the far side safely. She thanked the traveller and hurried home.",
        "When she returned home, Mira kept her promise and told the village how the traveller had helped her. The journey had changed her.",
    ]
    raw = {
        "title": "The Hidden Path",
        "logline": "A brave young woman finds a way through a dangerous forest.",
        "characters": characters,
        "scenes": [
            {"title": f"Scene {i}", "narration": line,
             "visual_prompt": "Cinematic painterly storybook illustration showing the named characters in a dark forest beside an ancient stone bridge, with one clear visible action and dramatic moonlight.",
             "character_ids": ["hero"] if i not in {3, 6} else ["hero", "helper"]}
            for i, line in enumerate(lines, 1)
        ],
    }
    return validate_episode_plan(raw, story)


class DailyFairytaleRunnerTests(unittest.TestCase):
    def _run_with_mocks(self, root: Path, fail_render: bool = False):
        queue_story = sample_story()
        (root / "fairytale_queue.json").write_text(json.dumps({
            "schema_version": 1, "stories": [queue_story] + [sample_story(f"filler-{i}") for i in range(1, 8)]
        }), encoding="utf-8")
        plan = sample_plan(queue_story)

        def fake_generate_scenes(**kwargs):
            return {
                "scene_count": 8, "images": [{} for _ in range(8)],
                "approved": False, "published": False,
            }

        def fake_render(plan_path, images_dir, output_dir, voice, speed):
            if fail_render:
                raise RuntimeError("simulated render failure")
            output_dir.mkdir(parents=True, exist_ok=True)
            video = output_dir / "video.mp4"
            video.write_bytes(b"x" * 150000)
            (output_dir / "captions.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nOnce upon a time.\n",
                encoding="utf-8",
            )
            (output_dir / "contact-sheet.jpg").write_bytes(b"contact-sheet")
            metadata = {
                "scene_count": 8, "chapter_overlays": False,
                "opening": "full_bleed_scene_one_no_title_overlay",
                "narration_word_count": plan["narration_word_count"],
                "approved": False, "published": False,
                "duration_seconds": 90.0, "resolution": "1080x1920",
                "fps": 30, "voice": voice,
            }
            return video, metadata

        env = {
            "CLOUDFLARE_ACCOUNT_ID": "test-account",
            "CLOUDFLARE_API_TOKEN": "test-token",
            "GROQ_API_KEY": "test-key",
            "EPISODE_DATE": "2026-11-14",
            "GITHUB_RUN_ID": "12345",
            "GITHUB_RUN_ATTEMPT": "1",
        }
        with patch.object(fairytale_daily, "ROOT", root), \
             patch.object(fairytale_daily, "JsonLLM", return_value=Mock()), \
             patch.object(fairytale_daily, "generate_episode", return_value=plan), \
             patch.object(fairytale_daily, "generate_scenes", side_effect=fake_generate_scenes), \
             patch.object(fairytale_daily, "render", side_effect=fake_render):
            return fairytale_daily.run(
                root / "fairytale_queue.json",
                root / "state" / "fairytale_rotation.json",
                root / "output",
                env=env,
            )

    def test_state_advances_only_after_a_complete_private_review_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run_with_mocks(root)
            state = json.loads((root / "state" / "fairytale_rotation.json").read_text())
            self.assertEqual(result["status"], "private_review_ready")
            self.assertEqual(state["completed_story_ids"], ["test-story"])
            self.assertFalse(result["approved"])
            self.assertFalse(result["published"])
            self.assertEqual(result["image_count"], 8)

    def test_failed_render_does_not_advance_story_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "simulated render failure"):
                self._run_with_mocks(root, fail_render=True)
            state_path = root / "state" / "fairytale_rotation.json"
            self.assertFalse(state_path.exists())


if __name__ == "__main__":
    unittest.main()
