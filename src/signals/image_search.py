"""Reverse-image search through SerpAPI (AC-09).

No match is never treated as proof of originality, and a match is only a clue: the
context of each match is assessed later (Gemini, grounded) before it can score.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import requests

from ..errors import ProviderError
from ..schemas import ImageMatch, ImageSearchResult, ProviderStatus
from ..url_safety import validate_url
from ..utils import clean_text, error_category, registrable_domain

SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
MAX_MATCHES_PER_IMAGE = 6
MAX_MATCHES_TOTAL = 10
_JUNK = re.compile(r"(logo|icon|sprite|favicon|placeholder|pixel|badge|avatar|\.svg($|\?)|\.gif($|\?))", re.I)

HttpGet = Callable[..., Any]


def pick_images(image_urls: List[str], limit: int) -> List[str]:
    """Choose usable, publicly reachable image URLs (deduplicated, junk filtered)."""
    chosen: List[str] = []
    for url in image_urls:
        if len(chosen) >= limit:
            break
        if not isinstance(url, str) or url in chosen or url.lower().startswith("data:"):
            continue
        if _JUNK.search(url):
            continue
        try:
            validate_url(url, resolve=True)
        except Exception:
            continue
        chosen.append(url)
    return chosen


def _price_text(match: Dict[str, Any]) -> str:
    price = match.get("price")
    if isinstance(price, dict):
        return str(price.get("value") or price.get("extracted_value") or "")
    return str(price or "")


def parse_matches(payload: Dict[str, Any], image_url: str, listing_domain: str) -> List[ImageMatch]:
    raw = payload.get("visual_matches") or payload.get("image_results") or []
    listing_domain = registrable_domain(listing_domain)  # accept a host or a registrable domain
    matches: List[ImageMatch] = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "")
        scheme = urlsplit(link).scheme.lower()
        if scheme not in ("http", "https") or link in seen:
            continue
        domain = registrable_domain(link)
        if domain and domain == listing_domain:
            continue  # the listing's own pages are not evidence of copying
        seen.add(link)
        matches.append(
            ImageMatch(
                image_url=image_url,
                title=clean_text(item.get("title"), 300),
                source=clean_text(item.get("source") or item.get("displayed_link"), 120),
                link=link[:2048],
                domain=domain,
                snippet=clean_text(item.get("snippet"), 300),
                price_text=clean_text(_price_text(item), 60),
            )
        )
        if len(matches) >= MAX_MATCHES_PER_IMAGE:
            break
    return matches


def _query_one(image_url: str, api_key: str, engine: str, timeout: float, http_get: HttpGet) -> Dict[str, Any]:
    resp = http_get(
        SERPAPI_ENDPOINT,
        params={"engine": engine, "url": image_url, "api_key": api_key},
        timeout=timeout,
    )
    if getattr(resp, "status_code", 200) == 429:
        raise ProviderError("Rate limited", category="quota_or_rate_limit")
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        raise ProviderError("Unexpected payload", category="invalid_response")
    error = str(payload.get("error") or "")
    if error:
        low = error.lower()
        if "hasn't returned any results" in low or "no results" in low:
            return {}
        if "run out" in low or "limit" in low or "quota" in low or "plan" in low:
            raise ProviderError("Quota exhausted", category="quota_or_rate_limit")
        if "api key" in low or "invalid" in low:
            raise ProviderError("Credential problem", category="auth_error")
        raise ProviderError("Provider error", category="provider_error")
    return payload


def search_images(
    image_urls: List[str],
    listing_domain: str,
    *,
    api_key: Optional[str],
    engine: str = "google_lens",
    timeout: float = 15.0,
    max_images: int = 2,
    http_get: Optional[HttpGet] = None,
) -> Tuple[ImageSearchResult, ProviderStatus]:
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    if not api_key:
        return (
            ImageSearchResult(status="unavailable", reason="Image search is not configured (no SerpAPI key)."),
            ProviderStatus(provider="serpapi", status="skipped", detail="not_configured", elapsed_ms=elapsed()),
        )
    candidates = pick_images(image_urls, max_images)
    if not candidates:
        return (
            ImageSearchResult(status="unavailable", reason="No usable listing image was found to search."),
            ProviderStatus(provider="serpapi", status="skipped", detail="no_images", elapsed_ms=elapsed()),
        )
    getter: HttpGet = http_get or requests.get

    def one(image_url: str) -> Tuple[str, Optional[List[ImageMatch]], Optional[str]]:
        try:
            payload = _query_one(image_url, api_key, engine, timeout, getter)
            return image_url, parse_matches(payload, image_url, listing_domain), None
        except Exception as exc:
            return image_url, None, error_category(exc)

    with ThreadPoolExecutor(max_workers=max(len(candidates), 1)) as pool:
        outcomes = list(pool.map(one, candidates))

    matches: List[ImageMatch] = []
    errors: List[str] = []
    searched: List[str] = []
    for image_url, found, error in outcomes:
        if error is not None:
            errors.append(error)
            continue
        searched.append(image_url)
        matches.extend(found or [])
    matches = matches[:MAX_MATCHES_TOTAL]

    if not searched:
        category = errors[0] if errors else "unexpected_error"
        return (
            ImageSearchResult(status="error", reason=f"Image search failed ({category})."),
            ProviderStatus(
                provider="serpapi",
                status="timeout" if category == "timeout" else "error",
                detail=category,
                elapsed_ms=elapsed(),
            ),
        )
    detail = f"{len(matches)} match(es)" + (f"; {len(errors)} image(s) failed" if errors else "")
    return (
        ImageSearchResult(status="ok", searched_images=searched, matches=matches),
        ProviderStatus(provider="serpapi", status="ok", detail=detail, elapsed_ms=elapsed()),
    )
