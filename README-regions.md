# Past the Postcard — destination history shorts

The renderer is shared: regional differences belong in `regions/<slug>.yaml`, not Python branches. The pipeline is destination-driven, not limited to a fixed country dropdown: use a city, landmark, region, or country from any nation, create a config on demand, then review its facts and image board before rendering. It is not necessary to pre-store every country or city as a config.

The short format opens immediately on a strong image plus a 3–5-word hook (first 1–2 seconds), keeps a subtle **PAST THE POSTCARD** channel mark, and ends with a brief branded close—no separate five-second intro that wastes the hook.

## Add a region

1. Run `python -m pip install -r requirements-regions.txt`.
2. Create a destination config on demand: `python destination-prototype/make_region_config.py "Kyoto" --country "Japan"` (or substitute any destination and country). The workflow accepts any config slug, not just Tokyo/Lagos.
3. Draft a config with `python destination-prototype/make_region_config.py "Kano" --country "Nigeria"`. This writes `regions/kano.yaml`; it is intentionally a draft, not verified history.
4. Edit the config:
   - Add 8–12 scene entries, each with at least three Commons search variants, required and banned keywords, a narration intent, and `image_type: historic | modern | map`.
   - Set map latitude/longitude and the visual palette/accent/mood.
   - Add trustworthy source URLs and a `claim_sources` mapping for every factual claim used in the narration.
   - Write an original 110–155 word story, including a first-three-second hook and a useful visitor tip. Add local pronunciation hints.
5. Run the config checks in GitHub Actions. Review facts, pronunciations, candidate image matches, image rights, and generated contact sheet before approval.
6. Create a review JSON file in the branch (see `regions/review-template.json`). Set `approved: true` only after a human has checked the script and sources. The approval gate requires every claim key from the region config, HTTPS source URLs, reviewer and date.
7. Run **Region-driven history shorts** via Actions → Run workflow, select the region, enter the committed review JSON path, and set `render_approved` to true.
8. Review the MP4, contact sheet, credits, narration and QA report artifacts. A successful config check alone is not a rendered-video QA pass.

## Scene timing (`starts_at`)

List scenes **in narration order**. Give each scene after the first a `starts_at:` phrase copied from the narration; the cut lands where that phrase is spoken (word times come from Kokoro's per-sentence chunk boundaries). A phrase that is missing or out of order stops the render with a clear error. Scenes without `starts_at` are spaced evenly between their neighbours; if no scene has it, the old intent-length weighting is used.

## Asset rules

Scene images are found, in order, from: Commons categories (the place, each `named_entities` entry, and optional `commons_categories`), then museum/library APIs (Met, Art Institute of Chicago, Cleveland, Library of Congress; Smithsonian, Europeana and DPLA when their API-key env vars are set). NGA, NYPL, Rijksmuseum and Getty are off by default because their endpoints were not verified; enable with `asset_resolver.providers`.

- Queries come from `named_entities`, then `visual_tags`, then `image_search_terms`, plus short forms. Keep them short: museum APIs match a few words best.
- `required_keywords` rank candidates (and hard-filter only free-text Commons search). Relevance is decided by CLIP.
- Gates: explicit permissive rights, banned terms (whole words), size checked against how the renderer will actually display the image, OCR text-density (when tesseract is installed), no duplicates, CLIP document-vs-picture probe, CLIP relevance threshold, and for `image_type: modern` a CLIP "modern photograph" probe.
- When a scene fails, the approval board shows a **Resolver diagnostics** section: queries tried, candidates found per provider, how many were rejected by each gate, the top rejected items, and provider errors.
- Existing repository stills are reused only if `assets_manifest.json` records a verifiable source and license **and** `"approved": true`.
- Maps are drawn offline from vendored Natural Earth data (public domain) in the region palette (`map.span_deg`, `map.places`, `map.connect`); no tile servers are called.

The workflow intentionally does not send, publish or merge anything. Review outputs before publishing. A generated ambient bed is procedural, not a licensed music recording; if a real region-specific licensed music track is required, add a verified source/license field and a corresponding validation step before using it.
