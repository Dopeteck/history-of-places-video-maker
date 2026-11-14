import hashlib
import json
import tempfile
import unittest
import wave
from pathlib import Path

from PIL import Image

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "destination-prototype"))
from render_fairytale_video import load_inputs, make_contact_sheet, make_ambient, _audio_mix_filter, _scene_entries, _story_word_timings_by_scene, _build_story_text, _build_srt, write_continuous_narration
from fairytale_planner import validate_episode_plan


class FairytaleVideoInputTests(unittest.TestCase):
    def setUp(self):
        self.plan_path = ROOT / "research" / "rumpelstiltskin-pilot.json"
        self.plan = json.loads(self.plan_path.read_text(encoding="utf-8"))

    def _create_generated_set(self, folder: Path):
        rows = []
        for index, scene in enumerate(self.plan["scenes"]):
            filename = f"{index + 1:02d}-{scene['id']}.jpg"
            path = folder / filename
            image = Image.new("RGB", (832, 1472), ((index * 23) % 255, 70, 90))
            image.save(path, quality=88)
            rows.append({
                "scene_id": scene["id"],
                "file": filename,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "approved": False,
            })
        manifest = {
            "scene_count": len(rows),
            "status": "generated_unapproved_review_only",
            "approved": False,
            "published": False,
            "images": rows,
            "model_strategy": {
                "character_scenes": "@cf/black-forest-labs/flux-2-klein-9b",
                "other_scenes": "@cf/black-forest-labs/flux-2-klein-4b",
            },
        }
        (folder / "generation-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    def test_video_renderer_has_no_chapter_banner_overlay(self):
        import render_fairytale_video as renderer
        self.assertFalse(hasattr(renderer, "_chapter_frame"))
        self.assertFalse(hasattr(renderer, "_intro_frame"))
        self.assertFalse(hasattr(renderer, "INTRO_SECONDS"))

    def test_story_plan_runs_in_chronological_order_with_a_clear_name_reveal(self):
        plan = json.loads((ROOT / "research" / "rumpelstiltskin-pilot.json").read_text(encoding="utf-8"))
        ids = [scene["id"] for scene in plan["scenes"]]
        self.assertEqual(ids, [
            "beat-02-boast",
            "beat-01-threat",
            "beat-03-first-bargains",
            "beat-04-king-demands",
            "beat-05-child-pact",
            "beat-06-claim",
            "beat-07-three-days",
            "beat-08-forest-fire",
            "beat-09-foot-revelation",
        ])
        self.assertIn("Rumpelstiltskin!", plan["scenes"][-1]["narration"])
        self.assertEqual(plan["narration"], " ".join(scene["narration"] for scene in plan["scenes"]))
        self.assertTrue(150 <= len(plan["narration"].split()) <= 235)
        self.assertIn("A miller wanted to impress the king", plan["narration"])
        self.assertIn("That boast would put her life in danger", plan["narration"])
        self.assertIn("For two days", plan["narration"])
        self.assertIn("But the king wanted more", plan["narration"])
        self.assertIn("“No,” he said", plan["narration"])
        self.assertIn("On the third day", plan["narration"])
        self.assertIn("forest's edge", plan["narration"])
        self.assertIn("wrench it free", plan["narration"])

    def test_story_text_uses_real_paragraph_breaks_and_preserves_all_words(self):
        story_text = _build_story_text(self.plan["scenes"])
        expected_words = sum(len(scene["narration"].split()) for scene in self.plan["scenes"])
        self.assertEqual(len(story_text.split()), expected_words)
        self.assertIn("\n\n", story_text)
        self.assertNotIn("\\n", story_text)
        self.assertEqual(story_text, "\n\n".join(scene["narration"].strip() for scene in self.plan["scenes"]))

    def test_srt_sidecar_uses_real_line_breaks(self):
        events = [{
            "start": 0.0,
            "words": [
                {"word": "Once", "start": 0.0, "end": 0.3},
                {"word": "upon", "start": 0.3, "end": 0.6},
                {"word": "a", "start": 0.6, "end": 0.7},
                {"word": "time.", "start": 0.7, "end": 1.0},
            ],
        }]
        srt = _build_srt(events)
        self.assertIn("1\n00:00:00,000 --> 00:00:01,000\nOnce upon a time.", srt)
        self.assertNotIn("\\n", srt)
        self.assertTrue(srt.endswith("\n"))

    def test_authored_script_timing_preserves_every_word(self):
        script_words = [token for scene in self.plan["scenes"] for token in scene["narration"].split()]
        words = _story_word_timings_by_scene(self.plan["scenes"], 90.0)
        self.assertEqual(len(words), len(script_words))
        self.assertEqual([row["word"] for row in words], script_words)
        self.assertAlmostEqual(words[0]["start"], 0.0)
        self.assertAlmostEqual(words[-1]["end"], 90.0)

    def test_scene_boundaries_cover_all_authored_words_without_omissions(self):
        words = _story_word_timings_by_scene(self.plan["scenes"], 90.0)
        entries = _scene_entries(self.plan["scenes"], words, 0.0)
        self.assertEqual(len(entries), len(self.plan["scenes"]))
        self.assertAlmostEqual(entries[0]["start"], 0.0)
        self.assertAlmostEqual(entries[-1]["start"] + entries[-1]["duration"], 90.0, places=2)

    def test_scene_entries_split_one_continuous_story_track(self):
        words = _story_word_timings_by_scene(self.plan["scenes"], 90.0)
        entries = _scene_entries(self.plan["scenes"], words, 0.0)
        self.assertEqual(len(entries), 9)
        self.assertAlmostEqual(entries[0]["start"], 0.0, places=3)
        final_end = entries[-1]["start"] + entries[-1]["duration"]
        self.assertAlmostEqual(final_end, 90.0, places=2)
        for previous, current in zip(entries, entries[1:]):
            self.assertAlmostEqual(
                current["start"],
                previous["start"] + previous["duration"],
                places=3,
            )

    def test_scene_timing_keeps_all_authored_words_attached_to_their_beat(self):
        words = _story_word_timings_by_scene(self.plan["scenes"], 92.0)
        self.assertEqual(len(words), sum(len(scene["narration"].split()) for scene in self.plan["scenes"]))
        self.assertEqual(
            [word["scene_id"] for word in words if word["scene_id"] != words[0]["scene_id"]][0],
            self.plan["scenes"][1]["id"],
        )
        entries = _scene_entries(self.plan["scenes"], words, 0.0)
        self.assertEqual(len(entries), 9)
        self.assertAlmostEqual(entries[0]["start"], 0.0, places=3)
        self.assertAlmostEqual(entries[-1]["start"] + entries[-1]["duration"], 92.0, places=2)

    def test_continuous_narration_starts_immediately_without_title_card_silence(self):
        from captions import word_timings
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "speech.wav"
            destination = root / "continuous.wav"
            with wave.open(str(source), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(24000)
                audio.writeframes(b"\x10\x00" * 24000)
            text = "Once a miller made a foolish promise."
            row = {
                "path": str(source),
                "duration": 1.0,
                "words": word_timings(text, 1.0),
            }
            events = write_continuous_narration(destination, row, 1.0)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["start"], 0.0)
            self.assertEqual(len(events[0]["words"]), len(text.split()))
            with wave.open(str(destination), "rb") as audio:
                self.assertEqual(audio.getnframes(), round(1.0 * 24000))
                self.assertEqual(audio.getframerate(), 24000)
                self.assertEqual(audio.readframes(1), b"\x10\x00")

    def test_ffmpeg_mix_reads_narration_and_ambient_from_correct_inputs(self):
        mix = _audio_mix_filter()
        self.assertIn("[1:a]volume=1.0[narr]", mix)
        self.assertIn("[2:a]volume=0.22[bed]", mix)
        self.assertNotIn("[0:a]", mix)

    def test_ambient_audio_fade_uses_integer_slice_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ambient.wav"
            make_ambient(path, 0.8)
            with wave.open(str(path), "rb") as audio:
                self.assertEqual(audio.getnchannels(), 1)
                self.assertEqual(audio.getsampwidth(), 2)
                self.assertGreater(audio.getframerate(), 0)
                self.assertGreater(audio.getnframes(), 0)

    def test_renderer_loads_a_different_fairytale_without_rumpelstiltskin_schema(self):
        story = {
            "id": "test-tale", "title": "The Test Tale",
            "synopsis": "A brave heroine faces a forest danger and returns safely.",
            "key_beats": ["She leaves", "A helper appears", "She faces danger", "She returns", "The village is safe"],
            "source": {"title": "Test Collection", "author": "Traditional",
                       "url": "https://www.gutenberg.org/ebooks/11027",
                       "edition_note": "Test edition."},
        }
        characters = [
            {"id": "hero", "name": "Mira", "role": "heroine",
             "visual_identity": "Young woman with a green cloak and long black braid."},
            {"id": "helper", "name": "Rowan", "role": "helper",
             "visual_identity": "Elderly traveller with a grey beard and brown coat."},
        ]
        narrations = [
            "Mira left her village before sunrise, carrying one small bag and a promise she hoped she could keep. She had never travelled alone.",
            "At the forest edge she heard a voice calling her name from behind an old stone wall. Someone seemed to know her, but no one was there.",
            "An elderly traveller warned her that a dangerous creature guarded the narrow bridge ahead, especially after sunset when the last daylight disappeared behind the hills.",
            "Mira listened carefully and noticed a narrow hidden path beneath the tangled roots of an ancient tree. It seemed to lead towards the bridge's far side.",
            "She followed the path, but the creature heard her footsteps and blocked the bridge with a roar. There was nowhere else to cross safely.",
            "The traveller returned and showed her how to ring the bell hanging from the wooden bridge post. Its strange clear sound would frighten the creature.",
            "The sound startled the creature long enough for Mira to cross and reach the far side safely. She thanked the traveller and hurried towards home.",
            "When she returned home, Mira kept her promise and told the village how the traveller had helped her. The journey had changed her forever.",
        ]
        raw = {
            "title": "The Hidden Path",
            "logline": "A brave young woman finds a way through a dangerous forest.",
            "characters": characters,
            "scenes": [
                {"title": f"Scene {i}", "narration": line,
                 "visual_prompt": "Cinematic painterly storybook illustration showing the named characters in a dark forest beside an ancient stone bridge, with one clear visible action and dramatic moonlight.",
                 "character_ids": ["hero"] if i not in {3, 6} else ["hero", "helper"]}
                for i, line in enumerate(narrations, 1)
            ],
        }
        plan = validate_episode_plan(raw, story)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = root / "episode.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            images_dir = root / "images"
            images_dir.mkdir()
            rows = []
            for index, scene in enumerate(plan["scenes"], 1):
                filename = f"{index:02d}-{scene['id']}.jpg"
                image_path = images_dir / filename
                Image.new("RGB", (832, 1472), (index * 15, 65, 90)).save(image_path, quality=86)
                rows.append({
                    "scene_id": scene["id"], "file": filename,
                    "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                    "model": "@cf/black-forest-labs/flux-2-klein-4b",
                })
            manifest = {
                "story": plan["title"], "story_id": plan["story_id"],
                "scene_count": 8, "status": "generated_unapproved_review_only",
                "approved": False, "published": False,
                "images": rows, "model_strategy": {"all_scenes": "@cf/black-forest-labs/flux-2-klein-4b"},
            }
            (images_dir / "generation-manifest.json").write_text(json.dumps(manifest))
            loaded, saved_manifest, saved_rows, images = load_inputs(plan_path, images_dir)
            self.assertEqual(loaded["format"], "dark_fairytale_episode_v1")
            self.assertEqual(loaded["story_id"], "test-tale")
            self.assertEqual(len(images), 8)
            self.assertEqual(saved_manifest["story_id"], "test-tale")
            self.assertFalse(saved_manifest["approved"])

    def test_requires_all_nine_generated_images_and_checksums(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self._create_generated_set(folder)
            plan, manifest, rows, images = load_inputs(self.plan_path, folder)
            self.assertEqual(len(plan["scenes"]), 9)
            self.assertEqual(manifest["scene_count"], 9)
            self.assertEqual(len(rows), 9)
            self.assertEqual(len(images), 9)
            self.assertTrue(all(image.size == (832, 1472) for image in images))
            self.assertFalse(manifest["approved"])
            self.assertFalse(manifest["published"])

    def test_rejects_incomplete_image_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self._create_generated_set(folder)
            manifest = json.loads((folder / "generation-manifest.json").read_text())
            first_row = manifest["images"][0]
            (folder / first_row["file"]).unlink()
            with self.assertRaisesRegex(ValueError, "Missing generated image"):
                load_inputs(self.plan_path, folder)

    def test_rejects_approved_or_published_artifact_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self._create_generated_set(folder)
            manifest_path = folder / "generation-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["approved"] = True
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "explicitly unapproved"):
                load_inputs(self.plan_path, folder)

    def test_rejects_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self._create_generated_set(folder)
            manifest = json.loads((folder / "generation-manifest.json").read_text())
            image_path = folder / manifest["images"][0]["file"]
            image_path.write_bytes(b"not-the-generated-image")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                load_inputs(self.plan_path, folder)

    def test_contact_sheet_contains_all_nine_beats(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self._create_generated_set(folder)
            plan, _, _, images = load_inputs(self.plan_path, folder)
            output = folder / "contact-sheet.jpg"
            make_contact_sheet(plan, images, output)
            self.assertTrue(output.is_file())
            with Image.open(output) as sheet:
                self.assertEqual(sheet.size, (720, 1278))


if __name__ == "__main__":
    unittest.main()
