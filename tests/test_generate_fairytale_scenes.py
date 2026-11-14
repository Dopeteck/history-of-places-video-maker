import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "destination-prototype"))
import generate_fairytale_scenes as generator
from fairytale_planner import validate_episode_plan


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.plan = {
            "title": "Rumpelstiltskin: The Price of a Promise",
            "scenes": [
                {"id": "beat-01-threat", "title": "A threat before the magic",
                 "narration": "Spin straw into gold by morning."},
                {"id": "beat-05-child-pact", "title": "The child pact",
                 "narration": "She bargains away her first child."},
                {"id": "beat-08-forest-fire", "title": "A dance in the forest",
                 "narration": "A messenger finds him dancing by a fire."},
            ],
        }
        buffer = io.BytesIO()
        Image.new("RGB", (832, 1472), (12, 34, 56)).save(buffer, format="JPEG", quality=82)
        self.image_bytes = buffer.getvalue()
        self.image_b64 = base64.b64encode(self.image_bytes).decode("ascii")

    def _generic_plan(self):
        story = {
            "id": "test-tale", "title": "The Test Tale",
            "synopsis": "A brave heroine faces a forest danger and returns safely.",
            "key_beats": ["She leaves", "A stranger warns her", "She faces danger", "She returns", "The village is safe"],
            "source": {"title": "Test Collection", "author": "Traditional",
                       "url": "https://www.gutenberg.org/ebooks/11027",
                       "edition_note": "Test edition."},
        }
        characters = [
            {"id": "hero", "name": "Mira", "role": "heroine",
             "visual_identity": "Young woman with a green cloak, long black braid and brown boots."},
            {"id": "helper", "name": "Rowan", "role": "helper",
             "visual_identity": "Elderly traveller with a grey beard, weathered brown coat and walking staff."},
        ]
        narration = [
            "Mira left her village before sunrise, carrying one small bag and a promise she hoped she could keep, even though the forest frightened her.",
            "At the forest edge she heard a voice calling her name from behind an old stone wall. The voice sounded close, but the path ahead was empty.",
            "An elderly traveller warned her that a dangerous creature guarded the narrow bridge ahead, especially after sunset when the last daylight disappeared behind the hills. He spoke as if the creature could hear them.",
            "Mira listened carefully and noticed a hidden path beneath the tangled roots of an ancient tree. It was narrow, but it seemed to lead around the danger.",
            "She followed the path, but the creature heard her footsteps and blocked the bridge with a roar. Turning back would mean breaking her promise.",
            "The traveller returned and showed her how to ring the bell hanging from the wooden bridge post. He pressed its rope into her hand.",
            "Its clear sound startled the creature long enough for Mira to cross and reach the far side safely. She ran before the creature could recover.",
            "When Mira returned home, she kept her promise and told the village how the traveller had helped her. This time, she did not feel alone."
        ]
        raw = {
            "title": "The Hidden Path",
            "logline": "A brave young woman finds a way through a dangerous forest.",
            "characters": characters,
            "scenes": [
                {"title": f"Scene {i}", "narration": line,
                 "visual_prompt": "Cinematic painterly storybook illustration showing the named characters in a dark forest beside an ancient stone bridge, with one clear visible action and dramatic moonlight.",
                 "character_ids": ["hero"] if i not in {3, 6} else ["hero", "helper"]}
                for i, line in enumerate(narration, 1)
            ],
        }
        return validate_episode_plan(raw, story)

    def _reference_file(self, path: Path):
        Image.new("RGB", (510, 510), (90, 60, 35)).save(path, format="PNG")
        return path

    def test_prompt_contains_shared_style_and_scene_direction(self):
        prompt = generator.build_prompt(self.plan["scenes"][0], 1)
        self.assertIn("adult woman in her early twenties", prompt)
        self.assertIn("vast stone chamber piled high", prompt)
        self.assertIn("No lettering", prompt)
        self.assertLessEqual(len(prompt), 2048)

    def test_rumpelstiltskin_prompt_uses_identity_and_separate_scale_reference(self):
        prompt = generator.build_prompt(self.plan["scenes"][1], 5)
        for required in (
            "input image 0 is Rumpelstiltskin",
            "input image 1",
            "input image 1 shows ONLY the height scale",
            "60-centimetre",
            "reaches no higher than her KNEECAP",
            "adult woman",
            "white beard",
            "fiery-red nose",
            "pointed red cap",
            "blue tunic",
            "ochre breeches",
            "NOT a normal-sized man",
        ):
            self.assertIn(required.lower(), prompt.lower())
        self.assertIn("There is NO child in this scene", prompt)
        self.assertIn("Dark medieval European fairytale", prompt)
        self.assertIn("knee joint", prompt)
        self.assertNotIn("hip", prompt.lower())
        self.assertLessEqual(len(prompt), 2048)

    def test_forest_scene_requests_exactly_one_character(self):
        prompt = generator.build_prompt(self.plan["scenes"][2], 8)
        self.assertIn("Show exactly ONE person", prompt)
        self.assertIn("no second dwarf", prompt)

    def test_reference_lock_is_only_added_to_scenes_where_he_appears(self):
        queen_scene = {"id": "beat-07-three-days", "title": "Three days to name him"}
        prompt = generator.build_prompt(queen_scene, 7)
        self.assertNotIn("input image 0", prompt)
        self.assertNotIn("input image 1", prompt)
        self.assertNotIn("60-centimetre", prompt)

    def test_every_character_appearance_uses_both_reference_constraints(self):
        expected = {
            "beat-03-first-bargains",
            "beat-05-child-pact",
            "beat-06-claim",
            "beat-08-forest-fire",
            "beat-09-foot-revelation",
        }
        self.assertEqual(generator.RUMPELSTILTSKIN_SCENES, expected)
        for index, scene_id in enumerate(sorted(expected), 1):
            prompt = generator.build_prompt({"id": scene_id, "title": scene_id}, index)
            self.assertIn("input image 0 is Rumpelstiltskin", prompt)
            self.assertIn("input image 1", prompt.lower())
            self.assertIn("60-centimetre", prompt)
            self.assertLessEqual(len(prompt), 2048)


    def test_all_nine_episode_prompts_fit_the_model_input_limit(self):
        plan = json.loads((Path(__file__).resolve().parents[1] / "research" / "rumpelstiltskin-pilot.json").read_text(encoding="utf-8"))
        for index, scene in enumerate(plan["scenes"], 1):
            prompt = generator.build_prompt(scene, index)
            self.assertLessEqual(len(prompt), 2048, scene["id"])
            if scene["id"] in generator.RUMPELSTILTSKIN_SCENES:
                self.assertIn("input image 0 is Rumpelstiltskin", prompt)
                self.assertIn("input image 1", prompt.lower())
                self.assertIn("KNEECAP", prompt)

    def test_generic_fairytale_prompt_uses_character_bible_without_rumpel_lock(self):
        plan = self._generic_plan()
        prompt = generator.build_episode_prompt(plan, plan["scenes"][0], 1)
        self.assertIn("Mira", prompt)
        self.assertIn("green cloak", prompt)
        self.assertIn("Cinematic painterly storybook", prompt)
        self.assertNotIn("Rumpelstiltskin", prompt)
        self.assertIn("repeated character copies", prompt)
        self.assertLessEqual(len(prompt), 2048)

    def test_generic_episode_scene_generation_needs_no_story_specific_references(self):
        plan = self._generic_plan()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan_path = root / "episode.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            output = root / "images"
            original = generator.generate_one
            generate_mock = Mock(return_value=self.image_bytes)
            generator.generate_one = generate_mock
            try:
                manifest = generator.run(
                    plan_path, output, "account", "token",
                    only_scene="scene-01", pause_seconds=0,
                )
            finally:
                generator.generate_one = original
            self.assertEqual(manifest["scene_count"], 1)
            item = manifest["images"][0]
            self.assertEqual(item["model"], generator.MODEL_ID)
            self.assertFalse(item["uses_character_reference"])
            self.assertFalse(item["uses_scale_reference"])
            self.assertFalse(item["model_fallback_used"])
            self.assertEqual(generate_mock.call_count, 1)
            self.assertIsNone(manifest["reference_image_sha256"])
            self.assertTrue((output / "generation-manifest.json").is_file())

    def test_unknown_scene_fails_closed(self):
        with self.assertRaises(ValueError):
            generator.build_prompt({"id": "unknown-beat"}, 1)

    def test_cloudflare_request_uses_two_reference_multipart_images(self):
        response = Mock(status_code=200)
        response.json.return_value = {"result": {"image": self.image_b64}}
        session = Mock()
        session.post.return_value = response
        identity = b"identity-image-png"
        scale = b"scale-image-png"
        data = generator.generate_one(
            "a fairytale scene", "account", "token",
            reference_image=identity,
            scale_reference_image=scale,
            model_id=generator.RUMPELSTILTSKIN_MODEL_ID,
            width=832, height=1472, session=session
        )
        self.assertTrue(data.startswith(b"\xff\xd8"))
        session.post.assert_called_once()
        args, kwargs = session.post.call_args
        self.assertIn("/ai/run/@cf/black-forest-labs/flux-2-klein-9b", args[0])
        file_fields = {name: value for name, value in kwargs["files"]}
        self.assertEqual(file_fields["prompt"], (None, "a fairytale scene"))
        self.assertEqual(file_fields["width"], (None, "832"))
        self.assertEqual(file_fields["height"], (None, "1472"))
        self.assertEqual(file_fields["input_image_0"][1], identity)
        self.assertEqual(file_fields["input_image_0"][2], "image/png")
        self.assertEqual(file_fields["input_image_1"][1], scale)
        self.assertEqual(file_fields["input_image_1"][2], "image/png")
        self.assertNotIn("seed", kwargs)

    def test_transient_cloudflare_500_is_retried(self):
        first = Mock(status_code=500, text='temporary internal error', headers={})
        second = Mock(status_code=200)
        second.json.return_value = {"result": {"image": self.image_b64}}
        session = Mock()
        session.post.side_effect = [first, second]
        with patch.object(generator.time, "sleep") as sleep:
            data = generator.generate_one("fairytale scene", "account", "token", session=session)
        self.assertTrue(data.startswith(b"\xff\xd8"))
        self.assertEqual(session.post.call_count, 2)
        sleep.assert_called_once()

    def test_resume_reuses_existing_scene_and_generates_only_missing_beats(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan_path = root / "plan.json"
            plan = {"title": "Resume test", "scenes": [
                {"id": "beat-01-threat", "title": "Threat", "narration": "The threat."},
                {"id": "beat-05-child-pact", "title": "Bargain", "narration": "She makes a bargain."},
                {"id": "beat-08-forest-fire", "title": "Fire", "narration": "He dances by the fire."},
            ]}
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            out = root / "images"
            out.mkdir()
            partial_path = out / "01-beat-01-threat.jpg"
            Image.new("RGB", (832, 1472), (22, 45, 80)).save(partial_path, format="JPEG")
            identity = self._reference_file(root / "identity.png")
            scale = self._reference_file(root / "scale.png")
            generated_buffer = io.BytesIO()
            Image.new("RGB", (832, 1472), (60, 80, 100)).save(generated_buffer, format="JPEG")
            generated = generated_buffer.getvalue()
            original = generator.generate_one
            generate_mock = Mock(return_value=generated)
            generator.generate_one = generate_mock
            try:
                manifest = generator.run(
                    plan_path, out, "account", "token",
                    reference_image_path=identity,
                    scale_reference_path=scale,
                    pause_seconds=0,
                    resume=True,
                )
            finally:
                generator.generate_one = original
            self.assertEqual(generate_mock.call_count, 2)
            self.assertEqual(manifest["scene_count"], 3)
            self.assertTrue(manifest["images"][0]["resumed_from_partial_run"])
            self.assertFalse(manifest["images"][1]["resumed_from_partial_run"])
            self.assertFalse(manifest["images"][2]["resumed_from_partial_run"])
            self.assertEqual(manifest["images"][0]["file"], partial_path.name)
            self.assertTrue((out / "generation-manifest.json").is_file())
            self.assertEqual(len(list(out.glob("*.jpg"))), 3)

    def test_character_scene_falls_back_to_4b_after_exhausted_9b_transient_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps({"title": "Fallback test", "scenes": [
                self.plan["scenes"][1]
            ]}), encoding="utf-8")
            identity = self._reference_file(root / "identity.png")
            scale = self._reference_file(root / "scale.png")
            original = generator.generate_one
            generate_mock = Mock(side_effect=[
                RuntimeError("Cloudflare Workers AI transient HTTP 500 persisted after 4 attempts"),
                self.image_bytes,
            ])
            generator.generate_one = generate_mock
            try:
                manifest = generator.run(
                    plan_path, root / "images", "account", "token",
                    reference_image_path=identity,
                    scale_reference_path=scale,
                    only_scene="beat-05-child-pact",
                    pause_seconds=0,
                )
            finally:
                generator.generate_one = original
            self.assertEqual(generate_mock.call_count, 2)
            self.assertEqual(generate_mock.call_args_list[0].kwargs["model_id"], generator.RUMPELSTILTSKIN_MODEL_ID)
            self.assertEqual(generate_mock.call_args_list[1].kwargs["model_id"], generator.MODEL_ID)
            item = manifest["images"][0]
            self.assertEqual(item["preferred_model"], generator.RUMPELSTILTSKIN_MODEL_ID)
            self.assertEqual(item["model"], generator.MODEL_ID)
            self.assertTrue(item["model_fallback_used"])
            self.assertTrue(item["uses_character_reference"])
            self.assertTrue(item["uses_scale_reference"])

    def test_http_errors_are_clear(self):
        response = Mock(status_code=429, text="rate limited")
        session = Mock()
        session.post.return_value = response
        with patch.object(generator.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "rate limit"):
                generator.generate_one("prompt", "account", "token", session=session)
        self.assertEqual(session.post.call_count, generator.TRANSIENT_RETRIES + 1)
        self.assertEqual(sleep.call_count, generator.TRANSIENT_RETRIES)

    def test_rumpel_scene_requires_two_valid_small_visual_references(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps({"scenes": [self.plan["scenes"][1]]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "visual identity reference"):
                generator.run(
                    plan_path, root / "out", "account", "token",
                    only_scene="beat-05-child-pact"
                )

            identity_path = self._reference_file(root / "identity.png")
            with self.assertRaisesRegex(ValueError, "height-scale reference"):
                generator.run(
                    plan_path, root / "out", "account", "token",
                    reference_image_path=identity_path,
                    only_scene="beat-05-child-pact"
                )

            oversized = root / "large.png"
            Image.new("RGB", (512, 512)).save(oversized)
            with self.assertRaisesRegex(ValueError, "strictly smaller than 512x512"):
                generator.run(
                    plan_path, root / "out", "account", "token",
                    reference_image_path=oversized,
                    scale_reference_path=identity_path,
                    only_scene="beat-05-child-pact"
                )

    def test_run_writes_reference_conditioned_image_and_manifest(self):
        response = Mock(status_code=200)
        response.json.return_value = {"result": {"image": self.image_b64}}
        session = Mock()
        session.post.return_value = response
        original = generator.generate_one
        generator.generate_one = lambda prompt, account_id, api_token, reference_image=None, scale_reference_image=None, model_id=generator.MODEL_ID, width=832, height=1472, guidance=4.0: original(
            prompt, account_id, api_token, reference_image, scale_reference_image,
            model_id, width, height, guidance, session=session
        )
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                plan_path = root / "plan.json"
                plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
                identity_path = self._reference_file(root / "identity.png")
                scale_path = self._reference_file(root / "scale.png")
                out = root / "images"
                manifest = generator.run(
                    plan_path, out, "account", "token",
                    reference_image_path=identity_path,
                    scale_reference_path=scale_path,
                    only_scene="beat-05-child-pact",
                    pause_seconds=0,
                )
                self.assertEqual(manifest["scene_count"], 1)
                self.assertEqual(manifest["model_strategy"]["character_scenes"], "@cf/black-forest-labs/flux-2-klein-9b")
                self.assertEqual(manifest["model_strategy"]["other_scenes"], "@cf/black-forest-labs/flux-2-klein-4b")
                self.assertFalse(manifest["approved"])
                self.assertFalse(manifest["rendered"])
                self.assertTrue((out / "01-beat-05-child-pact.jpg").is_file())
                saved = json.loads((out / "generation-manifest.json").read_text())
                self.assertEqual(saved["scene_count"], 1)
                self.assertEqual(saved["images"][0]["model"], "@cf/black-forest-labs/flux-2-klein-9b")
                self.assertTrue(saved["images"][0]["uses_character_reference"])
                self.assertTrue(saved["images"][0]["uses_scale_reference"])
                self.assertFalse(saved["images"][0]["approved"])
        finally:
            generator.generate_one = original


if __name__ == "__main__":
    unittest.main()
