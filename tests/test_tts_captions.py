"""Offline contracts for narration WAVs and caption timing/formatting."""
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from captions import build_ass, word_timings
from tts import Narrator, RATE, duration, write_wav


class NarrationContractTests(unittest.TestCase):
    def test_written_wav_is_valid_mono_24khz_pcm_and_duration_is_measured(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "voice.wav"
            samples = np.sin(np.arange(RATE // 2) * (2 * np.pi * 220 / RATE))
            write_wav(path, samples)
            self.assertAlmostEqual(duration(path), 0.5, places=3)
            with wave.open(str(path), "rb") as audio:
                self.assertEqual(audio.getnchannels(), 1)
                self.assertEqual(audio.getsampwidth(), 2)
                self.assertEqual(audio.getframerate(), RATE)
                self.assertEqual(audio.getnframes(), RATE // 2)

    def test_invalid_or_empty_audio_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "empty or invalid"):
                write_wav(Path(tmp) / "empty.wav", np.array([], dtype=np.float32))
            with self.assertRaisesRegex(ValueError, "Unknown narrator"):
                Narrator("not_a_voice")
            with self.assertRaisesRegex(ValueError, "Speed"):
                Narrator("bm_george", speed=2.0)

    def test_word_timings_are_monotonic_and_cover_measured_duration(self):
        rows = word_timings("A short narration, with punctuation.", 3.25)
        self.assertGreater(len(rows), 3)
        self.assertAlmostEqual(rows[0]["start"], 0.0)
        self.assertAlmostEqual(rows[-1]["end"], 3.25)
        for previous, current in zip(rows, rows[1:]):
            self.assertAlmostEqual(previous["end"], current["start"])
        self.assertTrue(all(row["end"] > row["start"] for row in rows))

    def test_ass_caption_file_has_timed_events_and_escapes_braces(self):
        words = word_timings("Text with {unsafe} braces.", 2.0)
        ass = build_ass([{"start": 1.8, "words": words}], (540, 960), karaoke=True)
        self.assertIn("[Events]", ass)
        self.assertIn("Dialogue: 0,", ass)
        self.assertIn(r"{\\k", ass)
        self.assertIn("(unsafe)", ass)
        self.assertNotIn("{unsafe}", ass)


if __name__ == "__main__":
    unittest.main()
