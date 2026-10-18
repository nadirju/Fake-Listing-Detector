"""Deterministic, documented risk scoring (AC-06, AC-07, AC-08).

The score is an integer 0-100 computed server-side from explicit signals. Higher
means MORE SUSPICIOUS SIGNALS WERE OBSERVED - it is not the probability that an
item is fake. Missing data never adds or removes points.

Initial heuristic weights (from the PRD; NOT empirically validated):

    image_search         25  copied imagery from an unrelated source (15 if only one source)
    price_check          20  only with a user-supplied comparable reference + source URL
    listing_consistency  15  grounded contradictions (high 15 / medium 10 / low 5)
    domain_age           10  < 30 days: 10, < 90 days: 5          } correlated:
    contact_returns      10  neither found: 10, one missing: 5    } capped at 15 together
    urgency_payment      10  risky payment request 10; pressure language 3-5

Total is capped at 100. Same normalized input + same evidence => same score.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .gemini_analyzer import _url_key
from .schemas import (
    AssessmentStatus,
    DomainAgeResult,
    GroundedAnalysis,
    ImageSearchResult,
    Listing,
    ReferencePrice,
    RiskBand,
    Signal,
)
from .signals.listing_rules import RuleFindings
from .utils import registrable_domain, truncate

SCORING_VERSION = "1.0"

WEIGHTS: Dict[str, int] = {
    "image_search": 25,
    "price_check": 20,
    "listing_consistency": 15,
    "domain_age": 10,
    "contact_returns": 10,
    "urgency_payment": 10,
}
LABELS: Dict[str, str] = {
    "image_search": "Reverse image search",
    "price_check": "Price check",
    "listing_consistency": "Listing consistency",
    "domain_age": "Domain age",
    "contact_returns": "Contact & returns information",
    "urgency_payment": "Pressure & payment language",
}
# Correlated signals share a cap so they cannot double-count the same weakness.
GROUP_CAPS: Dict[str, Tuple[int, Tuple[str, ...]]] = {
    "store_hygiene": (15, ("domain_age", "contact_returns")),
}
OPTIONAL_CHECKS = frozenset({"price_check"})  # absence does not make an assessment "partial"
MIN_KNOWN_SIGNALS = 2
BAND_CAUTION_FROM = 30
BAND_HIGH_FROM = 60
MIN_TEXT_FOR_CONSISTENCY = 40

PRICE_RATIO_HIGH = 0.40  # price <= 40% of reference -> 20 points
PRICE_RATIO_MEDIUM = 0.60  # price <= 60% of reference -> 10 points
DOMAIN_VERY_NEW_DAYS = 30
DOMAIN_NEW_DAYS = 90


@dataclass
class ScoringInputs:
    listing: Listing
    now: datetime
    domain: Optional[DomainAgeResult] = None
    images: Optional[ImageSearchResult] = None
    ai: Optional[GroundedAnalysis] = None
    ai_unavailable_reason: Optional[str] = None
    rules: RuleFindings = field(default_factory=RuleFindings)
    reference: Optional[ReferencePrice] = None


@dataclass
class ScoreOutcome:
    score: int
    band: RiskBand
    status: AssessmentStatus
    signals: List[Signal]
    insufficient_reason: Optional[str] = None


def band_for(score: int) -> RiskBand:
    if score >= BAND_HIGH_FROM:
        return "high_suspicion"
    if score >= BAND_CAUTION_FROM:
        return "caution"
    return "lower_observed_risk"


def _base(signal_id: str) -> Signal:
    return Signal(id=signal_id, label=LABELS[signal_id], max_weight=WEIGHTS[signal_id])


def _set(sig: Signal, status: str, points: int = 0, severity: str = "low", evidence: str = "") -> Signal:
    sig.status = status  # type: ignore[assignment]
    sig.severity = severity  # type: ignore[assignment]
    sig.raw_contribution = points
    sig.score_contribution = points
    sig.evidence = truncate(evidence, 400)
    return sig


def _unavailable(sig: Signal, reason: str) -> Signal:
    sig.status = "unknown"
    sig.reason_unavailable = reason
    sig.evidence = truncate(reason, 400)
    return sig


# --------------------------------------------------------------------------- #
# Individual signals
# --------------------------------------------------------------------------- #


def _image_signal(inp: ScoringInputs) -> Signal:
    sig = _base("image_search")
    images = inp.images
    if images is None:
        return _unavailable(sig, "Image search did not run.")
    if images.status != "ok":
        return _unavailable(sig, images.reason or "Image search was unavailable.")
    if not images.matches:
        sig.evidence = "No matching images were returned. This does not prove the image is original."
        return sig
    count = len(images.matches)
    if inp.ai is None:
        return _unavailable(
            sig,
            f"{count} possible match(es) were found, but their context could not be assessed "
            "because the AI analysis was unavailable.",
        )
    by_url = {_url_key(m.link): m for m in images.matches}
    listing_domain = registrable_domain(inp.listing.domain or inp.listing.final_url or inp.listing.url or "")
    unrelated = []
    benign = 0
    for assessment in inp.ai.image_match_assessments:
        match = by_url.get(_url_key(assessment.source_url))
        if match is None:
            continue
        if assessment.context == "unrelated_context" and match.domain != listing_domain:
            unrelated.append((assessment, match))
        elif assessment.context in ("manufacturer_or_official", "same_product_listing"):
            benign += 1
    if unrelated:
        domains = sorted({m.domain for _, m in unrelated if m.domain})
        strong = len(domains) >= 2
        first_assessment, first_match = unrelated[0]
        evidence = (
            f"Image also found on {', '.join(domains) or 'another site'} "
            f"(e.g. \"{truncate(first_match.title or first_match.source, 90)}\"). "
            f"Context assessment: {first_assessment.reason}"
        )
        sig.source_url = first_match.link
        return _set(sig, "flagged", 25 if strong else 15, "high" if strong else "medium", evidence)
    if benign:
        return _set(
            sig,
            "clear",
            0,
            "low",
            f"{count} match(es) found; the assessed ones look like manufacturer or other retailer listings of the same product.",
        )
    sig.evidence = f"{count} match(es) found but their context was unclear. Not scored."
    return sig


def _price_signal(inp: ScoringInputs) -> Signal:
    sig = _base("price_check")
    listing, ref = inp.listing, inp.reference
    if ref is None:
        note = ""
        if inp.ai and inp.ai.price_observation.note:
            note = f" Model impression (general knowledge, not live market data): {inp.ai.price_observation.note}"
        sig.note = "heuristic/unverified"
        return _unavailable(sig, "No comparable reference price was supplied, so the price was not verified." + note)
    if not ref.source_url:
        sig.note = "heuristic/unverified"
        return _unavailable(sig, "The reference price has no source URL, so it cannot count as a verified comparison.")
    if listing.price is None or listing.price_source not in ("structured", "manual"):
        return _unavailable(sig, "The listing price could not be read reliably, so no comparison was made.")
    if not listing.currency or listing.currency.upper() != ref.currency.upper():
        return _unavailable(
            sig, "The listing currency is unknown or differs from the reference currency; no comparison was made."
        )
    ratio = listing.price / ref.amount
    sig.source_url = ref.source_url
    evidence = (
        f"Listing price {listing.price:g} {listing.currency} vs reference {ref.amount:g} {ref.currency.upper()} "
        f"({ratio * 100:.0f}% of the reference)."
    )
    if ratio <= PRICE_RATIO_HIGH:
        return _set(sig, "flagged", 20, "high", evidence)
    if ratio <= PRICE_RATIO_MEDIUM:
        return _set(sig, "flagged", 10, "medium", evidence)
    return _set(sig, "clear", 0, "low", evidence)


_CONF_POINTS = {"high": (15, "high"), "medium": (10, "medium"), "low": (5, "low")}
_CONF_ORDER = {"high": 3, "medium": 2, "low": 1}


def _consistency_signal(inp: ScoringInputs) -> Signal:
    sig = _base("listing_consistency")
    if inp.ai is None:
        return _unavailable(sig, inp.ai_unavailable_reason or "AI analysis was unavailable.")
    listing = inp.listing
    text_len = len(listing.description or "") + len(listing.page_text or "")
    if not listing.title or text_len < MIN_TEXT_FOR_CONSISTENCY:
        return _unavailable(sig, "There was not enough listing text to check for inconsistencies.")
    if inp.ai.inconsistencies:
        best = max(inp.ai.inconsistencies, key=lambda i: _CONF_ORDER[i.confidence])
        points, severity = _CONF_POINTS[best.confidence]
        sig.note = best.description
        return _set(sig, "flagged", points, severity, f'{best.evidence_field}: "{best.evidence_quote}"')
    return _set(sig, "clear", 0, "low", "No evidence-backed contradiction was found in the fields provided.")


def _domain_signal(inp: ScoringInputs) -> Signal:
    sig = _base("domain_age")
    d = inp.domain
    if d is None:
        return _unavailable(sig, "Domain-age lookup did not run.")
    if d.status != "ok" or d.creation_date is None:
        return _unavailable(sig, d.reason or "Domain age is unavailable.")
    created = d.creation_date if d.creation_date.tzinfo else d.creation_date.replace(tzinfo=timezone.utc)
    age_days = max((inp.now - created).days, 0)
    evidence = (
        f"{d.domain} was registered on {created:%Y-%m-%d} ({age_days} days before this analysis; source: WHOIS)."
    )
    sig.note = "Domain age alone is weak evidence."
    if age_days < DOMAIN_VERY_NEW_DAYS:
        return _set(sig, "flagged", 10, "high", evidence)
    if age_days < DOMAIN_NEW_DAYS:
        return _set(sig, "flagged", 5, "low", evidence)
    return _set(sig, "clear", 0, "low", evidence)


def _contact_signal(inp: ScoringInputs) -> Signal:
    sig = _base("contact_returns")
    ret, contact = inp.listing.has_return_policy, inp.listing.has_contact_info
    if ret is None and contact is None:
        return _unavailable(sig, "Return and contact information could not be checked from this input.")
    if ret is True and contact is True:
        return _set(sig, "clear", 0, "low", "Return/refund information and contact details were found on the page.")
    if ret is False and contact is False:
        return _set(
            sig, "flagged", 10, "medium",
            "Neither return/refund information nor contact details were found on the captured page.",
        )
    if ret is False or contact is False:
        missing = "return/refund information" if ret is False else "contact details"
        if ret is None or contact is None:
            return _unavailable(sig, f"{missing.capitalize()} not found, and the other item could not be checked.")
        return _set(sig, "flagged", 5, "low", f"No {missing} were found on the captured page.")
    return _unavailable(sig, "Return and contact information could not be fully checked.")


def _urgency_signal(inp: ScoringInputs) -> Signal:
    sig = _base("urgency_payment")
    rules = inp.rules
    if not rules.text_checked:
        return _unavailable(sig, "There was no listing text to scan for pressure or payment language.")
    if rules.payment_hits:
        hit = rules.payment_hits[0]
        return _set(sig, "flagged", 10, "high", f'{hit.field}: "…{hit.quote}…"')
    if rules.urgency_hits:
        hit = rules.urgency_hits[0]
        points, severity = (5, "medium") if rules.distinct_urgency_rules >= 2 else (3, "low")
        sig.note = "Sale banners are common on legitimate shops; this is a weak signal."
        return _set(sig, "flagged", points, severity, f'{hit.field}: "…{hit.quote}…"')
    return _set(sig, "clear", 0, "low", "No pressure tactics or risky payment requests were detected in the text.")


# --------------------------------------------------------------------------- #
# Combination
# --------------------------------------------------------------------------- #


def _apply_group_caps(signals: List[Signal]) -> None:
    order = list(WEIGHTS)
    for _, (cap, ids) in GROUP_CAPS.items():
        members = sorted(
            (s for s in signals if s.id in ids and s.raw_contribution > 0),
            key=lambda s: (-s.raw_contribution, order.index(s.id)),
        )
        remaining = cap
        for s in members:
            allowed = min(s.raw_contribution, remaining)
            if allowed < s.raw_contribution:
                extra = f"Capped from {s.raw_contribution} to {allowed} points (correlated signals)."
                s.note = f"{s.note} {extra}".strip() if s.note else extra
            s.score_contribution = allowed
            remaining -= allowed


def assess_status(signals: List[Signal], listing: Listing) -> Tuple[AssessmentStatus, Optional[str]]:
    known = sum(1 for s in signals if s.status in ("flagged", "clear"))
    if not listing.title:
        return "insufficient_evidence", "The listing has no title, so no meaningful assessment is possible."
    if known < MIN_KNOWN_SIGNALS:
        return (
            "insufficient_evidence",
            f"Only {known} check(s) produced usable evidence; at least {MIN_KNOWN_SIGNALS} are needed.",
        )
    if any(s.reason_unavailable and s.id not in OPTIONAL_CHECKS for s in signals):
        return "partial", None
    return "complete", None


def compute_score(inp: ScoringInputs) -> ScoreOutcome:
    signals = [
        _image_signal(inp),
        _price_signal(inp),
        _consistency_signal(inp),
        _domain_signal(inp),
        _contact_signal(inp),
        _urgency_signal(inp),
    ]
    _apply_group_caps(signals)
    total = max(0, min(100, int(sum(s.score_contribution for s in signals))))
    status, reason = assess_status(signals, inp.listing)
    return ScoreOutcome(score=total, band=band_for(total), status=status, signals=signals, insufficient_reason=reason)
