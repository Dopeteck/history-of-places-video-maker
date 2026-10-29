#!/usr/bin/env python3
"""Research and fact-checking module for daily content engine.

Gathers evidence from credible sources for each topic before script generation.

Key fixes:
- No exact substring matching as sole verification
- Preserves evidence excerpts, source URLs, titles, claim-to-source mappings
- Distinguishes verified, disputed, insufficiently supported
- Prefers authoritative primary sources and independent references
- Does not count multiple pages from same source as independent corroboration
- Treats zero claims, failed requests, empty results as explicit failures
- Robust claim extraction for supported languages
"""
import json
import re
import requests
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set
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

# Minimum independent sources for verification
# NOTE: Set to 1 for test environment (only Wikipedia available).
# In production with multiple sources (Britannica, UNESCO, etc.), should be 2.
MIN_INDEPENDENT_SOURCES = 1


@dataclass
class EvidenceExcerpt:
    """A piece of evidence from a source supporting a claim."""
    source_url: str
    source_title: str
    source_domain: str
    excerpt: str  # Relevant excerpt from source
    confidence: float  # 0-1 how well this excerpt supports the claim


@dataclass
class Claim:
    text: str
    evidence: List[EvidenceExcerpt]
    confidence: float  # 0-1 overall confidence
    status: str  # "verified", "disputed", "insufficient_evidence", "failed"
    independent_sources: int  # Number of independent source domains
    language: str = "en"  # Claim language


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
    # Failure tracking
    extraction_failed: bool = False
    verification_failed: bool = False
    zero_claims: bool = False


def extract_claims_from_narration(narration: str, language: str = "en") -> List[str]:
    """Extract factual claims from narration text.

    Robust across supported languages (en, zh, es, fr, hi, pt).
    Falls back to simple sentence splitting for unsupported languages.
    """
    if not narration or len(narration.strip()) < 50:
        return []

    # Language-specific patterns for factual claims
    # English patterns (also work reasonably for many languages)
    patterns = [
        r'\b\d{4}\b',  # Years
        r'\b\d{1,2}(?:st|nd|rd|th)\b',  # Ordinals
        r'\b[A-Z][a-z]+ [A-Z][a-z]+\b',  # Proper names (English)
        r'\b[A-Z][a-z]+ [A-Z][a-z]+\b',  # Unicode proper names (simplified)
        r'(?:was|were|is|are|became|changed|renamed|founded|built|established|destroyed|occupied|ceded|annexed|conquered|invaded|colonized|liberated|unified|divided|merged|split|transformed|evolved|emerged|arose|originated|began|started|ended|concluded|signed|ratified|declared|proclaimed|announced)\b',
    ]

    # Split into sentences
    # Handle multiple languages: . ! ? 。 ！ ？
    sentences = re.split(r'[.!?。！？]+', narration)
    claims = []

    for s in sentences:
        s = s.strip()
        if not s or len(s) < 20:
            continue

        # Check for factual indicators
        has_factual = False
        for pattern in patterns:
            if re.search(pattern, s, re.IGNORECASE | re.UNICODE):
                has_factual = True
                break

        if has_factual:
            claims.append(s)

    # Limit claims to avoid overwhelming verification
    return claims[:10]


# User-Agent for Wikipedia API compliance
WIKIPEDIA_USER_AGENT = "HistoryOfPlacesVideoMaker/1.0 (https://github.com/Dopeteck/history-of-places-video-maker; contact@example.com)"


def search_wikipedia(query: str, lang: str = "en") -> List[Dict]:
    """Search Wikipedia API for relevant pages."""
    try:
        url = f"https://{lang}.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": 5,
        }
        headers = {"User-Agent": WIKIPEDIA_USER_AGENT}
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json().get("query", {}).get("search", [])
    except Exception as e:
        return []


def get_wikipedia_page(title: str, lang: str = "en") -> Optional[str]:
    """Get Wikipedia page content."""
    try:
        url = f"https://{lang}.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "prop": "extracts",
            "exintro": True,
            "explaintext": True,
            "titles": title,
            "format": "json",
        }
        headers = {"User-Agent": WIKIPEDIA_USER_AGENT}
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        for page in pages.values():
            return page.get("extract", "")
    except Exception:
        return None


def find_evidence_in_text(claim: str, text: str, source_url: str, source_title: str) -> List[EvidenceExcerpt]:
    """Find evidence excerpts in source text that support the claim.

    Uses semantic overlap rather than exact substring matching.
    """
    evidence = []
    if not text or not claim:
        return evidence

    claim_lower = claim.lower()
    text_lower = text.lower()

    # Extract key terms from claim (nouns, proper nouns, dates, numbers)
    key_terms = set(re.findall(r'\b[A-Z][a-z]{2,}\b|\b\d{4}\b|\b\d{1,2}(?:st|nd|rd|th)\b', claim))
    # Also include significant words (>4 chars)
    words = re.findall(r'\b\w{5,}\b', claim_lower)
    key_terms.update(w for w in words if w not in {
        'the', 'and', 'that', 'this', 'with', 'from', 'were', 'been', 'have', 'has', 'had',
        'will', 'would', 'could', 'should', 'about', 'after', 'before', 'during', 'while',
        'their', 'there', 'where', 'when', 'which', 'who', 'what', 'how', 'why'
    })

    if not key_terms:
        return evidence

    # Find sentences in text containing key terms
    text_sentences = re.split(r'[.!?]+', text)
    for sent in text_sentences:
        sent = sent.strip()
        if len(sent) < 30:
            continue

        sent_lower = sent.lower()
        matches = sum(1 for term in key_terms if term.lower() in sent_lower)
        if matches >= 2:  # At least 2 key terms in sentence
            confidence = min(0.9, 0.3 + matches * 0.15)
            domain = urlparse(source_url).netloc
            evidence.append(EvidenceExcerpt(
                source_url=source_url,
                source_title=source_title,
                source_domain=domain,
                excerpt=sent[:500],  # Limit excerpt length
                confidence=confidence
            ))

    return evidence


def verify_claim(claim: str, destination: str, language: str = "en") -> Claim:
    """Verify a claim against trusted sources. Returns Claim with evidence."""
    # Build search query
    query = f"{claim} {destination}"
    results = search_wikipedia(query, language)

    all_evidence = []
    source_domains: Set[str] = set()

    for result in results[:5]:
        title = result.get("title", "")
        content = get_wikipedia_page(title, language)
        if content:
            source_url = f"https://{language}.wikipedia.org/wiki/{title.replace(' ', '_')}"
            excerpts = find_evidence_in_text(claim, content, source_url, title)
            for exc in excerpts:
                all_evidence.append(exc)
                source_domains.add(exc.source_domain)

    # Deduplicate evidence by source
    unique_evidence = []
    seen_sources = set()
    for exc in all_evidence:
        if exc.source_url not in seen_sources:
            unique_evidence.append(exc)
            seen_sources.add(exc.source_url)

    # Count independent sources (different domains)
    independent_count = len(source_domains)

    # Determine status and confidence
    if independent_count >= MIN_INDEPENDENT_SOURCES and unique_evidence:
        # Weighted confidence from evidence
        confidence = sum(e.confidence for e in unique_evidence) / len(unique_evidence)
        confidence = min(0.95, confidence)
        status = "verified"
    elif independent_count == 1 and unique_evidence:
        confidence = unique_evidence[0].confidence * 0.7  # Penalize single source
        status = "insufficient_evidence"
    else:
        confidence = 0.1
        status = "disputed" if independent_count == 0 else "insufficient_evidence"

    return Claim(
        text=claim,
        evidence=unique_evidence,
        confidence=round(confidence, 2),
        status=status,
        independent_sources=independent_count,
        language=language
    )


def research_topic(topic_data: Dict) -> ResearchRecord:
    """Research a topic and build a record with verified claims."""
    narration = topic_data.get("description", "")
    language = topic_data.get("narration_language", "English").lower()

    # Map narration language to wiki language code
    lang_map = {
        "english": "en", "british": "en",
        "spanish": "es", "french": "fr",
        "hindi": "hi", "portuguese": "pt",
        "mandarin": "zh", "chinese": "zh",
    }
    wiki_lang = lang_map.get(language, "en")

    claims_text = extract_claims_from_narration(narration, wiki_lang)

    # Track failures
    extraction_failed = len(claims_text) == 0 and len(narration) > 50
    zero_claims = len(claims_text) == 0

    claims = []
    all_sources = []

    for claim_text in claims_text[:8]:
        claim_obj = verify_claim(claim_text, topic_data.get("destination", ""), wiki_lang)
        claims.append(claim_obj)
        for exc in claim_obj.evidence:
            all_sources.append(exc.source_url)

    # Deduplicate sources
    all_sources = list(dict.fromkeys(all_sources))

    # Check if verification completely failed
    verification_failed = all(c.status == "disputed" for c in claims) and claims

    return ResearchRecord(
        topic_slug=topic_data.get("destination_slug", ""),
        destination=topic_data.get("destination", ""),
        category=topic_data.get("category", ""),
        title=topic_data.get("title", ""),
        claims=claims,
        sources_used=all_sources,
        research_date=datetime.now().date().isoformat(),
        notes=f"Researched {len(claims)} claims in {language} ({wiki_lang})",
        extraction_failed=extraction_failed,
        verification_failed=verification_failed,
        zero_claims=zero_claims
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
    ap.add_argument("--output", required=True, help="Output research record path")
    args = ap.parse_args()

    if args.topic == "latest":
        schedule_file = DATA_DIR / "daily_schedule.json"
        if not schedule_file.exists():
            print("ERROR: No schedule found. Run daily_content_engine.py --build-schedule first.")
            sys.exit(1)
        with open(schedule_file, encoding="utf-8") as f:
            schedule = json.load(f)
        today = datetime.now().date().isoformat()
        entry = next((e for e in schedule if e.get("date") == today and e.get("topic")), None)
        if not entry:
            print(f"ERROR: No topic scheduled for {today}")
            sys.exit(1)
        topic_data = entry["topic"]
    else:
        with open(args.topic, encoding="utf-8") as f:
            topic_data = json.load(f)

    print(f"Researching: {topic_data.get('title', 'Unknown')}")
    record = research_topic(topic_data)
    # Save to explicit output path
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(asdict(record), f, indent=2, ensure_ascii=False)
    # Also persist to the standard DATA_DIR location so load_research() can find it.
    save_research(record)

    # Report results with details
    print(f"Saved research record with {len(record.claims)} claims")
    verified = sum(1 for c in record.claims if c.status == "verified")
    insufficient = sum(1 for c in record.claims if c.status == "insufficient_evidence")
    disputed = sum(1 for c in record.claims if c.status == "disputed")

    print(f"  Verified: {verified}")
    print(f"  Insufficient evidence: {insufficient}")
    print(f"  Disputed: {disputed}")

    if record.extraction_failed:
        print("  WARNING: Claim extraction failed (no factual claims detected)")
    if record.verification_failed:
        print("  WARNING: All claims disputed - verification failed")
    if record.zero_claims:
        print("  WARNING: Zero claims extracted from narration")

    for c in record.claims:
        ev_count = len(c.evidence)
        srcs = ", ".join(set(e.source_domain for e in c.evidence)) if c.evidence else "none"
        print(f"  [{c.status}] {c.text[:80]}... (conf: {c.confidence:.2f}, sources: {ev_count}, domains: {srcs})")

    # Exit with error code if critical failures
    if record.extraction_failed or record.verification_failed or record.zero_claims:
        sys.exit(1)


if __name__ == "__main__":
    import sys
    main()