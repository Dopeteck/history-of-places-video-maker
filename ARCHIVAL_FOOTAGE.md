# Archival footage resolver

The additive `archival_footage.py` module searches official APIs in this order:

1. Internet Archive moving-image metadata and item files
2. Library of Congress catalog API
3. U.S. National Archives Catalog API
4. Wikimedia Commons API, video files only
5. NASA Images and Video Library API
6. Smithsonian Open Access API for stills and documents

Example:

```bash
python archival_footage.py --query "Tokyo firebombing 1945" --manifest destination-prototype/assets_manifest.json --assets destination-prototype/assets
```

This creates metadata-only candidates in the shared JSON manifest. It does not download files and it never approves them. Only candidates with an explicit public-domain, CC0, CC BY, CC BY-SA, or “no known restrictions” statement pass the conservative filter. Unknown, non-commercial, no-derivatives, all-rights-reserved, or permission-required items are rejected. Exact source page, rights statement, and rights URL are retained. APIs can change and some institutions express rights in free text, so human verification remains mandatory.

No scraping of news sites or downloads from YouTube, TikTok, Facebook, or other user-video platforms are implemented. An API error is logged and the resolver continues to the next institution.

Fallback order per scene: archival still with slow zoom, map, document/manuscript image, current site photo, and only then an AI reconstruction. If a reconstruction is used, set `"ai_reconstruction": true` on the scene; the renderer overlays **ILLUSTRATION / RECONSTRUCTION**. Archival footage/stills should set `"archival_look": true` for a consistent mild desaturation, grain, and vignette. Scene-level review flags should note identifiable people, logos, modern rights ambiguity, or contextual uncertainty.

**Important limitations:** API responses are discovery metadata, not a guarantee of public-domain status. This first resolver pass does not download or decode media, does not prove a file's entire rights chain, and does not yet automatically orchestrate each fallback type into the video plan. Keep candidates unapproved until the rights statement and item page have been manually checked. NASA material is not blanket public domain; only explicit rights labels pass. Smithsonian access depends on the API's current public access configuration.


## Download and validation gate

After a reviewer checks an item's exact rights statement and source page, they may set that manifest entry's `approved` field to `true`. The downloader refuses every other item and accepts only HTTPS URLs on a fixed institutional host allowlist. It validates image decoding and minimum dimensions, or uses `ffprobe` to confirm a video stream, records SHA-256/byte size/dimensions, and leaves the item flagged for editorial review.

```bash
python archival_download.py --manifest destination-prototype/assets_manifest.json --assets destination-prototype/assets
```

Download failures are written to that item's `download_error` field; the item is not marked as downloaded. The script does not approve rights or publish content. Review before setting `approved: true`; do not treat API metadata as legal advice. This downloader intentionally rejects redirects to hosts outside the institutional allowlist and file types with ambiguous extensions.
