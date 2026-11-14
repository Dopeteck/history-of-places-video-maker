# Past the Postcard — weekly shorts review

## A/B hook test
- Give each topic two 3–5-word opening hooks.
- The first two seconds should show a real, relevant, rights-cleared image with the hook overlaid. Do not use a standalone title card or a map as the opening image.
- Keep the rest of the video as comparable as possible between variants so the hook is the main change.
- Do not declare a winner until both variants have comparable exposure and enough views to avoid overreacting to noise.

## Human approval gate
Before production rendering, a reviewer must approve the exact script and source mappings, chosen images and licenses, and each hook variant to be tested. The review JSON must list approved variants in `approved_hook_variants`. The workflow must fail closed if the selected variant is not listed. Visual review artifacts are not publishable videos. This workflow does not publish or send Telegram messages.

## Weekly 10-minute review
1. Export platform analytics and enter available metrics in `shorts-performance-template.csv`.
2. Compare the two hooks for the same topic and platform: viewed rate, average view duration, 3-second retention, completion, shares, saves, and follows.
3. Compare similar time windows and similar view counts; do not treat a small or unequal sample as a reliable winner.
4. Prefer a hook only when its improvement is consistent and does not harm completion or engagement quality.
5. Add one sentence to Notes: what the data suggests and what to test next.
6. Leave metrics blank when a platform does not expose them. Never invent values.
