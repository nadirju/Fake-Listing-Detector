"""End-to-end orchestration: input -> listing -> independent signals -> Gemini ->
deterministic score -> report.

Every provider is isolated (own timeout, own status). A failing provider downgrades
the assessment to "partial"/"insufficient evidence"; it never crashes the run (AC-12).
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from .cache import fingerprint, get_cache
from .config import Settings
from .demo_data import load_demo
from .errors import AnalysisError, ExtractionFailed, InvalidInputError, UnsafeURLError
from .extractor import ExtractionOutcome, extract_listing, listing_from_manual
from .gemini_analyzer import GeminiAnalyzer, ground_analysis
from .i18n import GEMINI_LANGUAGE_NAMES, t
from .logging_utils import log_event
from .reporting import build_actions, build_limitations, build_red_flags, build_unavailable
from .schemas import (
    AnalysisReport,
    DomainAgeResult,
    GeminiAnalysis,
    GroundedAnalysis,
    ImageSearchResult,
    Listing,
    ManualInput,
    ProviderStatus,
    ReferencePrice,
)
from .scoring import ScoringInputs, compute_score
from .signals.domain_age import lookup_domain_age
from .signals.image_search import search_images
from .signals.listing_rules import scan_text
from .url_safety import normalize_url_for_cache, redact_url, validate_url
from .utils import error_category, run_with_timeout

ProgressFn = Callable[[float, str], None]
AI_EXCERPT_CHARS = 6000


# --------------------------------------------------------------------------- #
# Request + dependency injection (keeps providers swappable and testable)
# --------------------------------------------------------------------------- #


@dataclass
class AnalysisRequest:
    mode: Literal["url", "manual", "demo"] = "url"
    url: Optional[str] = None
    manual: Optional[ManualInput] = None
    demo_id: Optional[str] = None
    language: str = "en"
    reference: Optional[ReferencePrice] = None
    now: Optional[datetime] = None


def _default_extract(url: str, settings: Settings) -> ExtractionOutcome:
    return extract_listing(url, settings)


def _default_domain_age(host: str, settings: Settings) -> Tuple[DomainAgeResult, ProviderStatus]:
    return lookup_domain_age(host, timeout=settings.whois_timeout)


def _default_image_search(
    images: List[str], domain: str, settings: Settings
) -> Tuple[ImageSearchResult, ProviderStatus]:
    return search_images(
        images,
        domain,
        api_key=settings.serpapi_api_key,
        engine=settings.serpapi_engine,
        timeout=settings.image_timeout,
        max_images=settings.max_images_to_search,
    )


def _default_ai(evidence: Dict[str, Any], language_name: str, settings: Settings) -> GeminiAnalysis:
    analyzer = GeminiAnalyzer(settings.gemini_api_key, settings.gemini_model, settings.gemini_timeout)
    return analyzer.analyze(evidence, language_name)


@dataclass
class Dependencies:
    extract: Callable[[str, Settings], ExtractionOutcome] = _default_extract
    domain_age: Callable[[str, Settings], Tuple[DomainAgeResult, ProviderStatus]] = _default_domain_age
    image_search: Callable[[List[str], str, Settings], Tuple[ImageSearchResult, ProviderStatus]] = _default_image_search
    ai: Callable[[Dict[str, Any], str, Settings], GeminiAnalysis] = _default_ai
    use_cache: bool = True


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _redact_listing(listing: Listing) -> Listing:
    """Copy of the listing safe to store/export: no page text, redacted URLs."""
    return listing.model_copy(
        update={
            "page_text": "",
            "url": redact_url(listing.url) if listing.url else None,
            "final_url": redact_url(listing.final_url) if listing.final_url else None,
            "image_urls": [redact_url(u) for u in listing.image_urls],
        },
        deep=True,
    )


def build_evidence(
    listing: Listing,
    domain: Optional[DomainAgeResult],
    images: Optional[ImageSearchResult],
    reference: Optional[ReferencePrice],
    now: datetime,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Evidence JSON sent to Gemini + the exact text corpus used for grounding."""
    excerpt = (listing.page_text or "")[:AI_EXCERPT_CHARS]
    domain_block: Dict[str, Any] = {"status": "unavailable"}
    if domain and domain.status == "ok" and domain.creation_date:
        created = domain.creation_date
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        domain_block = {
            "status": "ok",
            "creation_date": created.date().isoformat(),
            "age_days": max((now - created).days, 0),
        }
    matches = []
    if images and images.status == "ok":
        matches = [
            {
                "source_url": m.link,
                "title": m.title,
                "source": m.source,
                "source_domain": m.domain,
                "snippet": m.snippet,
            }
            for m in images.matches
        ]
    evidence = {
        "listing": {
            "title": listing.title,
            "price_text": listing.price_text,
            "price": listing.price,
            "currency": listing.currency,
            "price_source": listing.price_source,
            "description": listing.description,
            "seller": listing.seller,
            "brand": listing.brand,
            "domain": listing.domain,
            "has_return_policy": listing.has_return_policy,
            "has_contact_info": listing.has_contact_info,
            "image_count": len(listing.image_urls),
        },
        "page_text_excerpt": excerpt,
        "domain_age": domain_block,
        "image_matches": matches,
        "reference_price": (
            {"amount": reference.amount, "currency": reference.currency, "source_url": reference.source_url}
            if reference
            else None
        ),
    }
    corpus = {
        "title": listing.title or "",
        "description": listing.description or "",
        "seller": listing.seller or "",
        "brand": listing.brand or "",
        "price_text": listing.price_text or "",
        "page_text": excerpt,
    }
    return evidence, corpus


def _collect_domain(
    deps: Dependencies, settings: Settings, listing: Listing, ttl: int
) -> Tuple[DomainAgeResult, ProviderStatus]:
    host = listing.domain
    if not host:
        return (
            DomainAgeResult(domain="", status="unavailable", reason="No domain is known for this listing."),
            ProviderStatus(provider="whois", status="skipped", detail="no_domain"),
        )
    cache = get_cache("whois", max(ttl * 8, 60))
    key = host.lower()
    if deps.use_cache:
        hit = cache.get(key)
        if hit:
            result, status = hit
            return result, status.model_copy(update={"detail": "cache hit", "elapsed_ms": 0})
    started = time.monotonic()
    try:
        result, status = run_with_timeout(deps.domain_age, settings.whois_timeout + 2, host, settings)
    except Exception as exc:
        category = error_category(exc)
        return (
            DomainAgeResult(domain=host, status="unavailable", reason=f"WHOIS lookup failed ({category})."),
            ProviderStatus(
                provider="whois",
                status="timeout" if category == "timeout" else "error",
                detail=category,
                elapsed_ms=_ms(started),
            ),
        )
    if deps.use_cache and status.status == "ok":
        cache.set(key, (result, status))
    return result, status


def _collect_images(
    deps: Dependencies, settings: Settings, listing: Listing, ttl: int
) -> Tuple[ImageSearchResult, ProviderStatus]:
    cache = get_cache("image_search", max(ttl * 8, 60))
    key = fingerprint([sorted(listing.image_urls)[:12], listing.domain])
    if deps.use_cache:
        hit = cache.get(key)
        if hit:
            result, status = hit
            return result, status.model_copy(update={"detail": "cache hit", "elapsed_ms": 0})
    started = time.monotonic()
    try:
        result, status = run_with_timeout(
            deps.image_search,
            settings.image_timeout + 8,
            list(listing.image_urls),
            listing.domain or "",
            settings,
        )
    except Exception as exc:
        category = error_category(exc)
        return (
            ImageSearchResult(status="error", reason=f"Image search failed ({category})."),
            ProviderStatus(
                provider="serpapi",
                status="timeout" if category == "timeout" else "error",
                detail=category,
                elapsed_ms=_ms(started),
            ),
        )
    if deps.use_cache and status.status == "ok":
        cache.set(key, (result, status))
    return result, status


def _run_ai(
    deps: Dependencies,
    settings: Settings,
    evidence: Dict[str, Any],
    corpus: Dict[str, str],
    allowed_urls: set,
    language: str,
) -> Tuple[Optional[GroundedAnalysis], Optional[str], ProviderStatus]:
    """Returns (grounded analysis | None, unavailable reason | None, status)."""
    if not settings.gemini_api_key and deps.ai is _default_ai:
        return (
            None,
            "AI analysis is not configured (no Gemini API key).",
            ProviderStatus(provider="gemini", status="skipped", detail="not_configured"),
        )
    language_name = GEMINI_LANGUAGE_NAMES.get(language, "English")
    cache = get_cache("gemini", settings.cache_ttl_seconds)
    key = fingerprint([evidence, language_name, settings.gemini_model])
    started = time.monotonic()
    raw: Optional[GeminiAnalysis] = cache.get(key) if deps.use_cache else None
    cached = raw is not None
    if raw is None:
        try:
            raw = run_with_timeout(deps.ai, settings.gemini_timeout + 5, evidence, language_name, settings)
        except AnalysisError as exc:
            return (
                None,
                f"AI analysis failed ({exc.category}).",
                ProviderStatus(
                    provider="gemini",
                    status="timeout" if exc.category == "timeout" else "error",
                    detail=exc.category,
                    elapsed_ms=_ms(started),
                ),
            )
        except Exception as exc:
            category = error_category(exc)
            return (
                None,
                f"AI analysis failed ({category}).",
                ProviderStatus(
                    provider="gemini",
                    status="timeout" if category == "timeout" else "error",
                    detail=category,
                    elapsed_ms=_ms(started),
                ),
            )
        if deps.use_cache:
            cache.set(key, raw)
    grounded = ground_analysis(raw, corpus, allowed_urls)
    detail = ("cache hit; " if cached else "") + f"{grounded.dropped_claims} unsupported claim(s) dropped"
    return grounded, None, ProviderStatus(provider="gemini", status="ok", detail=detail, elapsed_ms=_ms(started))


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #


def analyze(
    request: AnalysisRequest,
    settings: Settings,
    deps: Optional[Dependencies] = None,
    progress: Optional[ProgressFn] = None,
) -> AnalysisReport:
    deps = deps or Dependencies()
    started = time.monotonic()
    request_id = uuid.uuid4().hex[:10]
    now = request.now or datetime.now(timezone.utc)
    lang = request.language if request.language in ("en", "ur") else "en"
    statuses: List[ProviderStatus] = []

    def tick(fraction: float, message: str) -> None:
        if progress:
            try:
                progress(fraction, message)
            except Exception:  # a UI callback must never break the analysis
                pass

    log_event(
        request_id,
        "analysis_started",
        mode=request.mode,
        url=redact_url(request.url) if request.url else None,
    )

    # ---- 1. listing ----------------------------------------------------- #
    domain: Optional[DomainAgeResult] = None
    images: Optional[ImageSearchResult] = None
    reference = request.reference
    demo_ai: Optional[GeminiAnalysis] = None
    demo_notice: Optional[str] = None

    if request.mode == "demo":
        tick(0.1, "Loading pre-recorded demo case")
        try:
            bundle = load_demo(request.demo_id or "", now)
        except KeyError:
            raise InvalidInputError("Unknown demo case.") from None
        listing = bundle.listing
        domain, images, demo_ai, reference = bundle.domain, bundle.images, bundle.ai, bundle.reference
        demo_notice = t("demo_banner", lang)
        statuses += [
            ProviderStatus(provider="demo_bundle", status="ok", detail="pre-recorded listing, WHOIS, image search and AI output"),
        ]
    elif request.mode == "manual":
        tick(0.1, "Reading the details you entered")
        if request.manual is None:
            raise InvalidInputError("No listing details were provided.")
        listing = listing_from_manual(request.manual)
        statuses.append(ProviderStatus(provider="manual_input", status="ok", detail="user-provided"))
    else:
        tick(0.05, "Validating the URL")
        try:
            safe = validate_url(request.url)
        except UnsafeURLError as exc:
            raise InvalidInputError(str(exc)) from None
        tick(0.12, "Reading the listing page")
        cache = get_cache("extraction", settings.cache_ttl_seconds)
        cache_key = normalize_url_for_cache(safe.url)
        cached_listing = cache.get(cache_key) if deps.use_cache else None
        if cached_listing is not None:
            listing = cached_listing.model_copy(deep=True)
            statuses.append(ProviderStatus(provider="extraction", status="ok", detail="cache hit"))
        else:
            outcome = deps.extract(safe.url, settings)
            statuses += outcome.statuses
            if outcome.unsafe:
                raise InvalidInputError("The page redirected to a blocked (private or internal) address.")
            if outcome.listing is None or not outcome.listing.title:
                log_event(request_id, "extraction_failed", statuses=[f"{s.provider}:{s.status}" for s in outcome.statuses])
                raise ExtractionFailed("The listing could not be read automatically.", outcome.statuses)
            listing = outcome.listing
            listing.url = safe.url
            if deps.use_cache and outcome.sufficient:
                cache.set(cache_key, listing.model_copy(deep=True))

    # ---- 2. independent signals (parallel, isolated) --------------------- #
    if request.mode != "demo":
        tick(0.4, "Checking domain age and reverse-image matches")
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_domain = pool.submit(_collect_domain, deps, settings, listing, settings.cache_ttl_seconds)
            f_images = pool.submit(_collect_images, deps, settings, listing, settings.cache_ttl_seconds)
            domain, domain_status = f_domain.result()
            images, image_status = f_images.result()
        statuses += [domain_status, image_status]
    else:
        statuses += [
            ProviderStatus(provider="whois", status="ok", detail="demo bundle"),
            ProviderStatus(provider="serpapi", status="ok", detail="demo bundle"),
        ]

    # ---- 3. Gemini interpretation ---------------------------------------- #
    tick(0.65, "Interpreting the evidence")
    evidence, corpus = build_evidence(listing, domain, images, reference, now)
    allowed_urls = {m.link for m in (images.matches if images and images.status == "ok" else [])}
    ai: Optional[GroundedAnalysis]
    ai_reason: Optional[str]
    if request.mode == "demo" and demo_ai is not None:
        ai = ground_analysis(demo_ai, corpus, allowed_urls)
        ai_reason = None
        statuses.append(ProviderStatus(provider="gemini", status="ok", detail="demo bundle (pre-recorded output)"))
    else:
        ai, ai_reason, ai_status = _run_ai(deps, settings, evidence, corpus, allowed_urls, lang)
        statuses.append(ai_status)

    # ---- 4. deterministic scoring ---------------------------------------- #
    tick(0.88, "Calculating the risk score")
    rules = scan_text(
        {"title": listing.title or "", "description": listing.description or "", "page_text": listing.page_text}
    )
    outcome_score = compute_score(
        ScoringInputs(
            listing=listing,
            now=now,
            domain=domain,
            images=images,
            ai=ai,
            ai_unavailable_reason=ai_reason,
            rules=rules,
            reference=reference,
        )
    )

    # ---- 5. report -------------------------------------------------------- #
    extra_unavailable: Dict[str, str] = {}
    if ai is None:
        extra_unavailable["ai_analysis"] = ai_reason or "AI analysis was unavailable."
    unavailable_ids, unavailable_details = build_unavailable(outcome_score.signals, extra_unavailable, lang)
    price_signal = next((s for s in outcome_score.signals if s.id == "price_check"), None)
    report = AnalysisReport(
        listing_url=redact_url(listing.url) if listing.url else None,
        risk_score=outcome_score.score,
        risk_band=outcome_score.band,
        assessment_status=outcome_score.status,
        signals=outcome_score.signals,
        red_flags=build_red_flags(outcome_score.signals, ai, lang),
        checks_unavailable=unavailable_ids,
        recommended_actions=build_actions(
            outcome_score.signals, ai, outcome_score.band, outcome_score.status, lang
        ),
        disclaimer=t("disclaimer", lang),
        request_id=request_id,
        generated_at=now,
        elapsed_ms=_ms(started),
        mode=request.mode,
        language=lang,  # type: ignore[arg-type]
        listing=_redact_listing(listing),
        image_matches=[
            m.model_copy(update={"image_url": redact_url(m.image_url)})
            for m in (images.matches if images and images.status == "ok" else [])
        ],
        provider_statuses=statuses,
        unavailable_details=unavailable_details,
        ai_summary=ai.summary if ai and ai.summary else None,
        price_note=(price_signal.reason_unavailable if price_signal and price_signal.status == "unknown" else None),
        limitations=build_limitations(outcome_score.signals, listing.source, ai is not None, lang),
        insufficient_reason=outcome_score.insufficient_reason,
        demo_notice=demo_notice,
    )
    tick(1.0, "Done")
    log_event(
        request_id,
        "analysis_completed",
        mode=request.mode,
        elapsed_ms=report.elapsed_ms,
        score=report.risk_score,
        band=report.risk_band,
        status=report.assessment_status,
        providers=[f"{s.provider}:{s.status}" for s in statuses],
    )
    return report
