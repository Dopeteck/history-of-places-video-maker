# Stickman Video Bot V2 — short + long, local assets

**History rebuild — Stage 3:** the new procedural renderer, offline smoke test, and Kokoro integration are implemented. See [STAGE3.md](STAGE3.md) for setup and render commands, and [STAGE2.md](STAGE2.md) for script approval. The workflow and examples below are still legacy V2 until Stage 4.

A lightweight Python/MoviePy/FFmpeg generator adapted for **stickman videos instead of stock footage**.
It can render portrait shorts or longer landscape/portrait videos and can run unattended in GitHub Actions.

## What changed in V2

- No Pexels/Pixabay/stock-footage step.
- Character art comes from `assets/characters/*.png`.
- Background art comes from `assets/backgrounds/*.png`.
- If an asset is missing, the bot creates a simple local placeholder so CI can still complete.
- `--mode short|long`, `--aspect portrait|landscape|auto`, and `--minutes` were added.
- Long mode is no longer capped at 12 dialogue scenes.
- Added `explainer` and `story` formats alongside history/joke/riddle.
- GitHub Actions supports manual test runs and a daily scheduled render.

## Local stickman asset structure

```text
assets/
├── characters/
│   ├── teacher_standing.png
│   ├── teacher_talking.png
│   ├── teacher_shocked.png
│   ├── teacher_laughing.png
│   ├── teacher_thinking.png
│   ├── student_*.png
│   ├── narrator_*.png
│   ├── friend_*.png
│   ├── peasant_*.png
│   ├── knight_*.png
│   └── stranger_*.png
└── backgrounds/
    ├── classroom.png
    ├── studio.png
    ├── room.png
    ├── tavern.png
    └── street.png
```

Use transparent PNGs for characters. For each character, keep the same canvas size and feet position in every pose so pose swaps do not jump.

Create placeholders once:

```bash
python main.py --prefetch-assets
```

Then replace the generated PNGs with your final stickman art **without changing the filenames**.

## Run examples

Install FFmpeg and Python requirements, then set at least one LLM API key.

```bash
pip install -r requirements.txt
export GROQ_API_KEY="..."
```

Short portrait:

```bash
python main.py --category history --mode short
```

3-minute landscape explainer:

```bash
python main.py --category explainer --mode long --minutes 3 \
  --topic "why your brain remembers embarrassing moments"
```

5-minute portrait story:

```bash
python main.py --category story --mode long --aspect portrait --minutes 5 \
  --topic "a student who found a note inside a library book"
```

Output is written to `output/` with an MP4 and metadata JSON.

## GitHub Actions

Add these repository secrets:

- `GROQ_API_KEY` — recommended primary script provider
- `GEMINI_API_KEY` — fallback
- `OPENROUTER_API_KEY` — optional fallback
- `TELEGRAM_BOT_TOKEN` — optional delivery
- `TELEGRAM_CHAT_ID` — optional delivery

The daily schedule is currently commented out until the first successful test render. When enabled, it runs at **07:00 UTC / 08:00 WAT** and defaults to long mode. You can also open **Actions → Scheduled stickman video → Run workflow** and choose category, mode, aspect, minutes and topic.

## Why GitHub Actions instead of Colab for scheduling

Colab is useful for experiments, but it is an interactive notebook environment and is not a dependable unattended scheduler. GitHub Actions is a better fit here because the repo, local assets, cron, secrets, FFmpeg install, output artifact and Telegram delivery all live in one reproducible workflow.

## Practical first production target

Start with 2–3 minute landscape videos until the style is stable. Once the render is consistent, increase to 4–5 minutes. Very long videos create larger LLM responses, more TTS calls and larger render artifacts, so they are better added after the first scheduled runs are proven.
