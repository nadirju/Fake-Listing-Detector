"""Orchestrates independent providers and deterministic risk scoring."""
import os
from urllib.parse import urlparse
from .schemas import Listing, Signal, RedFlag
from .extractor import extract_listing
from .signals.domain_age import check_domain_age
from .signals.image_search import reverse_image_search
from .signals.listing_rules import collect_listing_signals
from .gemini_analyzer import analyze_with_gemini
from .scoring import build_result
from .cache import cache_key, get_cached, set_cached
from .url_safety import validate_url


def analyze_listing(url: str, manual: Listing | None = None, use_cache: bool = True):
    safe_url = validate_url(url, resolve_dns=(manual is None)) if url else (manual.listing_url if manual else "")
    key = cache_key({"url": safe_url, "manual": manual.model_dump() if manual else None})
    ttl = int(os.getenv("CACHE_TTL_SECONDS", "300"))
    if use_cache:
        cached = get_cached(key, ttl)
        if cached is not None:
            return cached
    unavailable = []
    if manual is not None:
        listing = manual
    else:
        try:
            listing = extract_listing(safe_url)
        except Exception as exc:
            unavailable.append(f"listing_extraction: {type(exc).__name__}")
            listing = Listing(listing_url=safe_url, domain=urlparse(safe_url).hostname or "", extraction_notes=[f"Extraction failed ({type(exc).__name__}). Use manual input."])
    domain_result = check_domain_age(listing.domain)
    if domain_result.get("status") != "available":
        unavailable.append("domain_age")
    image_result = reverse_image_search(listing.image_urls[0] if listing.image_urls else "")
    if image_result.get("status") != "available":
        unavailable.append("image_search")
    signals, flags = collect_listing_signals(listing, domain_result, image_result)
    # Report image matches without treating a match as proof or automatically scoring it.
    for match in image_result.get("matches", []):
        flags.append(RedFlag(title="Reverse-image result to review", explanation="A search provider returned a source associated with this image. Inspect the page context; stock images and manufacturer photos can appear on legitimate listings.", evidence=match.get("title", "Image result") + (": " + match.get("snippet", "") if match.get("snippet") else ""), confidence="low", source_url=match.get("url")))
    ai_summary, ai_status = analyze_with_gemini(listing, signals, flags)
    if ai_status != "available":
        unavailable.append("gemini_analysis")
    result = build_result(listing, signals, flags, unavailable, ai_summary, ai_status)
    package = {"listing": listing, "result": result, "domain_result": domain_result, "image_result": image_result}
    if use_cache:
        set_cached(key, package)
    return package
