# Long-form history video pilot — implementation plan

## Status

Design branch: `feature/long-form-history-pilot`, based on the successful short pipeline. This branch does not alter the existing short workflow and does not publish or merge anything.

The Tokyo short proves that the current renderer can produce a roughly 55-second, 1080×1920 MP4 with Kokoro narration, word-level karaoke captions, a generated ambient bed, slow motion on still images, and scene transitions. It does **not** prove long-form reliability.

## Pilot target

Build a **3-minute Tokyo history pilot** first. Keep the vertical 1080×1920 format and the visual language you approved. Do not scale to 10–20 minutes until the pilot passes review.

- Target duration: 180 seconds; acceptable pilot range 165–195 seconds.
- Narration: approximately 380–430 words, written as a connected story, not bullet fragments.
- Structure: 4–5 chapters with a hook, historical context, daily life/culture, surviving traces, and a closing visitor-oriented takeaway.
- Visual coverage: about 18–24 distinct scene beats, typically 6–12 seconds each; no duplicate images to pad runtime.
- Audio: Kokoro narration and a quiet, original procedural ambient bed. Mix must stay intelligible throughout.
- Captions: derive word timing from actual narration audio/chunk timing; never stretch the 55-second timing map over a longer script.
- Rights and fact gates: unchanged or stricter. Every image must have traceable source and explicit allowed rights; every historical claim must map to a reviewed HTTPS source.
- No text-card or generated-illustration substitutes for missing historical imagery. If an image cannot pass the gates, fail the render with diagnostics.

## Architecture

Keep `render_region_short.py` and `.github/workflows/region-history-shorts.yml` as the known-good short path. Implement a separate long-form entry point and workflow on this branch, sharing small tested primitives only where their contracts are duration-independent.

1. **Chapter config** — add a long-form config that defines chapters, claim-to-source mappings, visual style, narration language, and scene beats. Validate chapter order and ensure every beat references narration text in order.
2. **Story validation** — validate word count, chapter lengths, claims, scene count, unique titles, source mapping, and image-search coverage before spending time on image resolution.
3. **Image resolution** — reuse the existing rights/relevance resolver; never bypass licensing, duplicate, OCR/document, period, or CLIP gates. Gather diagnostics for every failed beat.
4. **Narration and timing** — synthesize the full narration in manageable chapter chunks, preserve chapter boundaries, and compute word timestamps from the produced audio. Verify chapter audio durations and total duration before rendering.
5. **Timeline render** — render incrementally/chunk-by-chunk to keep memory use bounded. Use the approved still-motion treatment, restrained crossfades, and captions. Keep chapter cards out of the picture; chapter transitions should be conveyed by scene changes and narration.
6. **Quality assurance** — use ffprobe to verify duration, vertical resolution, 30 fps, and audio; check loudness/silence, caption timing coverage, source/credit completeness, duplicate frames/assets, placeholder OCR, and per-chapter scene coverage. Fail closed.
7. **Review artifact** — upload the MP4, contact sheet, narration text, credits, scene timeline, and QA report as a GitHub Actions artifact. Artifact only; no publishing or messaging.

## Known changes required before the pilot can render

The short renderer hard-codes an 8–12 scene limit, 110–155 narration words, a 45–60 second audio duration check, a short-specific output name, and a 45–60 second QA assertion. Do not simply edit the Tokyo short config or remove these checks. Long-form needs its own validation rules and duration bounds, with tests proving the short path remains unchanged.

The current renderer also renders raw frames through one FFmpeg process. The long-form implementation should process frames in bounded batches or use an equivalent streaming design so a 3-minute pilot does not retain a full video in RAM.

## Acceptance checklist

- [ ] A dedicated long-form entry point and workflow exist on this branch.
- [ ] Unit tests reject invalid chapter order, missing claim sources, missing scene search variants, duplicate asset identities, and durations outside pilot bounds.
- [ ] Short workflow/config validation remains unchanged and passes.
- [ ] A full 3-minute Tokyo MP4 is produced with audio and real approved images only.
- [ ] The QA report shows no missing assets, no placeholder cards, no duplicate images, and no unverified claims.
- [ ] Human review confirms pacing, visual quality, audio intelligibility, and captions before any longer runtime is attempted.

## Safety and cost constraints

Use free/open-source software and GitHub Actions. Do not add paid API dependencies, publish the video, send Telegram messages, or merge to `main`. If free runner time or model downloads prevent the render, report the exact blocker rather than weakening a quality gate.
