from .schemas import AnalysisResult, Listing, Signal, RedFlag

ACTIONS = [
    "Open the brand's official website independently and confirm the seller is listed as an authorized retailer.",
    "Read the full return, refund, shipping, and contact policies before paying.",
    "Use a payment method with buyer protection; avoid gift-card, wire-transfer, or crypto-only requests.",
    "Compare the price with current listings from trustworthy sellers. This app has not verified a live market price.",
    "Search the product image and review any matching source in context; stock photos can be used by legitimate stores too.",
]

def calculate_score(signals: list[Signal]) -> int:
    """Deterministic additive score. Unknown signals contribute zero; total is capped at 100."""
    return max(0, min(100, sum(s.score_contribution for s in signals if s.status == "flagged")))

def build_result(listing: Listing, signals: list[Signal], flags: list[RedFlag], unavailable: list[str], ai_summary: str = "", ai_status: str = "not_configured") -> AnalysisResult:
    score = calculate_score(signals)
    band = "lower_observed_risk" if score < 30 else "caution" if score < 60 else "high_suspicion"
    evidence_count = sum(bool(x.evidence.strip()) and x.status != "unknown" for x in signals)
    completeness = round(100 * evidence_count / max(1, len(signals)))
    has_core_evidence = bool(listing.title or listing.description or listing.seller_name) and len(signals) >= 3
    status = "insufficient_evidence" if not has_core_evidence else "partial" if unavailable or completeness < 70 else "complete"
    if status == "insufficient_evidence":
        unavailable = list(dict.fromkeys(unavailable + ["insufficient_listing_evidence"]))
    return AnalysisResult(listing_url=listing.listing_url, risk_score=score, risk_band=band, assessment_status=status, signals=signals, red_flags=flags, checks_unavailable=list(dict.fromkeys(unavailable)), recommended_actions=ACTIONS, ai_summary=ai_summary, ai_provider_status=ai_status, evidence_completeness=completeness)
