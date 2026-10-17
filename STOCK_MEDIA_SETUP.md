# Stock media setup (free API, no paid service)

## Recommended first provider: Pexels

Pexels offers a free API for both photos and videos. You need to create a Pexels account and request an API key; the pipeline cannot authenticate without it. API access and use are subject to Pexels' current terms, rate limits, and attribution requirements.

1. Create/get a key at https://www.pexels.com/api/
2. In GitHub, open the repository **Settings → Secrets and variables → Actions → New repository secret**.
3. Name it `PEXELS_API_KEY` and paste the key as the secret value. Never commit the key into code, JSON, workflow YAML, or a public issue.
4. For local tests, set the environment variable in your terminal session rather than saving it in the repository.
5. Run the collector from the repository root:

```bash
python stock_media.py --plan destination-prototype/tokyo-plan.json --assets destination-prototype/assets --manifest destination-prototype/assets_manifest.json --mode photo
```

To search for video candidates as well:

```bash
python stock_media.py --plan destination-prototype/tokyo-plan.json --assets destination-prototype/assets --manifest destination-prototype/assets_manifest.json --mode both
```

The collector downloads a small, bounded number of candidates and records their source page, photographer, provider, retrieval date, dimensions, media type, and license label. It intentionally marks every new candidate `approved: false` and adds review flags. This is a *candidate-ingestion step*, not automatic rights approval.

## Human review required before render

For every candidate:
- Open the source page and confirm the exact media, creator, and current license/terms.
- Check it actually depicts the intended place/period; do not use generic Tokyo footage as proof of a specific historical claim.
- Inspect for identifiable people, logos, branded signs, copyrighted art, and misleading historical context.
- Verify the local file opens and is high enough resolution for 1080x1920 crops.
- Set `approved` to `true` only after review; remove resolved flags and keep any unresolved risk flags.
- For a video candidate, inspect the full clip and confirm duration, orientation, frame size, and that the downloaded file is decodable.

Pexels' license does not require creator attribution in general, but API guidelines request a prominent Pexels link and recommend photographer credit. Keep provider/creator details in the manifest and include a credit screen/description credit in the eventual publication workflow. Depicted people, trademarks, brands, and artworks may have separate rights.

## Current renderer limitation

The current renderer can use **reviewed photo assets**. The collector can also fetch video candidates, but the current destination renderer does not yet decode and cut those video clips into scenes. Video files are recorded in the manifest and must not be mistaken for still images. Implementing actual clip trimming, audio stripping/muting, aspect/crop handling, and scene timing is the next video-provider integration task.

## Alternative source

Pixabay also offers a free API for images and videos: https://pixabay.com/api/docs/. Its API requires a key too, and the usage/license rules should be checked for each integration. Wikimedia Commons is useful for historically specific photographs/maps, but license and attribution metadata varies file by file; do not assume all Commons content is commercially reusable.

## Required secrets and costs

- Required now for stock ingestion: `PEXELS_API_KEY` (free API key; no paid plan assumed).
- Required for speech render: existing Kokoro dependencies and FFmpeg in the runner.
- No stock provider key is required to run offline unit tests.
- No photos or video clips have been marked approved by this documentation.
