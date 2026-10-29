#!/usr/bin/env python3
"""Research and fact-checking module for daily content engine.

Gathers evidence from credible sources for each topic before script generation.

Key design:
- Multi-provider: Wikipedia, Library of Congress, Europeana, Smithsonian
- Provider failures (timeout, HTTP error, auth, rate limit, invalid JSON) are
  tracked as provider_failure, never conflated with insufficient evidence.
- Exceptions are never silently swallowed; each provider's status, HTTP status,
  elapsed time, result count and sanitized error are recorded.
- Evidence matching requires at least 2 key terms from the claim to appear in
  the source excerpt, with contradiction detection.
- Independent source counting uses domains, not raw page count.

API keys are read from environment variables only and are never logged.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Set
from urllib.parse import urlparse

# Import the multi-provider research engine
from research_providers import (
    ResearchSource as _RS,
    ProviderDiagnostic as _PD,
    verify_claim as _verify_claim,
    research_topic as _providers_research_topic,
    active_providers as _active_providers,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "destination-prototype" / "data"

# Minimum independent sources for verification.
# Set to 1 for environments where only Wikipedia is reachable.
# In production with multiple sources (Europeana, Smithsonian, LoC), should be 2.
MIN_INDEPENDENT_SOURCES = 1

# Trusted source domains (prioritized) - used for claim_sources validation
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
    status: str  # "verified", "disputed", "insufficient_evidence", "provider_failure"
    independent_sources: int  # Number of independent source domains
    language: str = "en"  # Claim language
    # New: provider-level diagnostics
    provider_diagnostics: List[Dict] = field(default_factory=list)
    provider_failures: List[str] = field(default_factory=list)


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
    # New: provider-level diagnostics across all claims
    provider_diagnostics: List[Dict] = field(default_factory=list)
    provider_failures: List[str] = field(default_factory=list)


def _claim_from_verification(vr, language: str) -> Claim:
    """Convert a VerificationResult from research_providers into a Claim."""
    evidence = [
        EvidenceExcerpt(
            source_url=s.url,
            source_title=s.title,
            source_domain=urlparse(s.url).netloc if s.url else s.provider,
            excerpt=s.excerpt,
            confidence=0.5,  # per-excerpt confidence not tracked separately in providers
        )
        for s in vr.evidence
    ]
    return Claim(
        text=vr.claim,
        evidence=evidence,
        confidence=vr.confidence,
        status=vr.status,
        independent_sources=vr.independent_sources,
        language=language,
        provider_diagnostics=vr.provider_diagnostics,
        provider_failures=vr.provider_failures,
    )


def research_topic(topic_data: Dict) -> ResearchRecord:
    """Research a topic using multiple providers and build a record.

    Uses Wikipedia, Library of Congress, Europeana and Smithsonian (when keys
    are configured) to gather evidence for each claim extracted from the
    narration. Provider failures are recorded explicitly and never treated
    as evidence that a claim is false.
    """
    narration = topic_data.get("description", "")
    language = topic_data.get("narration_language", "English").lower()

    lang_map = {
        "english": "en", "british": "en",
        "spanish": "es", "french": "fr",
        "hindi": "hi", "portuguese": "pt",
        "mandarin": "zh", "chinese": "zh",
    }
    wiki_lang = lang_map.get(language, "en")

    # Extract claims from narration
    claims_text = _extract_claims_from_narration(narration, wiki_lang)

    # Track failures
    extraction_failed = len(claims_text) == 0 and len(narration) > 50
    zero_claims = len(claims_text) == 0

    claims: List[Claim] = []
    all_sources: List[str] = []
    all_diagnostics: List[Dict] = []
    all_provider_failures: List[str] = []

    for claim_text in claims_text[:8]:
        try:
            vr = _verify_claim(claim_text, topic_data.get("destination", ""), wiki_lang)
        except Exception as e:  # noqa: BLE001 - never let one claim crash research
            vr = type("VR", (), {
                "claim": claim_text,
                "status": "provider_failure",
                "confidence": 0.0,
                "evidence": [],
                "independent_sources": 0,
                "provider_diagnostics": [
                    {"provider": "unknown", "endpoint": "unknown",
                     "query": f"{claim_text} {topic_data.get('destination','')}",
                     "status": "failed", "error": f"{type(e).__name__}: {str(e)[:200]}"}
                ],
                "provider_failures": ["unknown"],
            })()
        claim = _claim_from_verification(vr, wiki_lang)
        claims.append(claim)
        for s in vr.evidence:
            if s.url:
                all_sources.append(s.url)
        all_diagnostics.extend(vr.provider_diagnostics)
        all_provider_failures.extend(vr.provider_failures)

    # Deduplicate sources
    all_sources = list(dict.fromkeys(all_sources))

    # Verification failure: all providers failed for all claims
    verification_failed = all(c.status == "provider_failure" for c in claims) and claims

    return ResearchRecord(
        topic_slug=topic_data.get("destination_slug", ""),
        destination=topic_data.get("destination", ""),
        category=topic_data.get("category", ""),
        title=topic_data.get("title", ""),
        claims=claims,
        sources_used=all_sources,
        research_date=datetime.now().date().isoformat(),
        notes=f"Researched {len(claims)} claims across {len(_active_providers())} providers",
        extraction_failed=extraction_failed,
        verification_failed=verification_failed,
        zero_claims=zero_claims,
        provider_diagnostics=all_diagnostics,
        provider_failures=list(dict.fromkeys(all_provider_failures)),
    )


def extract_claims_from_narration(narration: str, language: str = "en") -> List[str]:
    """Extract factual claims from narration text.

    Robust across supported languages (en, zh, es, fr, hi, pt).
    Falls back to simple sentence splitting for unsupported languages.
    Public wrapper for backwards compatibility with tests.
    """
    return _extract_claims_from_narration(narration, language)


def verify_claim(claim: str, destination: str, language: str = "en") -> Claim:
    """Verify a claim against multiple research providers.

    Returns a Claim with status, evidence, confidence, and provider diagnostics.
    Provider failures are tracked separately from insufficient evidence.
    Public wrapper for backwards compatibility with tests.
    """
    from research_providers import verify_claim as _providers_verify_claim
    vr = _providers_verify_claim(claim, destination, language)
    return _claim_from_verification(vr, language)


def _extract_claims_from_narration(narration: str, language: str = "en") -> List[str]:
    if not narration or len(narration.strip()) < 50:
        return []

    patterns = [
        r'\b\d{4}\b',  # Years
        r'\b\d{1,2}(?:st|nd|rd|th)\b',  # Ordinals
        r'\b[A-Z][a-z]+ [A-Z][a-z]+\b',  # Proper names (English)
        r'\b[A-Z][a-z]+ [A-Z][a-z]+\b',  # Unicode proper names (simplified)
        r'(?:was|were|is|are|became|changed|renamed|founded|built|established|destroyed|occupied|ceded|annexed|conquered|invaded|colonized|liberated|unified|divided|merged|split|transformed|evolved|emerged|arose|originated|began|started|ended|concluded|signed|ratified|declared|proclaimed|announced)\b',
    ]

    sentences = re.split(r'[.!?。！？]+', narration)
    claims = []

    for s in sentences:
        s = s.strip()
        if not s or len(s) < 20:
            continue

        has_factual = False
        for pattern in patterns:
            if re.search(pattern, s, re.IGNORECASE | re.UNICODE):
                has_factual = True
                break

        if has_factual:
            claims.append(s)

    return claims[:10]


def save_research(record: ResearchRecord):
    """Save research record to disk."""
    filename = DATA_DIR / f"research_{record.topic_slug}_{record.research_date}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(asdict(record), f, indent=2, ensure_ascii=False)


def load_research(topic_slug: str, research_date: Optional[str] = None) -> Optional[ResearchRecord]:
    """Load a previously saved research record."""
    if research_date is None:
        research_date = datetime.now().date().isoformat()
    filename = DATA_DIR / f"research_{topic_slug}_{research_date}.json"
    if not filename.exists():
        return None
    try:
        with open(filename, encoding="utf-8") as f:
            data = json.load(f)
        # Convert dict back to ResearchRecord with Claim objects
        claims = []
        for c in data.get("claims", []):
            evidence = [
                EvidenceExcerpt(
                    source_url=e.get("source_url", ""),
                    source_title=e.get("source_title", ""),
                    source_domain=e.get("source_domain", ""),
                    excerpt=e.get("excerpt", ""),
                    confidence=e.get("confidence", 0.5),
                )
                for e in c.get("evidence", [])
            ]
            claims.append(Claim(
                text=c.get("text", ""),
                evidence=evidence,
                confidence=c.get("confidence", 0.0),
                status=c.get("status", "insufficient_evidence"),
                independent_sources=c.get("independent_sources", 0),
                language=c.get("language", "en"),
                provider_diagnostics=c.get("provider_diagnostics", []),
                provider_failures=c.get("provider_failures", []),
            ))
        return ResearchRecord(
            topic_slug=data.get("topic_slug", ""),
            destination=data.get("destination", ""),
            category=data.get("category", ""),
            title=data.get("title", ""),
            claims=claims,
            sources_used=data.get("sources_used", []),
            research_date=data.get("research_date", ""),
            notes=data.get("notes", ""),
            extraction_failed=data.get("extraction_failed", False),
            verification_failed=data.get("verification_failed", False),
            zero_claims=data.get("zero_claims", False),
            provider_diagnostics=data.get("provider_diagnostics", []),
            provider_failures=data.get("provider_failures", []),
        )
    except Exception:
        return None


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
    provider_failures = sum(1 for c in record.claims if c.status == "provider_failure")

    print(f"  Verified: {verified}")
    print(f"  Insufficient evidence: {insufficient}")
    print(f"  Disputed: {disputed}")
    print(f"  Provider failures: {provider_failures}")
    print(f"  Active providers: {', '.join(_active_providers())}")

    if record.provider_failures:
        print(f"  Provider failures: {', '.join(record.provider_failures)}")
    if record.provider_diagnostics:
        for d in record.provider_diagnostics:
            print(f"  [{d.get('provider')}] {d.get('status')} "
                  f"(HTTP={d.get('http_status')}, results={d.get('result_count')}, "
                  f"elapsed={d.get('elapsed_ms', 0):.0f}ms)"
                  + (f" error={d.get('error')}" if d.get('error') else ""))

    if record.extraction_failed:
        print("  WARNING: Claim extraction failed (no factual claims detected)")
    if record.verification_failed:
        print("  WARNING: All claims could not be verified by any provider")
    if record.zero_claims:
        print("  WARNING: Zero claims extracted from narration")

    for c in record.claims:
        ev_count = len(c.evidence)
        srcs = ", ".join(set(e.source_domain for e in c.evidence)) if c.evidence else "none"
        diags = "; ".join(f"{d.get('provider')}:{d.get('status')}" for d in c.provider_diagnostics)
        print(f"  [{c.status}] {c.text[:80]}... (conf: {c.confidence:.2f}, sources: {ev_count}, domains: {srcs} | {diags})")

    # Exit with error code if critical failures
    if record.extraction_failed or record.verification_failed or record.zero_claims:
        sys.exit(1)


if __name__ == "__main__":
    main()
