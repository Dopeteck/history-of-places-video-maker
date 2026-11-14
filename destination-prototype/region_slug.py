"""Normalize user-entered destination names to region config slugs."""
import re

def normalize_region_slug(value: str) -> str:
    """Return a lowercase hyphenated slug, rejecting empty/punctuation-only input."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    if not slug:
        raise ValueError("Destination must contain at least one letter or number.")
    return slug
