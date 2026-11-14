# Region configs

Add a region as `regions/<slug>.yaml`; no region-specific code should be needed.

1. Copy `regions/template.yaml` or run `python destination-prototype/make_region_config.py "Kano" --country Nigeria`.
2. Replace review markers and add 8–12 scenes, each with three image search variants, required and banned keywords, and `image_type` (`historic`, `modern`, or `map`).
3. Use reliable official heritage, museum, tourism and reference sources. Write 120–145 words with a hook, chronological arc, sourced facts, and visitor tip. Verify every claim and submit it to the existing fact-check/approval gate. The helper does not verify facts.
4. Add local spellings and pronunciation hints, palette, accent color, and ambient mood.
5. Run config validation and the region workflow. A scene without a rights-cleared, relevant image must fail the build.

Allowed licenses: Public Domain, CC0, CC BY, CC BY-SA. No NC or ND licenses. Every selected image must have author, license and source metadata. Human review remains mandatory before publishing.
