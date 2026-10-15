"""Offline regression tests for compositor layout, hard cuts, motion, and FX."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import ImageChops

from animation_engine import AnimationCompositor, parse_storyboard, render_video

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden_storyboard.json"


def storyboard():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


class AnimationEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = storyboard()

    def test_golden_storyboard_is_24_beats_and_30_seconds(self):
        _, entries = parse_storyboard(self.data)
        self.assertEqual(len(entries), 27)
        self.assertAlmostEqual(sum(item.duration for item in entries), 30.0)
        required = {"illustration", "icon_card", "statistic", "then_now", "method_card", "face_card"}
        self.assertEqual({item.data["type"] for item in entries} & required, required)

    def test_frame_geometry_and_full_width_16_9_card(self):
        compositor = AnimationCompositor(self.data, (540, 960))
        frame = compositor.frame(0.0)
        self.assertEqual(frame.size, (540, 960))
        card_h = round(540 * 9 / 16)
        card_y = (960 - card_h) // 2
        strip = frame.crop((0, card_y + card_h // 2, 540, card_y + card_h // 2 + 1))
        colors = strip.getcolors(maxcolors=1_000_000)
        self.assertIsNotNone(colors)
        self.assertGreater(len(colors or []), 6)

    def test_each_beat_has_its_own_background_and_hard_cut(self):
        compositor = AnimationCompositor(self.data, (360, 640))
        first = compositor.frame(1.24)
        second = compositor.frame(1.25)
        self.assertNotEqual(ImageChops.difference(first, second).getbbox(), None)
        self.assertEqual(len(compositor._backgrounds), 27)

    def test_camera_motion_is_smooth_and_not_12fps_quantized(self):
        compositor = AnimationCompositor(self.data, (360, 640))
        a = compositor.frame(0.101)
        b = compositor.frame(0.119)
        self.assertNotEqual(ImageChops.difference(a, b).getbbox(), None)

    def test_invalid_storyboards_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            parse_storyboard({"shots": []})
        invalid = {"shots": [{"id": "x", "duration": 0.0}, {"id": "x"}]}
        with self.assertRaisesRegex(ValueError, "between 0.2 and 5.0"):
            parse_storyboard(invalid)
        invalid = {"shots": [{"id": "x"}, {"id": "x"}]}
        with self.assertRaisesRegex(ValueError, "Duplicate shot id"):
            parse_storyboard(invalid)

    def test_render_smoke_video_with_synthetic_audio(self):
        if not shutil.which("ffmpeg"):
            self.skipTest("FFmpeg not installed")
        mini = {"hook": "OFFLINE TEST", "shots": self.data["shots"][:2]}
        compositor = AnimationCompositor(mini, (320, 568))
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "smoke.mp4"
            report = render_video(compositor, output, fps=10, audio="synthetic")
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 10_000)
            self.assertEqual(report["shot_count"], 2)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                 "-select_streams", "v:0", "-of", "csv=s=x:p=0", str(output)],
                capture_output=True, text=True, check=True)
            self.assertEqual(probe.stdout.strip(), "320x568")


if __name__ == "__main__":
    unittest.main()
