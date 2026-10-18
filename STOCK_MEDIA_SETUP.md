# Stock media setup (free API, no paid plan assumed)

The destination-history collector can search **Pexels, Pixabay, and Unsplash**. Pexels and Pixabay provide photos and videos; Unsplash is photo-only. Each provider requires its own API credential. All downloads remain unapproved until reviewed.

## Add the API credentials

In GitHub, open **Settings → Secrets and variables → Actions → New repository secret**. Add the secrets for providers you want to use. Never commit keys into code, JSON, workflow YAML, or issues.

| Provider | Secret name | Media | API access |
|---|---|---|---|
| Pexels | `PEXELS_API_KEY` | Photos + videos | https://www.pexels.com/api/ |
| Pixabay | `PIXABAY_API_KEY` | Photos + videos | https://pixabay.com/api/docs/ |
| Unsplash | `UNSPLASH_ACCESS_KEY` | Photos only | https://unsplash.com/developers |

Use local environment variables for local runs. You do not need to configure every provider: select only those whose secrets you have added.

## Run the collector

Search all configured providers for photos:

```bash
python stock_media.py --plan destination-prototype/tokyo-plan.json --assets destination-prototype/assets --manifest destination-prototype/assets_manifest.json --mode photo --provider all
```

Search Pexels and Pixabay for video candidates (Unsplash will not be queried for video):

```bash
python stock_media.py --plan destination-prototype/tokyo-plan.json --assets destination-prototype/assets --manifest destination-prototype/assets_manifest.json --mode both --provider all
```

Or limit a run to one provider with `--provider pexels`, `--provider pixabay`, or `--provider unsplash`. To keep the first test small, use `--per-scene 1`.

The collector records the source page, creator, provider, retrieval date, dimensions, media type, license label, query, and local filename in `assets_manifest.json`. It marks every candidate `approved: false`; ingestion is not rights approval.

## Review before rendering

For every candidate:
- Open its source page and confirm the exact asset, creator, and current license/terms.
- Confirm it depicts the intended place/period; generic Tokyo imagery must not be treated as evidence for a specific historical claim.
- Inspect identifiable people, logos, branded signs, artwork, and misleading historical context.
- Verify the local file opens and has sufficient resolution for a 1080×1920 crop.
- Set `approved: true` only after review; resolve review flags or retain outstanding risks.
- For video, inspect the entire clip and confirm duration, orientation, frame size, and decodability.

**License handling:** manifest labels are provider-level starting points, not a substitute for checking the exact license and current provider terms. Pixabay may contain AI-generated material, so inspect and flag it rather than assuming it is documentary photography. Unsplash and Pexels have their own API attribution/display guidance; follow current API terms as well as the content license. People, trademarks, and artworks can carry separate rights.

## Current renderer limitation

The destination renderer currently supports reviewed **photo assets**. The collector can fetch video candidates from Pexels and Pixabay, but actual video trimming, audio muting, crop handling, and scene timing are not yet integrated into the destination renderer. Do not treat downloaded clips as render-ready still images.

## Secrets and costs

- Add only the provider secrets you intend to use: `PEXELS_API_KEY`, `PIXABAY_API_KEY`, and/or `UNSPLASH_ACCESS_KEY`.
- No paid plan is assumed by this code; providers can change API limits or terms.
- No stock API key is needed for offline unit tests.
- No downloaded asset is approved automatically.
