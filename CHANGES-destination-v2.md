# Destination history shorts: v2 fixes

Root causes found by reading the code and the Tokyo approval board:

1. **Diagnostics never reached the board.** `_parse_diagnostics` used a double-escaped regex (never matched) and the resolver never emitted diagnostics.
2. **Search ignored planner entities/tags.** Queries were `image_search_terms + tags + entities` truncated to 3, so only the first three long phrases were ever used.
3. **Modern-scene gate hid the real error and compared different score scales.** It compared a CLIP cosine (~0.25) against a keyword score (>=3) and re-wrapped existing fallback cards, replacing the true failure reason.
4. **Required-keyword hard gate** rejected museum records (titles rarely contain "Japan").
5. **Size gate** demanded 1920px on the long side although framed landscape images are shown at ~1000px wide.
6. **Library of Congress** results used the smallest thumbnail (always below the size gate).
7. **Smithsonian** parser read the wrong JSON path; **Wikidata sitelinks** were never requested; **"No known copyright restrictions"** was rejected because it contains "copyright".
8. Landscape images had **no motion**; the blurred backdrop was **recomputed every frame**; captions **ran off the frame**; scene cuts ignored what the narrator was saying; the OSM-tile map was blank with a misplaced pin.

See `README-regions.md` for the new config keys. Tests: `python -m pytest destination-prototype/test_resolver_logic.py`.

Not verified here (no network to the provider APIs from the build sandbox): live responses of Commons, Met, AIC, Cleveland, LoC, Smithsonian, Europeana, DPLA; real CLIP scores; real Kokoro timing. Run the **visual_review_only** workflow and read the diagnostics block per scene.
