#!/usr/bin/env python3
"""Multi-provider research pipeline for historical fact-checking.

Queries configured collection APIs (Wikipedia, Europeana, Smithsonian,
Library of Congress) for evidence supporting or refuting narration claims.

Each provider adapter returns ResearchSource objects plus a ProviderDiagnostic
recording status, HTTP status, elapsed time, result count and any sanitized
error. Provider failures (timeouts, HTTP errors, auth failures, invalid JSON)
are tracked as provider_failure, never conflated with insufficient evidence.

API keys are read from environment variables only and are never logged.
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Set
from urllib.parse import urlencode, urlparse

import requests

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ResearchSource:
    """A piece of evidence returned by a research provider."""
    provider: str          # e.g. "Wikipedia", "Europeana", "Smithsonian", "Library of Congress"
    title: str
    url: str
    excerpt: str           # Relevant text excerpt describing the entity/event
    date_text: str         # Publication/date info if available
    query: str             # The search query that found this source
    status: str = "success"   # "success", "failed", "skipped"
    error: Optional[str] = None  # Sanitized error message (no credentials)
    http_status: Optional[int] = None


@dataclass
class ProviderDiagnostic:
    """Auditable record of a single provider request."""
    provider: str
    endpoint: str
    query: str
    status: str = "success"          # "success", "failed", "skipped"
    http_status: Optional[int] = None
    elapsed_ms: float = 0.0
    result_count: int = 0
    error: Optional[str] = None  # Sanitized, no credentials


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WIKIPEDIA_USER_AGENT = "HistoryOfPlacesVideoMaker/2.2 (https://github.com/Dopeteck/history-of-places-video-maker; contact@example.com)"

# Providers that need no API key (always attempted)
NO_KEY_PROVIDERS = ["wikipedia", "loc"]

# Providers that require an API key from environment
KEYED_PROVIDERS = {
    "europeana": "EUROPEANA_API_KEY",
    "smithsonian": "SMITHSONIAN_API_KEY",
}

# All research providers in priority order. Wikipedia first for broad discovery,
# then collection providers for specific historical evidence.
RESEARCH_PROVIDER_ORDER = ["wikipedia", "loc", "europeana", "smithsonian"]

# Request timeouts per provider (connect, read) in seconds
PROVIDER_TIMEOUTS = {
    "wikipedia": (10.0, 30.0),
    "loc": (15.0, 45.0),
    "europeana": (15.0, 45.0),
    "smithsonian": (15.0, 45.0),
}


def _redact(text: str) -> str:
    """Strip anything that looks like an API key or secret from text before logging."""
    if not text:
        return text
    # Redact common key patterns: key=XXXX or apikey=XXXX or wskey=XXXX
    text = re.sub(r'(api[_-]?key|apikey|wskey)=[A-Za-z0-9_\-]{5,}', r'\1=***REDACTED***', text, flags=re.I)
    return text


def _norm(text: str) -> str:
    """Lower-case and strip diacritics for matching."""
    t = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in t if not unicodedata.combining(ch)).lower()


def _clean_html(value: str) -> str:
    """Strip HTML tags and entities from provider text."""
    import html as _html
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()


def _key_terms(claim: str) -> Set[str]:
    """Extract significant key terms from a claim for evidence matching."""
    terms: Set[str] = set()
    # Proper nouns and dates
    for m in re.findall(r'\b[A-Z][a-z]{2,}\b|\b\d{4}\b|\b\d{1,2}(?:st|nd|rd|th)\b', claim):
        terms.add(_norm(m))
    # Significant words (>4 chars, not stopwords)
    stop = {
        'the', 'and', 'that', 'this', 'with', 'from', 'were', 'been', 'have', 'has', 'had',
        'will', 'would', 'could', 'should', 'about', 'after', 'before', 'during', 'while',
        'their', 'there', 'where', 'when', 'which', 'who', 'what', 'how', 'why', 'been',
        'historical', 'historic', 'history', 'photograph', 'photo', 'illustration', 'old',
        'image', 'images', 'panorama', 'period', 'early', 'modern', 'contemporary',
    }
    for w in re.findall(r'\b\w{5,}\b', claim.lower()):
        if w not in stop:
            terms.add(_norm(w))
    return terms


def _contradiction_indicators(text: str) -> List[str]:
    """Return negation/contradiction words found in text."""
    return [w for w in re.findall(r'\b(not|never|no|nor|neither|cannot|cannot|did not|was not|were not|is not|are not|unrelated|false|myth|contradict|refute|debunk)\b', text.lower())]


# ---------------------------------------------------------------------------
# Provider adapters
# ---------------------------------------------------------------------------


def _http_get(url: str, params: Optional[dict] = None, timeout: Tuple[float, float] = (10.0, 30.0),
              headers: Optional[dict] = None) -> requests.Response:
    """Perform a GET request with consistent timeouts and user-agent."""
    h = {"User-Agent": WIKIPEDIA_USER_AGENT}
    if headers:
        h.update(headers)
    return requests.get(url, params=params, headers=h, timeout=timeout)


def provider_wikipedia(claim: str, destination: str, language: str = "en") -> Tuple[List[ResearchSource], ProviderDiagnostic]:
    """Query Wikipedia search + page extract for evidence."""
    diag = ProviderDiagnostic(provider="Wikipedia", endpoint="wikipedia.org/w/api.php", query=f"{claim} {destination}")
    sources: List[ResearchSource] = []
    start = time.time()
    page_extract_ok = 0
    page_extract_fail = 0
    try:
        r = _http_get(
            f"https://{language}.wikipedia.org/w/api.php",
            {"action": "query", "list": "search", "srsearch": f"{claim} {destination}",
             "format": "json", "srlimit": 5},
            timeout=PROVIDER_TIMEOUTS["wikipedia"],
        )
        diag.http_status = r.status_code
        r.raise_for_status()
        results = r.json().get("query", {}).get("search", [])
        diag.result_count = len(results)

        for result in results[:5]:
            title = result.get("title", "")
            content = _fetch_wikipedia_page(title, language)
            if content:
                page_extract_ok += 1
                snippet = _clean_html(result.get("snippet", ""))
                excerpt = snippet or content[:400]
                sources.append(ResearchSource(
                    provider="Wikipedia",
                    title=title,
                    url=f"https://{language}.wikipedia.org/wiki/{title.replace(' ', '_')}",
                    excerpt=excerpt,
                    date_text="",
                    query=diag.query,
                ))
            else:
                page_extract_fail += 1
        if page_extract_ok > 0:
            diag.status = "success"
        elif results:
            # Search succeeded but no page extracts could be fetched.
            diag.status = "failed"
            diag.error = "page extract unavailable"
        else:
            diag.status = "success"  # zero results is not a failure
    except requests.HTTPError as e:
        diag.status = "failed"
        diag.http_status = getattr(getattr(e, "response", None), "status_code", None)
        diag.error = _redact(f"HTTP {diag.http_status}")
    except requests.Timeout:
        diag.status = "failed"
        diag.error = "timeout"
    except Exception as e:  # noqa: BLE001 - record any provider failure
        diag.status = "failed"
        diag.error = _redact(f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        diag.elapsed_ms = (time.time() - start) * 1000.0
    return sources, diag


def _fetch_wikipedia_page(title: str, language: str = "en") -> Optional[str]:
    """Fetch Wikipedia page extract text."""
    try:
        r = _http_get(
            f"https://{language}.wikipedia.org/w/api.php",
            {"action": "query", "prop": "extracts", "exintro": True, "explaintext": True,
             "titles": title, "format": "json"},
            timeout=PROVIDER_TIMEOUTS["wikipedia"],
        )
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        for page in pages.values():
            return page.get("extract", "")
    except Exception:
        return None
    return None


def provider_loc(claim: str, destination: str) -> Tuple[List[ResearchSource], ProviderDiagnostic]:
    """Query Library of Congress search API for evidence."""
    diag = ProviderDiagnostic(provider="Library of Congress", endpoint="www.loc.gov/search/",
                              query=f"{claim} {destination}")
    sources: List[ResearchSource] = []
    start = time.time()
    try:
        r = _http_get(
            "https://www.loc.gov/search/",
            {"q": f"{claim} {destination}", "fo": "json", "c": 10, "sp": 1},
            timeout=PROVIDER_TIMEOUTS["loc"],
        )
        diag.http_status = r.status_code
        r.raise_for_status()
        payload = r.json()
        results = payload.get("results", [])
        diag.result_count = len(results)
        for x in results[:5]:
            title = x.get("title", "")
            desc = " ".join(x.get("description") or []) if isinstance(x.get("description"), list) else str(x.get("description", ""))
            url = x.get("url") or x.get("id") or ""
            if url.startswith("http://"):
                url = "https://" + url[len("http://"):]
            date_text = " ".join(x.get("date") or []) if isinstance(x.get("date"), list) else str(x.get("date", ""))
            sources.append(ResearchSource(
                provider="Library of Congress",
                title=title,
                url=url,
                excerpt=_clean_html(desc)[:400],
                date_text=date_text,
                query=diag.query,
            ))
        diag.status = "success"
    except requests.HTTPError as e:
        # loc.gov returns 404 for "no results" - that's not an outage.
        if getattr(getattr(e, "response", None), "status_code", None) == 404:
            diag.status = "success"
            diag.result_count = 0
        else:
            diag.status = "failed"
            diag.http_status = getattr(getattr(e, "response", None), "status_code", None)
            diag.error = _redact(f"HTTP {diag.http_status}")
    except requests.Timeout:
        diag.status = "failed"
        diag.error = "timeout"
    except Exception as e:  # noqa: BLE001
        diag.status = "failed"
        diag.error = _redact(f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        diag.elapsed_ms = (time.time() - start) * 1000.0
    return sources, diag


def provider_europeana(claim: str, destination: str) -> Tuple[List[ResearchSource], ProviderDiagnostic]:
    """Query Europeana API for collection evidence."""
    key = os.getenv("EUROPEANA_API_KEY")
    if not key:
        diag = ProviderDiagnostic(provider="Europeana", endpoint="api.europeana.eu", query=f"{claim} {destination}")
        diag.status = "skipped"
        diag.error = "EUROPEANA_API_KEY not configured"
        return [], diag

    diag = ProviderDiagnostic(provider="Europeana", endpoint="api.europeana.eu", query=f"{claim} {destination}")
    sources: List[ResearchSource] = []
    start = time.time()
    try:
        r = _http_get(
            "https://api.europeana.eu/record/v2/search.json",
            {"wskey": key, "query": f"{claim} {destination}", "media": "true",
             "reusability": "open", "rows": 10, "profile": "rich"},
            timeout=PROVIDER_TIMEOUTS["europeana"],
            headers={"wskey": key},  # Europeana expects key in query; also send header for safety
        )
        diag.http_status = r.status_code
        r.raise_for_status()
        payload = r.json()
        items = payload.get("items", [])
        diag.result_count = len(items)
        for x in items[:5]:
            title = (x.get("title") or [""])[0] if isinstance(x.get("title"), list) else str(x.get("title", ""))
            desc = x.get("dcDescription") or []
            if isinstance(desc, list):
                desc = " ".join(desc)
            subjects = x.get("dcSubject") or []
            if isinstance(subjects, list):
                subjects = " ".join(subjects)
            creators = x.get("dcCreator") or []
            if isinstance(creators, list):
                creators = ", ".join(creators)
            shown_at = x.get("edmIsShownAt") or []
            url = shown_at[0] if isinstance(shown_at, list) and shown_at else str(shown_at or "")
            dates = x.get("year") or x.get("dcDate") or ""
            date_text = " ".join(dates) if isinstance(dates, list) else str(dates)
            excerpt = _clean_html(desc) or _clean_html(subjects)
            if creators:
                excerpt = f"{creators}: {excerpt}" if excerpt else creators
            sources.append(ResearchSource(
                provider="Europeana",
                title=title,
                url=url,
                excerpt=excerpt[:400],
                date_text=date_text,
                query=diag.query,
            ))
        diag.status = "success"
    except requests.HTTPError as e:
        diag.status = "failed"
        diag.http_status = getattr(getattr(e, "response", None), "status_code", None)
        diag.error = _redact(f"HTTP {diag.http_status}")
    except requests.Timeout:
        diag.status = "failed"
        diag.error = "timeout"
    except Exception as e:  # noqa: BLE001
        diag.status = "failed"
        diag.error = _redact(f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        diag.elapsed_ms = (time.time() - start) * 1000.0
    return sources, diag


def provider_smithsonian(claim: str, destination: str) -> Tuple[List[ResearchSource], ProviderDiagnostic]:
    """Query Smithsonian Open Access API for collection evidence."""
    key = os.getenv("SMITHSONIAN_API_KEY")
    if not key:
        diag = ProviderDiagnostic(provider="Smithsonian", endpoint="api.si.edu", query=f"{claim} {destination}")
        diag.status = "skipped"
        diag.error = "SMITHSONIAN_API_KEY not configured"
        return [], diag

    diag = ProviderDiagnostic(provider="Smithsonian", endpoint="api.si.edu", query=f"{claim} {destination}")
    sources: List[ResearchSource] = []
    start = time.time()
    try:
        r = _http_get(
            "https://api.si.edu/openaccess/api/v1.0/search",
            {"api_key": key, "q": f"{claim} {destination}", "rows": 10},
            timeout=PROVIDER_TIMEOUTS["smithsonian"],
        )
        diag.http_status = r.status_code
        r.raise_for_status()
        rows = r.json().get("response", {}).get("rows", [])
        diag.result_count = len(rows)
        for x in rows[:5]:
            cdata = x.get("content", {})
            desc = cdata.get("descriptiveNonRepeating", {})
            title = (desc.get("title") or {}).get("content") or x.get("title", "")
            record_link = desc.get("record_link") or ""
            date_text = desc.get("date") or ""
            if isinstance(date_text, dict):
                date_text = date_text.get("content", "")
            sources.append(ResearchSource(
                provider="Smithsonian",
                title=str(title),
                url=record_link,
                excerpt=_clean_html(str(desc))[:400],
                date_text=str(date_text),
                query=diag.query,
            ))
        diag.status = "success"
    except requests.HTTPError as e:
        diag.status = "failed"
        diag.http_status = getattr(getattr(e, "response", None), "status_code", None)
        diag.error = _redact(f"HTTP {diag.http_status}")
    except requests.Timeout:
        diag.status = "failed"
        diag.error = "timeout"
    except Exception as e:  # noqa: BLE001
        diag.status = "failed"
        diag.error = _redact(f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        diag.elapsed_ms = (time.time() - start) * 1000.0
    return sources, diag


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDER_ADAPTERS = {
    "wikipedia": provider_wikipedia,
    "loc": provider_loc,
    "europeana": provider_europeana,
    "smithsonian": provider_smithsonian,
}


def active_providers() -> List[str]:
    """Return the list of providers that can run (keyed providers only if key present)."""
    active = []
    for p in RESEARCH_PROVIDER_ORDER:
        if p in KEYED_PROVIDERS:
            if os.getenv(KEYED_PROVIDERS[p]):
                active.append(p)
        else:
            active.append(p)
    return active


# ---------------------------------------------------------------------------
# Evidence matching
# ---------------------------------------------------------------------------


def _supports_claim(claim: str, excerpt: str) -> Tuple[bool, float, List[str]]:
    """Determine whether an excerpt supports a claim.

    Returns (supports, confidence, matched_terms).
    Requires at least 2 key terms from the claim to appear in the excerpt,
    and no strong contradiction indicators in the excerpt.
    """
    terms = _key_terms(claim)
    if not terms:
        return False, 0.0, []

    excerpt_norm = _norm(excerpt)
    matched = [t for t in terms if t in excerpt_norm]

    # Need at least 2 key terms to consider it relevant support
    if len(matched) < 2:
        return False, 0.0, matched

    # Contradiction check: negation words near matched terms reduce confidence
    contradictions = _contradiction_indicators(excerpt)
    if contradictions:
        # If the excerpt explicitly negates key terms, it may contradict
        return False, 0.0, matched

    # Base confidence on how many key terms matched
    confidence = min(0.9, 0.4 + len(matched) * 0.1)
    return True, confidence, matched


def _contradicts_claim(claim: str, excerpt: str) -> bool:
    """Determine whether an excerpt contradicts a claim."""
    terms = _key_terms(claim)
    if not terms:
        return False
    excerpt_norm = _norm(excerpt)
    matched = [t for t in terms if t in excerpt_norm]
    if len(matched) < 2:
        return False
    # Contradiction indicators present alongside matched terms
    return len(_contradiction_indicators(excerpt)) > 0


# ---------------------------------------------------------------------------
# Claim verification
# ---------------------------------------------------------------------------


@dataclass
class VerificationResult:
    """Result of verifying a single claim across providers."""
    claim: str
    status: str              # "verified", "insufficient_evidence", "disputed", "provider_failure"
    confidence: float
    evidence: List[ResearchSource] = field(default_factory=list)
    independent_sources: int = 0
    provider_diagnostics: List[ProviderDiagnostic] = field(default_factory=list)
    provider_failures: List[str] = field(default_factory=list)  # provider names that failed


def verify_claim(claim: str, destination: str, language: str = "en") -> VerificationResult:
    """Verify a claim across multiple research providers.

    Returns a VerificationResult with status, evidence, and diagnostics.
    Provider failures are tracked separately from evidence insufficiency.
    """
    result = VerificationResult(claim=claim, status="provider_failure", confidence=0.0)
    all_sources: List[ResearchSource] = []
    supporting: List[ResearchSource] = []
    contradicting: List[ResearchSource] = []
    domains: Set[str] = set()
    any_provider_success = False

    providers = active_providers()
    if not providers:
        result.status = "provider_failure"
        result.provider_failures = ["no_providers_available"]
        return result

    for provider_name in providers:
        # Look up from globals() so that module-level patches (e.g. in tests)
        # take effect; fall back to PROVIDER_ADAPTERS dict for direct calls.
        adapter = globals().get(f"provider_{provider_name}") or PROVIDER_ADAPTERS.get(provider_name)
        if not adapter:
            continue
        try:
            sources, diag = adapter(claim, destination, language)
        except Exception as e:  # noqa: BLE001 - adapter must never crash verification
            diag = ProviderDiagnostic(
                provider=provider_name,
                endpoint="unknown",
                query=f"{claim} {destination}",
                status="failed",
                error=_redact(f"{type(e).__name__}: {str(e)[:200]}"),
            )
            sources = []
        result.provider_diagnostics.append(diag)

        if diag.status == "skipped":
            continue
        if diag.status == "failed":
            result.provider_failures.append(provider_name)
            continue

        any_provider_success = True
        for src in sources:
            all_sources.append(src)
            supports, conf, matched = _supports_claim(claim, src.excerpt)
            if supports:
                supporting.append(src)
                domains.add(urlparse(src.url).netloc if src.url else src.provider)
            elif _contradicts_claim(claim, src.excerpt):
                contradicting.append(src)

    # Determine status
    if contradicting:
        result.status = "disputed"
        result.confidence = 0.2
        result.evidence = contradicting
        result.independent_sources = len({urlparse(s.url).netloc if s.url else s.provider for s in contradicting})
    elif supporting:
        result.status = "verified"
        # Confidence based on source diversity and match strength
        best_conf = max((s for s in supporting), key=lambda s: 0.5).__class__  # noqa - placeholder
        result.confidence = min(0.95, 0.5 + len(supporting) * 0.1 + len(domains) * 0.1)
        result.evidence = supporting
        result.independent_sources = len(domains)
    elif any_provider_success:
        # Providers worked but found no supporting evidence
        result.status = "insufficient_evidence"
        result.confidence = 0.1
        result.evidence = all_sources
        result.independent_sources = 0
    else:
        # All providers failed
        result.status = "provider_failure"
        result.confidence = 0.0
        result.evidence = []
        result.independent_sources = 0

    return result


# ---------------------------------------------------------------------------
# Convenience: research_topic replacement
# ---------------------------------------------------------------------------


def research_topic(topic_data: Dict) -> Dict:
    """Research a topic and return a dict-compatible research record.

    This is a drop-in replacement for the research_factcheck.research_topic
    that uses multiple providers and records provider diagnostics.
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

    # Extract claims (simple sentence split for now; can import from research_factcheck)
    claims_text = _extract_claims(narration, wiki_lang)

    extraction_failed = len(claims_text) == 0 and len(narration) > 50
    zero_claims = len(claims_text) == 0

    claim_results = []
    all_sources = []
    all_diagnostics = []
    all_provider_failures = []

    for claim_text in claims_text[:8]:
        vr = verify_claim(claim_text, topic_data.get("destination", ""), wiki_lang)
        claim_results.append({
            "text": vr.claim,
            "status": vr.status,
            "confidence": vr.confidence,
            "evidence": [asdict(s) for s in vr.evidence],
            "independent_sources": vr.independent_sources,
            "provider_diagnostics": [asdict(d) for d in vr.provider_diagnostics],
            "provider_failures": vr.provider_failures,
        })
        all_sources.extend(s.url for s in vr.evidence if s.url)
        all_diagnostics.extend(vr.provider_diagnostics)
        all_provider_failures.extend(vr.provider_failures)

    # Deduplicate sources
    all_sources = list(dict.fromkeys(all_sources))

    # Verification failure: all providers failed for all claims
    verification_failed = all(c["status"] == "provider_failure" for c in claim_results) and claim_results

    return {
        "topic_slug": topic_data.get("destination_slug", ""),
        "destination": topic_data.get("destination", ""),
        "category": topic_data.get("category", ""),
        "title": topic_data.get("title", ""),
        "claims": claim_results,
        "sources_used": all_sources,
        "research_date": datetime.now().date().isoformat(),
        "notes": f"Researched {len(claim_results)} claims across {len(active_providers())} providers",
        "extraction_failed": extraction_failed,
        "verification_failed": verification_failed,
        "zero_claims": zero_claims,
        "provider_diagnostics": [asdict(d) for d in all_diagnostics],
        "provider_failures": list(dict.fromkeys(all_provider_failures)),
    }


def _extract_claims(narration: str, language: str = "en") -> List[str]:
    """Extract factual claims from narration text."""
    if not narration or len(narration.strip()) < 50:
        return []
    patterns = [
        r'\b\d{4}\b',
        r'\b\d{1,2}(?:st|nd|rd|th)\b',
        r'\b[A-Z][a-z]+ [A-Z][a-z]+\b',
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
