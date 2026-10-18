"""Listing extraction (AC-02, AC-03).

Providers (all produce the same `Listing` model; page content is untrusted data):
  * direct    - our own SSRF-safe fetch + JSON-LD / OpenGraph / HTML parsing
  * firecrawl - Firecrawl scrape API (needs FIRECRAWL_API_KEY)
  * jina      - Jina Reader (works without a key at a low rate limit)

Providers are tried in the configured order until the listing is "sufficient"
(title + at least two of price/description/seller/images). A site that blocks
automated access is reported as such - we never try to bypass CAPTCHAs or
anti-bot protections.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, FeatureNotFound

from .config import Settings
from .errors import (
    BlockedByPageError,
    FetchError,
    InvalidInputError,
    ProviderError,
    UnresolvableHostError,
    UnsafeURLError,
)
from .schemas import Listing, ManualInput, ProviderStatus
from .url_safety import safe_get, validate_url
from .utils import clean_text, error_category, run_with_timeout

MIN_FULL_PAGE_CHARS = 800
PAGE_TEXT_LIMIT = 20000

# --------------------------------------------------------------------------- #
# Price parsing
# --------------------------------------------------------------------------- #

CURRENCY_MAP = {
    "$": "USD", "us$": "USD", "usd": "USD", "£": "GBP", "gbp": "GBP", "€": "EUR", "eur": "EUR",
    "₹": "INR", "inr": "INR", "rs": "PKR", "rs.": "PKR", "₨": "PKR", "pkr": "PKR", "aed": "AED",
    "cad": "CAD", "ca$": "CAD", "aud": "AUD", "au$": "AUD",
}
_PRICE_RE = re.compile(
    r"(?P<cur>US\$|CA\$|AU\$|\$|£|€|₹|₨|Rs\.?|PKR|USD|EUR|GBP|INR|AED|CAD|AUD)\s?(?P<amt>\d[\d,]*(?:\.\d{1,2})?)",
    re.IGNORECASE,
)


def parse_amount(value: Any) -> Optional[float]:
    """Parse '1,299.00', '1.299,00', '12,50', 49.9 ... into a positive float."""
    if value is None:
        return None
    s = re.sub(r"[^\d,.]", "", str(value))
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        if len(tail) == 3 and head:
            s = s.replace(",", "")
        elif len(tail) in (1, 2):
            s = head.replace(",", "") + "." + tail
        else:
            s = s.replace(",", "")
    try:
        number = float(s)
    except ValueError:
        return None
    return number if 0 < number < 1e9 else None


def find_price(text: str) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    """First currency-prefixed amount in `text` -> (value, ISO currency, matched text)."""
    for match in _PRICE_RE.finditer(text or ""):
        amount = parse_amount(match.group("amt"))
        if amount is not None:
            currency = CURRENCY_MAP.get(match.group("cur").lower())
            return amount, currency, match.group(0)
    return None, None, None


def _normalize_currency(value: Any) -> Optional[str]:
    if not value:
        return None
    text = str(value).strip()
    return CURRENCY_MAP.get(text.lower(), text.upper()[:8])


# --------------------------------------------------------------------------- #
# HTML parsing
# --------------------------------------------------------------------------- #


def _make_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except FeatureNotFound:  # pragma: no cover
        return BeautifulSoup(html, "html.parser")


def _plain(fragment: Any) -> str:
    if fragment is None:
        return ""
    text = str(fragment)
    if "<" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    return clean_text(text)


def _meta_map(soup: BeautifulSoup) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or tag.get("itemprop") or "").strip().lower()
        content = tag.get("content")
        if key and content and key not in meta:
            meta[key] = str(content).strip()
    return meta


def _collect_jsonld(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, list):
            for item in obj:
                walk(item)
        elif isinstance(obj, dict):
            nodes.append(obj)
            if "@graph" in obj:
                walk(obj["@graph"])

    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        raw = script.string or script.get_text() or ""
        try:
            walk(json.loads(raw.strip()))
        except (json.JSONDecodeError, ValueError):
            continue
    return nodes


def _types(node: Dict[str, Any]) -> List[str]:
    t = node.get("@type")
    if isinstance(t, str):
        return [t.lower()]
    if isinstance(t, list):
        return [str(x).lower() for x in t]
    return []


def _find_product(nodes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for node in nodes:
        if any(t in ("product", "productgroup", "individualproduct") for t in _types(node)):
            return node
    return None


def _name_of(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        return clean_text(value.get("name"), 300) or None
    if isinstance(value, list) and value:
        return _name_of(value[0])
    if value:
        return clean_text(str(value), 300) or None
    return None


def _offer_fields(product: Dict[str, Any]) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    offers = product.get("offers")
    candidates: List[Dict[str, Any]] = []
    if isinstance(offers, dict):
        candidates = [offers]
    elif isinstance(offers, list):
        candidates = [o for o in offers if isinstance(o, dict)]
    for offer in candidates:
        price = offer.get("price")
        if price is None:
            price = offer.get("lowPrice")
        spec = offer.get("priceSpecification")
        if price is None and isinstance(spec, dict):
            price = spec.get("price")
        amount = parse_amount(price)
        if amount is not None:
            currency = offer.get("priceCurrency") or (spec.get("priceCurrency") if isinstance(spec, dict) else None)
            return amount, _normalize_currency(currency), _name_of(offer.get("seller"))
    seller = None
    for offer in candidates:
        seller = seller or _name_of(offer.get("seller"))
    return None, None, seller


def _image_candidates(value: Any) -> List[str]:
    out: List[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        out.extend(_image_candidates(value.get("url") or value.get("contentUrl")))
    elif isinstance(value, list):
        for item in value:
            out.extend(_image_candidates(item))
    return out


def _clean_images(candidates: List[str], base_url: str) -> List[str]:
    cleaned: List[str] = []
    for candidate in candidates:
        if not candidate or candidate.lower().startswith("data:"):
            continue
        absolute = urljoin(base_url, candidate.strip())
        if urlsplit(absolute).scheme.lower() not in ("http", "https"):
            continue
        if absolute not in cleaned:
            cleaned.append(absolute[:2048])
        if len(cleaned) >= 8:
            break
    return cleaned


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_RETURN_WORDS = ("return", "refund", "exchange", "money-back", "money back")
_CONTACT_HREF = ("contact", "mailto:", "tel:", "/support", "customer-service", "help-center")


def detect_policies(soup: BeautifulSoup, text: str) -> Tuple[bool, bool]:
    """(has_return_info, has_contact_info) from links and visible text."""
    has_return = False
    has_contact = False
    for anchor in soup.find_all("a"):
        href = (anchor.get("href") or "").lower()
        label = (anchor.get_text(" ", strip=True) or "").lower()
        blob = f"{href} {label}"
        if any(w in blob for w in _RETURN_WORDS):
            has_return = True
        if any(w in href for w in _CONTACT_HREF) or "contact" in label:
            has_contact = True
    low = text.lower()
    if re.search(r"(return|refund|exchange)s?\s+(?:&\s+\w+\s+)?polic|returns?\s*(?:&|and)\s*refunds?|money[- ]back|\d+[- ]day returns?", low):
        has_return = True
    if "contact us" in low or _EMAIL_RE.search(text):
        has_contact = True
    return has_return, has_contact


def _looks_blocked(text: str) -> bool:
    low = text.lower()
    markers = ("captcha", "verify you are a human", "access denied", "robot check", "are you a robot", "unusual traffic")
    return len(text) < 2500 and any(m in low for m in markers)


def parse_html(html: str, page_url: str, source: str) -> Listing:
    soup = _make_soup(html)
    meta = _meta_map(soup)
    product = _find_product(_collect_jsonld(soup))

    title = description = seller = brand = None
    price = currency = None
    price_text = None
    price_source: Optional[str] = None
    image_candidates: List[str] = []

    if product:
        title = _name_of(product.get("name"))
        description = _plain(product.get("description")) or None
        brand = _name_of(product.get("brand"))
        price, currency, seller = _offer_fields(product)
        if price is not None:
            price_source = "structured"
            price_text = f"{price:g} {currency}" if currency else f"{price:g}"
        image_candidates += _image_candidates(product.get("image"))

    title = title or clean_text(meta.get("og:title") or meta.get("twitter:title"), 300) or None
    if not title and soup.title and soup.title.string:
        title = clean_text(soup.title.string, 300) or None
    if not title:
        h1 = soup.find("h1")
        title = clean_text(h1.get_text(" ", strip=True), 300) if h1 else None
    description = description or clean_text(
        meta.get("og:description") or meta.get("description") or meta.get("twitter:description"), 3000
    ) or None
    seller = seller or clean_text(meta.get("og:site_name") or meta.get("application-name"), 200) or None

    if price is None:
        amount = parse_amount(meta.get("product:price:amount") or meta.get("og:price:amount") or meta.get("price"))
        if amount is not None:
            price = amount
            currency = _normalize_currency(
                meta.get("product:price:currency") or meta.get("og:price:currency") or meta.get("pricecurrency")
            )
            price_source = "structured"
            price_text = f"{amount:g} {currency}" if currency else f"{amount:g}"

    image_candidates += [meta[k] for k in ("og:image", "og:image:url", "twitter:image") if meta.get(k)]
    for img in soup.find_all("img", attrs={"itemprop": "image"})[:3]:
        if img.get("src"):
            image_candidates.append(img["src"])

    # Visible text (after structured data was collected)
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    text = re.sub(r"\n\s*\n+", "\n", soup.get_text("\n"))
    text = "\n".join(clean_text(line) for line in text.splitlines() if clean_text(line))
    has_return, has_contact = detect_policies(soup, text)

    if price is None:
        amount, cur, matched = find_price(text[:6000])
        if amount is not None:
            price, currency, price_text, price_source = amount, cur, matched, "text_pattern"

    complete_page = len(text) >= MIN_FULL_PAGE_CHARS
    domain = (urlsplit(page_url).hostname or "").lower() or None
    return Listing(
        url=page_url,
        final_url=page_url,
        domain=domain,
        title=title,
        price=price,
        currency=currency,
        price_text=price_text,
        price_source=price_source,  # type: ignore[arg-type]
        description=description,
        seller=seller,
        brand=brand,
        image_urls=_clean_images(image_candidates, page_url),
        has_return_policy=True if has_return else (False if complete_page else None),
        has_contact_info=True if has_contact else (False if complete_page else None),
        page_text=text[:PAGE_TEXT_LIMIT],
        page_text_length=len(text),
        source=source,
        sources=[source],
    )


def parse_markdown(
    markdown: str,
    page_url: str,
    source: str,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    images: Optional[List[str]] = None,
) -> Listing:
    """Build a Listing from reader-style markdown (Jina / Firecrawl fallback)."""
    text = markdown or ""
    if not title:
        m = re.search(r"^#\s+(.+)$", text, re.M)
        title = m.group(1).strip() if m else None
    image_urls = list(images or []) + re.findall(r"!\[[^\]]*\]\((https?://[^)\s]+)", text)
    plain = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    plain = re.sub(r"\[([^\]]*)\]\(([^)]*)\)", r"\1 \2", plain)
    plain = "\n".join(clean_text(line) for line in plain.splitlines() if clean_text(line))
    if not description:
        for para in re.split(r"\n{1,}", plain):
            if len(para) >= 80:
                description = para[:1500]
                break
    low = plain.lower()
    has_return = bool(re.search(r"return|refund|exchange", low))
    has_contact = bool(_EMAIL_RE.search(plain) or "contact" in low)
    amount, currency, matched = find_price(plain[:6000])
    complete_page = len(plain) >= MIN_FULL_PAGE_CHARS
    return Listing(
        url=page_url,
        final_url=page_url,
        domain=(urlsplit(page_url).hostname or "").lower() or None,
        title=title,
        price=amount,
        currency=currency,
        price_text=matched,
        price_source="text_pattern" if amount is not None else None,
        description=description,
        image_urls=_clean_images(image_urls, page_url),
        has_return_policy=True if has_return else (False if complete_page else None),
        has_contact_info=True if has_contact else (False if complete_page else None),
        page_text=plain[:PAGE_TEXT_LIMIT],
        page_text_length=len(plain),
        source=source,
        sources=[source],
    )


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


def extract_direct(url: str, settings: Settings, fetch: Callable[..., Any] = safe_get) -> Listing:
    result = fetch(
        url,
        timeout=settings.extractor_timeout,
        max_bytes=settings.max_response_bytes,
        max_redirects=settings.max_redirects,
    )
    if _looks_blocked(result.text):
        raise BlockedByPageError("The page asked for a CAPTCHA or human verification.")
    listing = parse_html(result.text, result.final_url, "direct")
    listing.url = url
    return listing


def extract_firecrawl(
    url: str, settings: Settings, http_post: Callable[..., Any] = requests.post
) -> Listing:
    if not settings.firecrawl_api_key:
        raise ProviderError("not configured", category="not_configured")
    resp = http_post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"},
        json={
            "url": url,
            "formats": ["html", "markdown"],
            "onlyMainContent": False,
            "timeout": int(settings.extractor_timeout * 1000),
        },
        timeout=settings.extractor_timeout + 5,
    )
    if getattr(resp, "status_code", 200) == 429:
        raise ProviderError("rate limited", category="quota_or_rate_limit")
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict) or not payload.get("success"):
        raise ProviderError("scrape failed", category="provider_error")
    data = payload.get("data") or {}
    meta = data.get("metadata") or {}
    html = (data.get("html") or "")[: settings.max_response_bytes]
    markdown = (data.get("markdown") or "")[: settings.max_response_bytes]
    if html:
        listing = parse_html(html, url, "firecrawl")
        if not listing.title and meta.get("title"):
            listing.title = clean_text(meta.get("title"), 300)
        if not listing.description and meta.get("description"):
            listing.description = clean_text(meta.get("description"), 3000)
        if not listing.image_urls and meta.get("ogImage"):
            listing.image_urls = _clean_images([str(meta["ogImage"])], url)
        return listing
    if not markdown:
        raise ProviderError("empty response", category="invalid_response")
    return parse_markdown(
        markdown,
        url,
        "firecrawl",
        title=clean_text(meta.get("title"), 300) or None,
        description=clean_text(meta.get("description"), 3000) or None,
        images=_clean_images([str(meta["ogImage"])], url) if meta.get("ogImage") else None,
    )


def extract_jina(url: str, settings: Settings, http_get: Callable[..., Any] = requests.get) -> Listing:
    headers = {"Accept": "application/json", "X-Return-Format": "markdown"}
    if settings.jina_api_key:
        headers["Authorization"] = f"Bearer {settings.jina_api_key}"
    resp = http_get("https://r.jina.ai/" + url, headers=headers, timeout=settings.extractor_timeout)
    if getattr(resp, "status_code", 200) == 429:
        raise ProviderError("rate limited", category="quota_or_rate_limit")
    resp.raise_for_status()
    try:
        payload = resp.json()
        data = payload.get("data") if isinstance(payload, dict) else None
    except ValueError:
        payload, data = None, None
    if isinstance(data, dict):
        content = (data.get("content") or "")[: settings.max_response_bytes]
        images = data.get("images")
        image_list = list(images.values()) if isinstance(images, dict) else (images if isinstance(images, list) else [])
        return parse_markdown(
            content,
            url,
            "jina",
            title=clean_text(data.get("title"), 300) or None,
            description=clean_text(data.get("description"), 3000) or None,
            images=[str(i) for i in image_list if isinstance(i, str)],
        )
    text = (resp.text or "")[: settings.max_response_bytes]
    if not text.strip():
        raise ProviderError("empty response", category="invalid_response")
    return parse_markdown(text, url, "jina")


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def is_sufficient(listing: Optional[Listing]) -> bool:
    """AC-02: title plus at least two of price, description, seller, images."""
    if listing is None or not listing.title:
        return False
    extras = [
        listing.price is not None,
        bool(listing.description),
        bool(listing.seller),
        bool(listing.image_urls),
    ]
    return sum(extras) >= 2


def merge_listings(base: Optional[Listing], extra: Listing) -> Listing:
    if base is None:
        return extra
    merged = base.model_copy(deep=True)
    for name in ("title", "description", "seller", "brand", "currency", "price_text", "price_source"):
        if not getattr(merged, name) and getattr(extra, name):
            setattr(merged, name, getattr(extra, name))
    if merged.price is None and extra.price is not None:
        merged.price = extra.price
        merged.currency = merged.currency or extra.currency
        merged.price_text = merged.price_text or extra.price_text
        merged.price_source = extra.price_source
    for img in extra.image_urls:
        if img not in merged.image_urls and len(merged.image_urls) < 12:
            merged.image_urls.append(img)
    for flag in ("has_return_policy", "has_contact_info"):
        mine, theirs = getattr(merged, flag), getattr(extra, flag)
        if mine is not True and theirs is True:
            setattr(merged, flag, True)
        elif mine is None and theirs is not None:
            setattr(merged, flag, theirs)
    if len(extra.page_text) > len(merged.page_text):
        merged.page_text = extra.page_text
        merged.page_text_length = max(merged.page_text_length, extra.page_text_length)
    merged.sources = list(dict.fromkeys(merged.sources + extra.sources))
    return merged


@dataclass
class ExtractionOutcome:
    listing: Optional[Listing]
    statuses: List[ProviderStatus] = field(default_factory=list)
    sufficient: bool = False
    unsafe: bool = False


def default_providers(settings: Settings) -> Dict[str, Callable[[str], Listing]]:
    registry: Dict[str, Callable[[str], Listing]] = {
        "direct": lambda u: extract_direct(u, settings),
        "jina": lambda u: extract_jina(u, settings),
    }
    if settings.firecrawl_api_key:
        registry["firecrawl"] = lambda u: extract_firecrawl(u, settings)
    return registry


def extract_listing(
    url: str,
    settings: Settings,
    providers: Optional[Dict[str, Callable[[str], Listing]]] = None,
) -> ExtractionOutcome:
    """Try providers in order, isolating each one's failures (AC-12)."""
    registry = providers if providers is not None else default_providers(settings)
    statuses: List[ProviderStatus] = []
    merged: Optional[Listing] = None
    started = time.monotonic()
    for name in settings.extractor_order:
        if name not in registry:
            statuses.append(ProviderStatus(provider=name, status="skipped", detail="not_configured"))
            continue
        if time.monotonic() - started > settings.extraction_budget:
            statuses.append(ProviderStatus(provider=name, status="skipped", detail="time_budget_exhausted"))
            continue
        t0 = time.monotonic()
        try:
            listing = run_with_timeout(registry[name], settings.extractor_timeout + 5, url)
        except BlockedByPageError:
            statuses.append(
                ProviderStatus(
                    provider=name,
                    status="unavailable",
                    detail="site_blocks_automated_access (not bypassed)",
                    elapsed_ms=int((time.monotonic() - t0) * 1000),
                )
            )
            continue
        except UnresolvableHostError:
            statuses.append(
                ProviderStatus(provider=name, status="error", detail="unresolvable_host",
                               elapsed_ms=int((time.monotonic() - t0) * 1000))
            )
            continue
        except UnsafeURLError:
            statuses.append(
                ProviderStatus(provider=name, status="error", detail="blocked_url_or_redirect",
                               elapsed_ms=int((time.monotonic() - t0) * 1000))
            )
            return ExtractionOutcome(listing=None, statuses=statuses, sufficient=False, unsafe=True)
        except FetchError as exc:
            statuses.append(
                ProviderStatus(provider=name, status="error", detail=clean_text(str(exc), 120),
                               elapsed_ms=int((time.monotonic() - t0) * 1000))
            )
            continue
        except Exception as exc:
            category = error_category(exc)
            statuses.append(
                ProviderStatus(
                    provider=name,
                    status="timeout" if category == "timeout" else "error",
                    detail=category,
                    elapsed_ms=int((time.monotonic() - t0) * 1000),
                )
            )
            continue
        statuses.append(
            ProviderStatus(provider=name, status="ok", detail="listing parsed",
                           elapsed_ms=int((time.monotonic() - t0) * 1000))
        )
        merged = merge_listings(merged, listing)
        if is_sufficient(merged):
            break
    return ExtractionOutcome(listing=merged, statuses=statuses, sufficient=is_sufficient(merged))


# --------------------------------------------------------------------------- #
# Manual fallback (AC-03)
# --------------------------------------------------------------------------- #


def listing_from_manual(data: ManualInput) -> Listing:
    title = clean_text(data.title, 300)
    if not title:
        raise InvalidInputError("Please enter at least the product title.")
    amount, currency, matched = find_price(data.price_text)
    if amount is None and data.price_text.strip():
        amount = parse_amount(data.price_text)
        matched = clean_text(data.price_text, 80)
    images: List[str] = []
    for raw in data.image_urls:
        raw = (raw or "").strip()
        if raw and urlsplit(raw).scheme.lower() in ("http", "https") and raw not in images:
            images.append(raw[:2048])
    url = (data.url or "").strip() or None
    domain = None
    if url:
        try:
            domain = validate_url(url, resolve=False).host
        except UnsafeURLError as exc:
            raise InvalidInputError(f"Listing URL: {exc}") from None
    description = clean_text(data.description, 3000) or None
    return Listing(
        url=url,
        final_url=url,
        domain=domain,
        title=title,
        price=amount,
        currency=currency,
        price_text=matched or (clean_text(data.price_text, 80) or None),
        price_source="manual" if amount is not None else None,
        description=description,
        seller=clean_text(data.seller, 200) or None,
        image_urls=images[:8],
        has_return_policy=None,
        has_contact_info=None,
        page_text=description or "",
        page_text_length=len(description or ""),
        source="manual",
        sources=["manual"],
    )
