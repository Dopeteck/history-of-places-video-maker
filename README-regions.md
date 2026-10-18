# Region-driven history shorts

The renderer is shared: regional differences belong in `regions/<slug>.yaml`, not Python branches.

## Add a region

1. Run `python -m pip install -r requirements-regions.txt`.
2. Draft a config with `python destination-prototype/make_region_config.py "Kano" --country "Nigeria"`. This writes `regions/kano.yaml`; it is intentionally a draft, not verified history.
3. Edit the config:
   - Add 8–12 scene entries, each with at least three Commons search variants, required and banned keywords, a narration intent, and `image_type: historic | modern | map`.
   - Set map latitude/longitude and the visual palette/accent/mood.
   - Add trustworthy source URLs and a `claim_sources` mapping for every factual claim used in the narration.
   - Write an original 110–155 word story, including a first-three-second hook and a useful visitor tip. Add local pronunciation hints.
4. Run the config checks in GitHub Actions. Review facts, pronunciations, candidate image matches, image rights, and generated contact sheet before approval.
5. Create a review JSON file in the branch (see `regions/review-template.json`). Set `approved: true` only after a human has checked the script and sources. The approval gate requires every claim key from the region config, HTTPS source URLs, reviewer and date.
6. Run **Region-driven history shorts** via Actions → Run workflow, select the region, enter the committed review JSON path, and set `render_approved` to true.
7. Review the MP4, contact sheet, credits, narration and QA report artifacts. A successful config check alone is not a rendered-video QA pass.

## Asset rules

The renderer searches Wikimedia Commons API results, rejects explicitly disallowed licenses and banned terms, requires metadata keyword matches, minimum dimensions and size, and records per-image credits. Existing repository assets are eligible only when `destination-prototype/assets_manifest.json` records a verifiable source, license URL, attribution and matching keywords. Maps use OpenStreetMap tiles with attribution.

The workflow intentionally does not send, publish or merge anything. Review outputs before publishing. A generated ambient bed is procedural, not a licensed music recording; if a real region-specific licensed music track is required, add a verified source/license field and a corresponding validation step before using it.
