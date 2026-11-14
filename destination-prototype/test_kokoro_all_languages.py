#!/usr/bin/env python3
"""Test Kokoro initialization for all 7 supported languages."""
import sys

LANGUAGES = [
    ("English", "a", "af_heart", "History begins here."),
    ("Japanese", "j", "jf_alpha", "東京の歴史は江戸から始まります。"),
    ("Mandarin", "z", "zf_xiaobei", "历史从这里开始。"),
    ("Spanish", "e", "ef_dora", "La historia comienza aquí."),
    ("French", "f", "ff_siwis", "L'histoire commence ici."),
    ("Korean", "k", "kf_alpha", "역사는 여기서 시작됩니다."),
    ("Hindi", "h", "hf_alpha", "इतिहास यहाँ से शुरू होता है।"),
]

def test_language(narration_lang, lang_code, voice, test_text):
    """Test Kokoro initialization for a specific language."""
    from kokoro import KPipeline
    pipeline = KPipeline(lang_code=lang_code)
    graphemes, phonemes, audio = next(pipeline(test_text, voice=voice))
    assert audio is not None, f"{narration_lang}: Kokoro returned None audio"
    assert audio.shape[0] > 0, f"{narration_lang}: Kokoro returned empty audio"
    print(f"PASS: {narration_lang} (lang_code={lang_code}, voice={voice}) -> {audio.shape[0]} samples at 24000 Hz")
    return True

def main():
    results = []
    for narration_lang, lang_code, voice, test_text in LANGUAGES:
        try:
            test_language(narration_lang, lang_code, voice, test_text)
            results.append((narration_lang, True, None))
        except Exception as e:
            print(f"FAIL: {narration_lang} (lang_code={lang_code}, voice={voice}) -> {type(e).__name__}: {e}")
            results.append((narration_lang, False, str(e)))
    
    print("\n=== SUMMARY ===")
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    for lang, ok, err in results:
        status = "PASS" if ok else "FAIL"
        print(f"  {status}: {lang}")
    
    print(f"\nPassed: {passed}/{total}")
    
    if passed < total:
        sys.exit(1)

if __name__ == "__main__":
    main()