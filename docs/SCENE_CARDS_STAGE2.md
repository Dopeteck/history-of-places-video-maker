# Stage 2 — Scene-card planning and image assets

This branch adds an isolated scene-card planning/asset layer. It does not replace the current `main.py` history workflow, change the renderer, enable the scheduled workflow, or approve generated material automatically.

## Modules

- `scene_planner.py` asks the configured `JsonLLM` for a bounded storyboard and passes every response through strict JSON/schema validation. The existing `llm.py` extracts JSON, retries validation failures with a correction prompt, and tries configured providers sequentially.
- Every factual scene must cite one or more source IDs from supplied source excerpts. Unknown source IDs, duplicate scene IDs, invalid labels/transitions, oversized narration, and unsupported card types are rejected.
- The planner passes each declared claim through the existing `factcheck.validate_claims` path. Extracted claims remain `needs_human_review`; model confidence is not verification.
- `make_review_template` creates a review file bound to the exact plan digest. `validate_scene_plan_review` requires a reviewer, full-script review, checked sources, a note and decision for every claim, and checked evidence for every scene. Qualified claims require disputed/legend labeling and an audible qualifier.
- `scene_assets.py` tries optional FAL then OpenAI image generation, validates the result, and falls through to deterministic local Pillow illustrations when providers are unavailable or fail. Remote provider keys are optional.
- Images are cached as PNGs using SHA-1 keys derived from prompt, provider/model, output dimensions and fallback palette. Cached files are validated again on read. Provider images are center-cropped to the target card ratio before the quality gate.
- The quality gate rejects small, near-blank, low-variation, or incorrectly proportioned images.

## Environment variables (optional)

- `FAL_KEY` (or `FAL_API_KEY`), optionally `FAL_IMAGE_MODEL` (default `fal-ai/flux/schnell`)
- `OPENAI_API_KEY`, optionally `OPENAI_IMAGE_MODEL` (default `gpt-image-1`)

No key is needed for procedural artwork. The default provider chain is deterministic in order: FAL, OpenAI, procedural fallback. Provider failures are recorded by exception type only, not by raw exception text that could contain sensitive request details.

## Integration status

This is an additive planning/asset API, not yet connected to the existing video renderer or Actions workflow. Run the standalone draft planner with `python scene_cards_cli.py --topic "Your topic" --sources sources.json --out output/my-draft`. The source file must be a JSON list of records containing `id`, `title`, `url`, and a relevant `excerpt`. Add `--generate-images` to exercise the provider chain and save images plus an `assets.json` manifest; remote image providers may incur charges when configured. The command saves `scene_plan.json`, `scene_plan.md`, and `review.template.json`, then stops. A caller can also use `plan_scene_cards(llm, topic, sources)`, `make_review_template(plan)`, and `validate_scene_plan_review(plan, review)` directly. Rendering and persistent approval integration remain later-stage work. Keep the current schedule paused.

## Offline tests

Run:

```bash
python -m unittest discover -s tests -p "test_scene_assets.py" -v
```

The tests cover schema/source rejection, claim extraction and human-review binding, duplicate review rejection, SHA-1 stability, image quality checks, and procedural fallback caching. Tests use fake providers and synthetic source excerpts; they do not prove real-world factual accuracy or verify live image API credentials.
