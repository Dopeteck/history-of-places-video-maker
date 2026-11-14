#!/usr/bin/env python3
"""Regression test for Kokoro English TTS pipeline initialization.
Ensures spaCy tokenizer compatibility with pinned en_core_web_sm model."""
import sys

def test_kokoro_english_pipeline_initializes():
    """Verify Kokoro English voice pipeline initializes and produces audio."""
    from kokoro import KPipeline
    pipeline = KPipeline(lang_code="a")
    graphemes, phonemes, audio = next(pipeline("Tokyo's history begins with Edo.", voice="af_heart"))
    assert audio is not None, "Kokoro returned None audio"
    assert audio.shape[0] > 0, "Kokoro returned empty audio"
    # Kokoro v0.9.4 uses 24kHz sample rate
    assert audio.shape[0] > 2400, "Audio too short for a sentence (expected >100ms)"
    print(f"PASS: Kokoro English TTS produces {audio.shape[0]} samples at 24000 Hz")

if __name__ == "__main__":
    test_kokoro_english_pipeline_initializes()
    print("ALL KOKORO INIT TESTS PASSED")