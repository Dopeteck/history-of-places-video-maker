# Scene-card Stage 3: reviewed video rendering

The Stage 3 renderer is additive. It does not replace or modify the existing `render.py` workflow.

## Safety gate

`scene_cards_render.py` calls `validate_scene_plan_review()` before writing video. The review must match the exact plan digest, identify a reviewer, confirm the full narration was reviewed, and list checked source IDs. Every extracted claim must have an explicit verified/qualified decision. A draft review template is not approval.

The renderer never publishes, schedules, or calls remote image APIs. Remote images remain an explicit Stage 2 action. If an asset folder is not supplied, locally generated procedural illustrations are used.

## Render a reviewed draft

After reviewing `scene_plan.md` and checking the original sources, edit `review.template.json`:
- Set `reviewer` to the human reviewer identity.
- Set `full_script_reviewed` to `true` only after reading the complete narration.
- Add source IDs actually checked to `sources_checked`.
- Give every listed claim a `verified` or `qualified` decision, checked source IDs, and a concise reviewer note.
- Do not approve claims you could not verify. Resolve or remove unsupported claims and regenerate the plan/review digest when the plan changes.

Then run a reduced-resolution render first:

`python scene_cards_render.py --plan output/scene-card-draft/scene_plan.json --review output/scene-card-draft/review.template.json --out output/scene-card-preview --preview --smoke --no-captions`

`--smoke` creates a real MP4 with synthetic test tones, not narration. It verifies the visual timeline and FFmpeg muxing without downloading a TTS model. `--preview` uses a lower resolution. For actual narration, omit `--smoke`; Kokoro and `espeak-ng` must be installed as described in `requirements-voice.txt`.

For a full-size long-form output with narration and captions:

`python scene_cards_render.py --plan output/scene-card-draft/scene_plan.json --review output/scene-card-draft/review.template.json --out output/scene-card-final --mode long`

For a vertical version:

`python scene_cards_render.py --plan output/scene-card-draft/scene_plan.json --review output/scene-card-draft/review.template.json --out output/scene-card-shorts --mode shorts`

Output directories must be new or empty. Rendering does not imply approval to publish. Inspect the final video, captions, duration, and metadata manually before any separate publishing step.

## CI coverage

The scene-card workflow runs offline tests, including an end-to-end FFmpeg smoke render with six scenes, plus a test confirming that an incomplete human review blocks rendering. It does not test paid image providers, remote credentials, or voice quality.

## Stage 4: reference-style portrait composition

In `shorts` mode, each scene uses the supplied reference's broad composition: darkened blurred full-screen background, a persistent white outlined headline above the center, and a full-width 16:9 illustration strip across the middle. The landscape `long` mode retains the earlier card layout. This is a visual-layout approximation; exact style matching still depends on the quality of generated or supplied artwork and a human review of the rendered MP4.

Preview the reference-oriented layout after approval with:

`python scene_cards_render.py --plan output/scene-card-draft/scene_plan.json --review output/scene-card-draft/review.template.json --out output/reference-style-preview --mode shorts --preview --smoke --no-captions`

This smoke preview uses tones, not narration, and does not publish.
