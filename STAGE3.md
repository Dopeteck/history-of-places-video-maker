# Stage 3 — character rendering and narration

The new renderer supports approved narrator-led scripts, procedural characters and backdrops, portrait Shorts and landscape chapters. The existing Actions workflow is still V2; workflow integration and persistent rendered-part tracking belong to Stage 4. Scheduling remains paused.

## Offline renderer test

On an Ubuntu runner or Colab, install FFmpeg, fonts, and the small renderer requirements:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg fonts-dejavu-core
pip install -r requirements-render.txt
python main.py --smoke-test --mode shorts
python main.py --smoke-test --mode long
```

The test itself makes no network calls. It renders a 2.5-second title and four scenes using tone audio, without API keys or Kokoro. Output is `output/stories/smoke-shorts/video.mp4` or `smoke-long/video.mp4`. The content lasts 20 seconds; AAC container padding can add approximately 0.04 seconds. `--preview` halves the output dimensions. `--captions` enables long-mode subtitles; Shorts always use karaoke.

## Kokoro narrator

Install CPU PyTorch first to avoid pulling GPU packages, then the voice requirements:

```bash
sudo apt-get install -y espeak-ng
pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-voice.txt
```

The first narration run downloads the Kokoro model, selected voice and language resources. It therefore needs network access; later runs can reuse the model and WAV caches. Voice choices are `bm_george` (default), `bm_fable`, and `am_michael`. The pipeline uses British or American English according to the selected voice. `--speed` accepts 0.7–1.3. No paid TTS service or voice cloning is used.

Kokoro usage follows the [upstream repository](https://github.com/hexgrad/kokoro) and its [voice list](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md). The supplied model is Apache-licensed; preserve applicable upstream license notices when redistributing model assets.

## Render a reviewed script

Complete the evidence review and approval described in [STAGE2.md](STAGE2.md), then:

```bash
python main.py --story --render --draft DRAFT_DIRECTORY --series-part 1 --voice bm_george
python main.py --story --render --draft LONG_DRAFT_DIRECTORY --voice bm_george
```

The CLI rechecks approval and all saved-script hashes before loading speech or rendering. Shorts require an explicit part number; long mode renders all chapters. `--approve` only approves; rendering is a separate action. No script regeneration occurs during rendering. Existing V2 commands remain available; use `--story` to disambiguate new story commands.

Each render folder contains video, per-chapter segments and timing records, cached WAVs, a source/disclosure description, and `render-metadata.json`. Long videos also get a procedural 1280×720 thumbnail. Chapter timestamps use measured encoded segment lengths. Narration is never shortened to fit a guessed duration. Out-of-target videos are flagged for revision, not automatically published.

WAV lengths are measured exactly. Word alignment is currently estimated proportionally from the measured audio and labelled accordingly; this is not forced alignment. Animated mouths are limited to characters explicitly assigned a talking pose. Other actors remain silent while the narrator tells the story.

## Locked visual style

- Warm ivory `#F7F1E3`, charcoal `#2B2B2B`, teal `#1FA2A6`, mustard `#E0A526`, sparse red `#C0392B`.
- One base skeleton, 12 poses, seven expressions, eight accessories plus no accessory; cached supersampled drawings.
- Nine procedural backdrops and seven props; no external image-generation dependency.
- Deterministic blinking, gentle body motion, gestures on emphasis, running motion, pop-in, camera movement, brief shake and fades from ivory.
- 24 fps output with pose animation on twos; PIL handles image scaling, no MoviePy resize or ImageMagick.
- ASS captions are burned with a relative filename and the chapter directory as FFmpeg's working directory.

PNG overrides go in `assets/characters/<cast_id>_<pose>.png`. Use transparent square canvases with consistent feet placement. Overrides take precedence over procedural faces and accessories and remain static artwork while scene movement continues. Missing overrides fall back in memory; the renderer never writes placeholders to `assets/`.

## Verification and limits

Run `python -m unittest discover -s tests -v`. The suite covers all visual enums, PNG fallback, caption colors/escaping, timing math, empty/nonfinite audio rejection, deterministic movement, provider fallback and exact-content approval.

Full-resolution portrait and landscape smoke renders are checked for successful encoding and visual layout. Their local wall-clock benchmarks are printed and saved in metadata. These short synthetic tests do not establish a 10-minute GitHub Actions runtime or historical/narration quality. No live history story has been approved or published. Full workflow, Telegram delivery, automatic state persistence and final dependency consolidation remain Stage 4.

### Recorded checks

- Python 3.12: 23 unit tests passed.
- Portrait 1080×1920, 24 fps, H.264/AAC with karaoke: 20.04 seconds, 23.51 seconds render wall time.
- Landscape 1920×1080, 24 fps, optional captions: 20.04 seconds, 23.36 seconds render wall time.
- Two-chapter concatenation at preview resolution: 40.04 seconds, second chapter timestamp 0:20.
- The offline smoke tests contain tone audio; the separate voice integration demo contains Kokoro narration.
- Kokoro 0.9.4 with CPU PyTorch 2.6.0 successfully generated a 5.95-second `bm_george` sample. Model and English language resources downloaded successfully. This verifies synthesis, not a full-length narrated history video.
- End-to-end Kokoro integration rendered the four-scene fictional fixture with speech and karaoke: 16.88 seconds, 23.27 seconds wall time at preview resolution. It correctly flagged the fixture as below the 60-second Shorts target.
