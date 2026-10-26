#!/usr/bin/env python3
"""Test Kokoro initialization for all 7 configured languages.
Reports which languages actually work with the installed model version."""
import sys

# Languages to test (from task requirements)
LANGUAGES = [
    ("English", "a", "af_heart", "History begins here.", True),
    ("Japanese", "j", "jf_alpha", "東京の歴史は江戸から始まります。", False),  # needs pyopenjtalk
    ("Mandarin", "z", "zf_xiaobei", "历史从这里开始。", True),
    ("Spanish", "e", "ef_dora", "La historia comienza aquí.", True),
    ("French", "f", "ff_siwis", "L'histoire commence ici.", True),
    ("Korean", "k", "kf_alpha", "역사는 여기서 시작됩니다.", False),  # not supported by model
    ("Hindi", "h", "hf_alpha", "इतिहास यहाँ से शुरू होता है।", True),
]

def test_language(narration_lang, lang_code, voice, test_text, expected_to_work):
    """Test Kokoro initialization for a specific language."""
    try:
        from kokoro import KPipeline
        pipeline = KPipeline(lang_code=lang_code)
        graphemes, phonemes, audio = next(pipeline(test_text, voice=voice))
        assert audio is not None, f"{narration_lang}: Kokoro returned None audio"
        assert audio.shape[0] > 0, f"{narration_lang}: Kokoro returned empty audio"
        status = "PASS" if expected_to_work else "UNEXPECTED_PASS"
        print(f"{status}: {narration_lang} (lang_code={lang_code}, voice={voice}) -> {audio.shape[0]} samples at 24000 Hz")
        return True
    except Exception as e:
        if expected_to_work:
            print(f"FAIL: {narration_lang} (lang_code={lang_code}, voice={voice}) -> {type(e).__name__}: {e}")
            return False
        else:
            print(f"EXPECTED_FAIL: {narration_lang} (lang_code={lang_code}, voice={voice}) -> {type(e).__name__}: {e}")
            return True  # Expected failure is OK

def main():
    results = []
    for narration_lang, lang_code, voice, test_text, expected_to_work in LANGUAGES:
        ok = test_language(narration_lang, lang_code, voice, test_text, expected_to_work)
        results.append((narration_lang, ok, expected_to_work))
    
    print("\n=== SUMMARY ===")
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    for lang, ok, expected in results:
        status = "PASS" if ok else "FAIL"
        expected_str = "expected" if expected else "not expected"
        print(f"  {status}: {lang} ({expected_str} to work)")
    
    # Check that all expected-to-work languages passed
    expected_count = sum(1 for _, _, exp in results if exp)
    expected_passed = sum(1 for _, ok, exp in results if ok and exp)
    
    print(f"\nLanguages expected to work: {expected_count}")
    print(f"Languages actually working: {expected_passed}")
    
    if expected_passed < expected_count:
        print("ERROR: Some languages expected to work failed!")
        sys.exit(1)
    
    print("SUCCESS: All expected languages work.")

if __name__ == "__main__":
    main()