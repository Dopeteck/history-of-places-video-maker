"""Renderer integration tests use local art and synthetic tone audio only."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scene_cards_render import render_scene_cards
from scene_planner import make_review_template
from story import digest


def reviewed_plan():
    sources = [{"id": "source_1", "title": "Test source",
                "url": "https://example.org/evidence", "excerpt": "A test excerpt."}]
    scenes = []
    for i in range(1, 7):
        scenes.append({
            "id": f"scene_{i:02d}", "kind": ["illustration", "statistic", "comparison",
                                                "timeline", "steps", "verdict"][i - 1],
            "title": f"Scene {i}", "narration": f"Scene {i} gives a useful test explanation.",
            "visual_prompt": f"Editorial illustration of people and a chart, scene {i}, no text.",
            "background": "ivory", "source_ids": ["source_1"],
            "labels": [{"label": "Example", "value": str(i)}],
            "claims": [], "uncertainty": "documented",
            "transition": "crossfade" if i < 6 else "cut",
        })
    plan = {"schema_version": 1, "title": "Renderer smoke test",
            "hook": "Keep this hook visible in every scene", "scenes": scenes,
            "source_ids": ["source_1"], "status": "needs_human_review",
            "topic": "test", "sources": sources,
            "review": {"status": "needs_human_review", "warning": "test", "claims": []}}
    review = make_review_template(plan)
    review.update({"reviewer": "CI reviewer", "full_script_reviewed": True,
                   "sources_checked": ["source_1"]})
    return plan, review


class SceneCardRenderTests(unittest.TestCase):
    def test_unreviewed_plan_is_rejected(self):
        plan, review = reviewed_plan()
        review["full_script_reviewed"] = False
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "complete narration"):
                render_scene_cards(plan, review, Path(tmp) / "out", preview=True, smoke=True)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg/ffprobe required for end-to-end smoke render")
    def test_smoke_render_creates_real_video_and_metadata(self):
        plan, review = reviewed_plan()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out"
            video = render_scene_cards(plan, review, output, mode="long",
                                       preview=True, captions=False, smoke=True)
            self.assertTrue(video.is_file())
            self.assertGreater(video.stat().st_size, 10_000)
            metadata = json.loads((output / "render-metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["plan_sha256"], digest(plan))
            self.assertTrue(metadata["approval_required"])
            self.assertEqual(metadata["voice"], "synthetic tone; no speech")
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
                check=True, capture_output=True, text=True)
            self.assertGreater(float(probe.stdout.strip()), 10.0)


if __name__ == "__main__":
    unittest.main()
