import json
import unittest
from pathlib import Path

from fairytale_planner import (
    initial_state, load_queue, next_story, record_success, validate_episode_plan
)


ROOT = Path(__file__).resolve().parents[1]


def queue_story():
    return {
        "id": "test-tale",
        "title": "The Test Tale",
        "synopsis": "A brave child discovers a secret path, faces a challenge, and returns home.",
        "key_beats": ["A child leaves", "A helper appears", "A danger is faced", "The child returns", "The family reunites"],
        "source": {
            "title": "Test Collection", "author": "Traditional",
            "url": "https://www.gutenberg.org/ebooks/11027",
            "edition_note": "Test source edition.",
        },
    }


def valid_plan(story=None):
    story = story or queue_story()
    characters = [
        {"id": "hero", "name": "Mira", "role": "young heroine",
         "visual_identity": "A young woman with a dark green cloak and long black braid."},
        {"id": "helper", "name": "Old Rowan", "role": "forest helper",
         "visual_identity": "An elderly traveller with a grey beard, brown coat and walking staff."},
    ]
    narrations = [
        "Mira left her village before sunrise, carrying one small bag and a promise she hoped she could keep. She had never travelled alone.",
        "At the edge of the forest, she heard a voice calling from behind the old stone wall. Someone seemed to know her name.",
        "An old traveller stepped out and warned her that the shortcut was guarded by a dangerous creature that feared only one sound.",
        "Mira listened carefully, then noticed a narrow path hidden beneath the roots of an ancient tree. It led towards the bridge.",
        "She followed the path, but the creature heard her footsteps and blocked the bridge ahead. There was nowhere else to cross.",
        "The traveller returned and showed her how to use the bell hanging from the bridge's wooden post. Its sound would frighten the creature.",
        "The sound startled the creature long enough for Mira to cross and reach the far side safely. She thanked the traveller and hurried home.",
        "When she returned home, Mira kept her promise and told the village how the traveller had helped her. The journey had changed her.",
    ]
    scenes = []
    for i, narration in enumerate(narrations, 1):
        scenes.append({
            "title": f"Scene {i}",
            "narration": narration,
            "visual_prompt": "A cinematic storybook view showing the heroine in a dark forest beside an old stone bridge, dramatic moonlight and clear action.",
            "character_ids": ["hero"] if i not in {3, 6} else ["hero", "helper"],
        })
    return {
        "title": "The Hidden Path",
        "logline": "A brave child faces a forest danger and returns with a lesson.",
        "characters": characters,
        "scenes": scenes,
    }


class FairytalePlannerTests(unittest.TestCase):
    def test_queue_has_many_unique_source_linked_public_domain_tales(self):
        queue = load_queue(ROOT / "fairytale_queue.json")
        self.assertGreaterEqual(len(queue), 20)
        self.assertEqual(len({x["id"] for x in queue}), len(queue))
        self.assertTrue(all(x["source"]["url"].startswith("https://www.gutenberg.org/ebooks/") for x in queue))

    def test_rotation_never_repeats_until_the_cycle_is_complete(self):
        queue = load_queue(ROOT / "fairytale_queue.json")
        state = initial_state()
        selected = []
        for _ in range(len(queue)):
            item, state = next_story(queue, state)
            selected.append(item["id"])
            state = record_success(state, item, "2026-11-14", "test-run")
        self.assertEqual(len(selected), len(set(selected)))
        item, state = next_story(queue, state)
        self.assertEqual(state["cycle"], 2)
        self.assertNotEqual(item["id"], selected[-1])

    def test_episode_plan_contains_eight_scenes_and_complete_narration(self):
        story = queue_story()
        plan = validate_episode_plan(valid_plan(story), story)
        self.assertEqual(plan["format"], "dark_fairytale_episode_v1")
        self.assertEqual(len(plan["scenes"]), 8)
        self.assertEqual(plan["narration_word_count"], len(plan["narration"].split()))
        self.assertFalse(plan["approved"])
        self.assertFalse(plan["published"])
        self.assertIn("public-domain edition", plan["disclosure"])

    def test_saved_episode_plan_revalidates_without_queue_object(self):
        plan = validate_episode_plan(valid_plan(), queue_story())
        saved = validate_episode_plan(plan)
        self.assertEqual(saved["story_id"], "test-tale")
        self.assertEqual(saved["source"]["title"], "Test Collection")

    def test_episode_plan_rejects_missing_scene_or_unknown_character(self):
        raw = valid_plan()
        raw["scenes"].pop()
        with self.assertRaisesRegex(ValueError, "exactly eight"):
            validate_episode_plan(raw, queue_story())
        raw = valid_plan()
        raw["scenes"][0]["character_ids"] = ["ghost"]
        with self.assertRaisesRegex(ValueError, "defined characters"):
            validate_episode_plan(raw, queue_story())


if __name__ == "__main__":
    unittest.main()
