from datetime import datetime, timedelta, timezone

import pytest

from src.schemas import (
    DomainAgeResult, GroundedAnalysis, ImageMatch, ImageMatchAssessment, ImageSearchResult,
    Inconsistency, Listing, PriceObservation, ReferencePrice,
)
from src.scoring import ScoringInputs, band_for, compute_score
from src.signals.listing_rules import scan_text

NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)


def listing(**kw):
    base = dict(
        url="https://shop.example.com/p", domain="shop.example.com", title="Blue Kettle 1.7L",
        price=40.0, currency="USD", price_text="$40", price_source="structured",
        description="A stainless steel kettle with auto shut-off and a 1.7 litre capacity for the home.",
        seller="Shop", page_text="A stainless steel kettle with auto shut-off. " * 5, page_text_length=1000,
        has_return_policy=True, has_contact_info=True, source="direct",
    )
    base.update(kw)
    return Listing(**base)


def inputs(**kw):
    l = kw.pop("listing", None) or listing()
    return ScoringInputs(listing=l, now=NOW, rules=scan_text({"title": l.title or "", "description": l.description or ""}), **kw)


def domain(days):
    return DomainAgeResult(domain="shop.example.com", status="ok", creation_date=NOW - timedelta(days=days))


def ai(**kw):
    return GroundedAnalysis(**kw)


def test_bands_boundaries():
    assert band_for(0) == band_for(29) == "lower_observed_risk"
    assert band_for(30) == band_for(59) == "caution"
    assert band_for(60) == band_for(100) == "high_suspicion"


def test_determinism():
    kw = dict(domain=domain(5), ai=ai())
    a, b = compute_score(inputs(**kw)), compute_score(inputs(**kw))
    assert a.score == b.score and [s.model_dump() for s in a.signals] == [s.model_dump() for s in b.signals]


def test_unknowns_do_not_change_score_and_are_insufficient():
    out = compute_score(inputs(listing=listing(has_return_policy=None, has_contact_info=None)))
    assert out.score == 0
    assert out.status == "insufficient_evidence"  # only urgency known
    assert "Only 1 check" in out.insufficient_reason


def test_missing_domain_age_neither_adds_nor_removes():
    base = compute_score(inputs(ai=ai()))
    withdom = compute_score(inputs(ai=ai(), domain=DomainAgeResult(domain="x", status="unavailable", reason="r")))
    assert base.score == withdom.score
    sig = next(s for s in withdom.signals if s.id == "domain_age")
    assert sig.status == "unknown" and sig.reason_unavailable


@pytest.mark.parametrize("days,pts", [(5, 10), (29, 10), (30, 5), (89, 5), (90, 0), (4000, 0)])
def test_domain_age_thresholds(days, pts):
    sig = next(s for s in compute_score(inputs(domain=domain(days), ai=ai())).signals if s.id == "domain_age")
    assert sig.score_contribution == pts


def test_price_not_scored_without_reference_or_source():
    out = compute_score(inputs(ai=ai(price_observation=PriceObservation(plausibility="looks_unusually_low", note="cheap"))))
    p = next(s for s in out.signals if s.id == "price_check")
    assert p.score_contribution == 0 and p.note == "heuristic/unverified" and "not live market data" in p.evidence
    ref = ReferencePrice(amount=400, currency="USD", source_url=None)
    p2 = next(s for s in compute_score(inputs(reference=ref, ai=ai())).signals if s.id == "price_check")
    assert p2.score_contribution == 0 and p2.status == "unknown"


@pytest.mark.parametrize("price,pts,status", [(40, 20, "flagged"), (160, 20, "flagged"), (200, 10, "flagged"), (240, 10, "flagged"), (300, 0, "clear")])
def test_price_with_reference(price, pts, status):
    ref = ReferencePrice(amount=400, currency="usd", source_url="https://brand.example/p")
    out = compute_score(inputs(listing=listing(price=price), reference=ref, ai=ai()))
    p = next(s for s in out.signals if s.id == "price_check")
    assert (p.score_contribution, p.status) == (pts, status)


def test_price_currency_mismatch_not_scored():
    ref = ReferencePrice(amount=400, currency="EUR", source_url="https://brand.example/p")
    p = next(s for s in compute_score(inputs(reference=ref, ai=ai())).signals if s.id == "price_check")
    assert p.status == "unknown"


def test_text_pattern_price_never_compared():
    ref = ReferencePrice(amount=400, currency="USD", source_url="https://brand.example/p")
    p = next(s for s in compute_score(inputs(listing=listing(price_source="text_pattern"), reference=ref, ai=ai())).signals if s.id == "price_check")
    assert p.status == "unknown"


def test_correlated_signals_are_capped():
    l = listing(has_return_policy=False, has_contact_info=False)
    out = compute_score(inputs(listing=l, domain=domain(3), ai=ai()))
    d = next(s for s in out.signals if s.id == "domain_age")
    c = next(s for s in out.signals if s.id == "contact_returns")
    assert d.raw_contribution + c.raw_contribution == 20
    assert d.score_contribution + c.score_contribution == 15
    assert "Capped" in (c.note or "")


def _match(link, domain_):
    return ImageMatch(image_url="https://shop.example.com/i.jpg", title="t", link=link, domain=domain_)


def test_image_signal_requires_grounded_unrelated_context():
    m1, m2 = _match("https://a.example.net/x", "example.net"), _match("https://b.example.org/y", "example.org")
    imgs = ImageSearchResult(status="ok", matches=[m1, m2])
    two = ai(image_match_assessments=[
        ImageMatchAssessment(source_url=m1.link, context="unrelated_context", reason="r"),
        ImageMatchAssessment(source_url=m2.link, context="unrelated_context", reason="r"),
    ])
    s = next(x for x in compute_score(inputs(images=imgs, ai=two)).signals if x.id == "image_search")
    assert s.score_contribution == 25 and s.source_url == m1.link
    one = ai(image_match_assessments=[ImageMatchAssessment(source_url=m1.link, context="unrelated_context", reason="r")])
    s = next(x for x in compute_score(inputs(images=imgs, ai=one)).signals if x.id == "image_search")
    assert s.score_contribution == 15
    stock = ai(image_match_assessments=[ImageMatchAssessment(source_url=m1.link, context="manufacturer_or_official", reason="r")])
    s = next(x for x in compute_score(inputs(images=imgs, ai=stock)).signals if x.id == "image_search")
    assert s.status == "clear" and s.score_contribution == 0
    unclear = ai(image_match_assessments=[ImageMatchAssessment(source_url=m1.link, context="unclear", reason="r")])
    s = next(x for x in compute_score(inputs(images=imgs, ai=unclear)).signals if x.id == "image_search")
    assert s.status == "unknown" and s.score_contribution == 0


def test_no_image_match_is_not_proof_and_not_scored():
    imgs = ImageSearchResult(status="ok", matches=[])
    s = next(x for x in compute_score(inputs(images=imgs, ai=ai())).signals if x.id == "image_search")
    assert s.status == "unknown" and s.score_contribution == 0 and "does not prove" in s.evidence
    assert s.reason_unavailable is None


def test_image_matches_without_ai_marked_unavailable():
    imgs = ImageSearchResult(status="ok", matches=[_match("https://a.example.net/x", "example.net")])
    s = next(x for x in compute_score(inputs(images=imgs, ai=None)).signals if x.id == "image_search")
    assert s.status == "unknown" and s.reason_unavailable


def test_consistency_points_by_confidence():
    for conf, pts in (("high", 15), ("medium", 10), ("low", 5)):
        a = ai(inconsistencies=[Inconsistency(description="d", evidence_field="title", evidence_quote="Blue Kettle", confidence=conf)])
        s = next(x for x in compute_score(inputs(ai=a)).signals if x.id == "listing_consistency")
        assert s.score_contribution == pts


def test_urgency_and_payment():
    l = listing(description="Hurry, only 3 left! Sale ends tonight. Pay by Western Union.")
    s = next(x for x in compute_score(inputs(listing=l, ai=ai())).signals if x.id == "urgency_payment")
    assert s.score_contribution == 10 and s.severity == "high"
    l = listing(description="Flash sale on a great kettle, only 3 left in stock for today.")
    s = next(x for x in compute_score(inputs(listing=l, ai=ai())).signals if x.id == "urgency_payment")
    assert s.score_contribution == 5
    l = listing(description="COD available across Pakistan. Cash on delivery accepted. Great kettle for tea.")
    s = next(x for x in compute_score(inputs(listing=l, ai=ai())).signals if x.id == "urgency_payment")
    assert s.score_contribution == 0  # cash on delivery is not suspicious


def test_total_capped_at_100_and_int():
    out = compute_score(inputs(domain=domain(1), ai=ai()))
    assert isinstance(out.score, int) and 0 <= out.score <= 100


def test_no_title_is_insufficient():
    out = compute_score(inputs(listing=listing(title=None), ai=ai()))
    assert out.status == "insufficient_evidence"


def test_complete_vs_partial():
    full = compute_score(inputs(domain=domain(4000), ai=ai(),
                                images=ImageSearchResult(status="ok", matches=[]),
                                reference=ReferencePrice(amount=40, currency="USD", source_url="https://b.example/p")))
    assert full.status == "complete"
    partial = compute_score(inputs(domain=domain(4000), ai=None))
    assert partial.status == "partial"
