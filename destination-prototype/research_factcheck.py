#!/usr/bin/env python3
"""Research and fact-checking module for daily content engine.

Gathers evidence from credible sources for each topic before script generation.
"""
import json
import re
import requests
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "destination-prototype" / "data"

# Trusted source domains (prioritized)
TRUSTED_DOMAINS = [
    "wikipedia.org",
    "britannica.com",
    "unesco.org",
    "worldhistory.org",
    "ancient.eu",
    "history.com",
    "smithsonianmag.com",
    "nationalgeographic.com",
    "theconversation.com",
    "academia.edu",
    "jstor.org",
    "projectmuse.org",
    "doi.org",
    "gov.",
    "edu.",
    "museum",
    "archive.org",
    "loc.gov",
    "nara.gov",
    "bl.uk",
    "bnf.fr",
    "dnb.de",
]

# Domains to avoid (low reliability)
BLOCKED_DOMAINS = [
    "pinterest.com",
    "tumblr.com",
    "reddit.com",
    "quora.com",
    "yahoo.com",
    "answers.com",
    "wikihow.com",
    "thoughtco.com",
    "factsnippet.com",
    "factinate.com",
]

@dataclass
class Claim:
    text: str
    sources: List[str]
    confidence: float  # 0-1
    status: str  # "verified", "disputed", "insufficient_evidence"

@dataclass
class ResearchRecord:
    topic_slug: str
    destination: str
    category: str
    title: str
    claims: List[Claim]
    sources_used: List[str]
    research_date: str
    notes: str

def extract_claims_from_narration(narration: str) -> List[str]:
    """Extract factual claims from narration text."""
    # Split into sentences and identify factual statements
    sentences = re.split(r'[.!?]+', narration)
    claims = []
    for s in sentences:
        s = s.strip()
        if not s or len(s) < 20:
            continue
        # Heuristic: sentences with dates, names, numbers, or specific assertions
        if re.search(r'\b\d{4}\b|\b\d{1,2}(st|nd|rd|th)\b|\b[A-Z][a-z]+ [A-Z][a-z]+\b|was (founded|built|established|destroyed|renamed|occupied)|became|changed to|named after', s, re.I):
            claims.append(s)
    return claims


def search_wikipedia(query: str) -> List[Dict]:
    """Search Wikipedia API for relevant pages."""
    try:
        url = "https://en.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": 5,
        }
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json().get("query", {}).get("search", [])
    except Exception:
        return []


def get_wikipedia_page(title: str) -> Optional[str]:
    """Get Wikipedia page content."""
    try:
        url = "https://en.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "prop": "extracts",
            "exintro": True,
            "explaintext": True,
            "titles": title,
            "format": "json",
        }
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        for page in pages.values():
            return page.get("extract", "")
    except Exception:
        return None


def verify_claim(claim: str, destination: str) -> Tuple[float, List[str], str]:
    """Verify a claim against trusted sources. Returns (confidence, sources, status)."""
    # Build search query
    query = f"{claim} {destination}"
    results = search_wikipedia(query)

    sources = []
    confidence = 0.0
    evidence_count = 0

    for result in results[:3]:
        title = result.get("title", "")
        content = get_wikipedia_page(title)
        if content and claim.lower()[:50] in content.lower():
            sources.append(f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}")
            evidence_count += 1

    # Check other trusted sources (simplified - in production would use web search API)
    if evidence_count >= 2:
        confidence = min(0.9, 0.5 + evidence_count * 0.15)
        status = "verified"
    elif evidence_count == 1:
        confidence = 0.5
        status = "insufficient_evidence"
    else:
        confidence = 0.1
        status = "disputed"

    return confidence, sources, status


def research_topic(topic_data: Dict) -> ResearchRecord:
    """Research a topic and build a record with verified claims."""
    narration = topic_data.get("description", "")
    claims_text = extract_claims_from_narration(narration)

    claims = []
    all_sources = []

    for claim_text in claims_text[:8]:  # Limit to top 8 claims
        confidence, sources, status = verify_claim(claim_text, topic_data.get("destination", ""))
        claims.append(Claim(
            text=claim_text,
            sources=sources,
            confidence=confidence,
            status=status
        ))
        all_sources.extend(sources)

    # Deduplicate sources
    all_sources = list(dict.fromkeys(all_sources))

    return ResearchRecord(
        topic_slug=topic_data.get("destination_slug", ""),
        destination=topic_data.get("destination", ""),
        category=topic_data.get("category", ""),
        title=topic_data.get("title", ""),
        claims=claims,
        sources_used=all_sources,
        research_date=datetime.now().date().isoformat(),
        notes=f"Auto-researched {len(claims)} claims from narration"
    )


def save_research(record: ResearchRecord):
    """Save research record to disk."""
    filename = DATA_DIR / f"research_{record.topic_slug}_{record.research_date}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(asdict(record), f, indent=2, ensure_ascii=False)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Research and fact-check a topic")
    ap.add_argument("--topic", required=True, help="Path to topic JSON or 'latest' from schedule")
    ap.add_argument("--output", help="Output research record path")
    args = ap.parse_args()

    if args.topic == "latest":
        schedule_file = DATA_DIR / "daily_schedule.json"
        if not schedule_file.exists():
            print("No schedule found. Run daily_content_engine.py --build-schedule first.")
            return
        with open(schedule_file, encoding="utf-8") as f:
            schedule = json.load(f)
        today = datetime.now().date().isoformat()
        entry = next((e for e in schedule if e.get("date") == today and e.get("topic")), None)
        if not entry:
            print(f"No topic scheduled for {today}")
            return
        topic_data = entry["topic"]
    else:
        with open(args.topic, encoding="utf-8") as f:
            topic_data = json.load(f)

    print(f"Researching: {topic_data.get('title', 'Unknown')}")
    record = research_topic(topic_data)
    save_research(record)
    print(f"Saved research record with {len(record.claims)} claims")
    for c in record.claims:
        print(f"  [{c.status}] {c.text[:80]}... (confidence: {c.confidence:.2f}, sources: {len(c.sources)})")


if __name__ == "__main__":
    main()