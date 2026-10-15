# Stickman History Story Bot

One narrator tells a researched history story while original stick figures act it out. Supports 3–5 numbered Shorts and chaptered long videos. Runs in GitHub Actions without rendering on your laptop. Scripts require source review before speech or video generation.

**Status:** story generation, source review/approval, CPU Kokoro narration, and the Remotion choreographed stickman renderer are integrated. The previous Pillow renderer remains selectable as a fallback. Scheduling is deliberately paused. See [STAGE3.md](STAGE3.md) for the legacy renderer's benchmarks. The archived V2 dialogue generator is `legacy_main.py`; the default `main.py` runs the history pipeline.

## Start in GitHub — no local setup

1. Open **Actions → Stickman history pipeline**. A source-code push runs unit tests and the offline portrait/landscape smoke tests automatically. You can also choose **Run workflow → action: smoke** on `main`.
2. Open the completed run and download its `story-...` artifact. The two smoke videos use test tones, not narration. They require no API keys and make no LLM/TTS calls.
3. Add at least one script-provider key under **Settings → Secrets and variables → Actions → New repository secret**. The workflow uses these names:

| Secret | Purpose |
| --- | --- |
| `GROQ_API_KEY` | Primary scripting provider; recommended |
| `GEMINI_API_KEY` | Optional fallback |
| `OPENROUTER_API_KEY` | Optional free-model fallback |
| `TELEGRAM_BOT_TOKEN` | Optional delivery and failure alerts |
| `TELEGRAM_CHAT_ID` | Destination paired with that bot |

Kokoro needs no API key. Its model and English resources download on the first speech run. Use the free quotas of the script providers; availability and limits can change. GitHub private-repository runner minutes and storage are also limited. This pipeline does not purchase credits or enable billing.

## Prepare a story

Add a file such as `research/my-topic.json` with evidence you have actually checked:

```json
[
  {
    "id": "source_1",
    "title": "Title of a source you have read",
    "url": "https://example.org/replace-with-the-real-source",
    "excerpt": "Replace this with the relevant source evidence."
  }
]
```

The example is a format illustration, not usable historical evidence. Source URLs are recorded rather than fetched automatically. Each excerpt is limited to 6,000 characters, with 24,000 total and at most 20 sources.

Run **action: draft**, choose `shorts` or `long`, supply the topic and evidence-file path. Set `minutes` to 8–15 for long videos or `parts` to 3–5 for Shorts. The run stops after creating the script and claim-review package. The summary prints a draft ID, and the workflow commits the files to `state/drafts/<draft_id>/`.

You may instead populate `topics.json`:

```json
[
  {"topic": "Your researched topic", "enabled": true, "sources_file": "research/my-topic.json"}
]
```

With a blank topic input, the first enabled, unused topic is selected. The empty queue shipped with this repository prevents accidental generation of an unchosen story. `config.STORY_NICHE` defaults to world history; change it for your channel's niche.

## Review, approve, render

1. Read `script.md` and `fact_check.md` in the saved draft. Check the original sources and the entire narration; model confidence is not verification.
2. Copy `review.template.json` into `state/reviews/<draft_id>.json`. Fill in `reviewer`, `full_script_reviewed`, `sources_checked`, and every claim's decision, source IDs and note. Claims must be verified or properly qualified. Legends and disputed claims need audible qualifications in the script.
3. Run **action: render**, enter the exact `draft_id`, set **approve: true**, and enter the completed `review_path`. The **renderer** defaults to `remotion`; `legacy` selects the previous Pillow renderer. For Shorts, choose a part from 1 to the series length; for long videos, leave part at 0.
4. The workflow validates the review and saves approval hashes before rendering. Later parts use the same draft with **approve: false**. Changed story, review or fact-check files invalidate approval.

If you change the narration, edit `story.json`, run **action: recheck** with its draft ID, and review the new draft. Editing only `script.md` does not change the authoritative narration. The workflow never silently generates replacement narration during rendering.

Completed parts are recorded in `state/series.json`. Out-of-target durations are marked `needs_timing_review` and held from automatic continuation; adjust the approved text through recheck, or change `STORY_SPEED` before manually retrying. Generated videos are delivered for inspection, not automatically posted to YouTube or Rumble.

## Outputs and persistence

The run artifact contains final MP4s, thumbnails for long videos, source/disclosure descriptions, render metadata, and all review/state files. Chapter timestamps come from measured encoded segment durations. Shorts metadata includes the part number.

Artifacts expire after **7 days** and require your GitHub login. Download videos you want to keep. Durable scripts, reviews, approval receipts and part progress are committed to the private repository; large audio/video files are not. Model downloads use a GitHub cache; it is an optimization, not durable storage. A state-push conflict fails safely and leaves recovery files in the artifact—there is no force push.

Only runs on `main` are allowed. Runs share one concurrency group. Source pushes perform smoke tests only; they do not generate history scripts, consume script API quotas, or send Telegram messages.

## Telegram setup

1. Open Telegram's verified **@BotFather**, use `/newbot`, and copy the token into the repository secret `TELEGRAM_BOT_TOKEN`. Do not commit or paste the token into source files.
2. Open your new bot and press **Start**, then send it a message.
3. To obtain the destination ID, open the Telegram Bot API `getUpdates` endpoint with your token in your own browser, and read `result → message → chat → id`. Keep that token-bearing URL private. Save the ID as `TELEGRAM_CHAT_ID`. For a group, add the bot and send a command addressed to it; for a channel, make the bot an administrator with posting permission and use the channel ID or public `@username`.
4. Run a manual **smoke** workflow. If both secrets are present, it sends a status link and the small portrait test video. Missing secrets simply skip delivery.

Draft runs send `script.md`, `fact_check.md`, and `review.template.json`. Shorts below **49,000,000 bytes** are attached; larger Shorts and all long videos use the artifact link. Known rate limits get one bounded retry; ambiguous transport failures are not blindly retried. Failure alerts are best effort—runner cancellation or dependency-install failure can prevent them.

## Schedule

The daily cron is commented out in `.github/workflows/daily_story.yml`. Keep it paused until smoke and a reviewed end-to-end production run succeed. When enabled, `0 7 * * *` means **08:00 Lagos time**. Scheduled runs render the next unfinished approved part first, otherwise draft the next enabled topic for review. They never auto-approve. An empty queue with no approved work exits as idle.

## Choreographed stickman renderer

Approved renders use the vendored MIT-licensed animation engine from [nispa/stickman-video](https://github.com/nispa/stickman-video), adapted from upstream commit `c2ede0f707559bf9e3d207c3902d64bf3affe8ae`. The Remotion subproject is in `remotion-stickman/`; see its README and `UPSTREAM_LICENSE` for attribution.

Unlike the old renderer, this path uses per-scene action beats and keyframed SVG figures. The LLM supplies opening poses, actions, emotions, and horizontal movements; the renderer animates walk/run cycles, gestures, reactions, camera movement, vector props, titles, sound effects, captions, and original synthesized background audio. Action beats are validated and repaired before they reach the renderer.

The animation path does not depend on Claude Code, Gemini Media MCP, paid image generation, or paid TTS. Script generation uses the existing configured provider keys/free quotas; narration is local Kokoro, and the sound effects/music are synthesized locally. Remotion's free license covers individuals and teams of up to three people, including commercial use, subject to its terms: https://www.remotion.dev/docs/license.

A manual **Remotion choreographed animation preview** workflow renders a multi-scene visual fixture without LLM or TTS API calls. This smoke preview uses synthetic tone audio, not narration. Production rendering still requires an approved saved story. Long-video runtime and final visual quality must be judged from the rendered output; the passing smoke test alone is not a quality guarantee.

## Art and voice

The original Clean & Modern style uses ivory `#F7F1E3`, charcoal `#2B2B2B`, teal `#1FA2A6`, mustard `#E0A526`, and sparse red `#C0392B`. Characters share one skeleton with 12 poses, seven expressions and eight accessories. Backdrops are procedural and need no paid image service.

To use your own character, add a transparent PNG at `assets/characters/<cast_id>_<pose>.png`. Match canvas size and foot placement across poses. Overrides replace the entire drawn character, including face/accessory, while scene movement continues. Missing PNGs use in-memory procedural art; runtime previews never overwrite `assets/`.

To add an accessory, extend `story.ACCESSORIES` and the accessory branch in `characters.character`, then run the tests. To add a backdrop, extend `story.BACKGROUNDS` and `scenes.backdrop`. `python main.py --prefetch-assets` writes an accessory preview sheet's individual PNGs under `work/art-preview/` only.

Voice defaults are in `config.py`: `STORY_VOICE='bm_george'`, `STORY_SPEED=1.0`. Alternatives: `bm_fable` and `am_michael`. Kokoro is not used for voice cloning. Word timing is estimated from exact WAV lengths, so karaoke may be imperfect; it is not forced alignment. Long captions are off by default. The lower-level CLI supports `--captions` to enable them.

## CLI / Colab

Use Python 3.11, FFmpeg with libass, DejaVu fonts and espeak-ng. Explicit Python dependencies are pinned; OS and transitive dependencies are not a fully hermetic lock.

```bash
pip install -r requirements.txt
# Only needed for actual narration:
pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-voice.txt

python main.py --smoke-test --mode shorts
python main.py --mode long --minutes 10 --topic "Your topic" --sources research/my-topic.json --require-approval
python main.py --approve --draft DRAFT_DIRECTORY --review COMPLETED_REVIEW.json
python main.py --render --draft DRAFT_DIRECTORY --series-part 1
python main.py --suggest-topics 5 --niche "West African history"
python -m unittest discover -s tests -v
```

`--story` remains an accepted compatibility flag. Direct CLI drafts live under `output/stories/` unless `--output` is supplied. GitHub uses `state/drafts/` for persistence. Direct CLI renders do not push state to GitHub; `workflow_run.py` owns workflow part tracking. Ideas suggested by the LLM are disabled and unverified; they are not automatically queued.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| No script API key | Add a supported key in Actions secrets; smoke requires none. |
| 429 or malformed JSON | The pipeline retries with backoff and switches providers. If all fail, retry after quota resets; do not approve partial output. |
| Approval rejected | Fill every claim, use checked sources, and verify the saved hashes. After narration edits, use recheck. |
| No unused topic | Add an enabled queue entry or supply a topic directly. |
| Missing Kokoro resource | Check model-download network access and install CPU torch before voice requirements. Retry with the model cache. |
| Empty TTS | The renderer stops rather than producing silent production narration. |
| Missing font / subtitle filter | Install `fonts-dejavu-core` and FFmpeg with libass. |
| FFmpeg failure | Read the final error in the run logs and download recovery files. Incomplete renders are not marked complete. |
| State push denied | Allow Actions write access under repository Actions settings; branch protection may require a compatible policy. Download the artifact before it expires. |
| State conflict | Preserve the recovery artifact, reconcile state with the latest `main`, then rerun. No force push is used. |
| Telegram rejects delivery | Start the bot, verify the chat ID and posting permissions, and confirm both secrets. |
| Video outside target | Review measured duration; adjust voice speed or revise and reapprove narration. |
| Runner minutes exhausted | Keep scheduling paused, use short smoke tests, and check GitHub usage before long renders. |

## Files and validation

| File | Role |
| --- | --- |
| `main.py`, `story_cli.py` | Default CLI and review/render commands |
| `config.py`, `llm.py`, `story.py`, `factcheck.py` | Providers, narrated story generation, evidence review |
| `story_store.py`, `state/`, `topics.json` | Saved drafts, approval hashes, queue and progress |
| `characters.py`, `scenes.py`, `captions.py`, `render.py`, `thumbnail.py` | Art, animation, subtitles and video |
| `tts.py`, `requirements-voice.txt` | CPU Kokoro narration |
| `workflow_run.py`, `persist_state.py`, `telegram_notify.py` | CI orchestration, persistence and delivery |
| `.github/workflows/daily_story.yml` | Manual workflow, smoke CI, paused daily schedule |
| `tests/` | Offline regression tests; no real Telegram messages |
| `legacy_main.py`, `requirements-legacy.txt`, `docs/legacy_daily_video.yml` | Archived V2 path; no active legacy workflow |

Stage 3 verified real Kokoro synthesis and a short narrated render, plus 1080p portrait and landscape tone tests. These do not guarantee a ten-minute cloud render time or historical accuracy. Production narration must still be reviewed. The current workflow test outcome is reported in GitHub Actions. Full source listings are in [docs/SOURCE.md](docs/SOURCE.md).


## Experimental scene-card planner (Stage 2)

An additive planner and image-asset layer is available on the `feat/scene-planner-assets` branch. It does not replace the current renderer or change the existing human approval workflow. See [docs/SCENE_CARDS_STAGE2.md](docs/SCENE_CARDS_STAGE2.md) for source JSON, CLI usage, optional image-provider keys, procedural fallback, caching and review requirements.

Create a storyboard draft with `python scene_cards_cli.py --topic "Your topic" --sources sources.json --out output/my-draft`. Add `--generate-images` to create cached scene art; remote image APIs may incur charges when configured. The command stops at a review package and never approves or renders the story.
