from src.schemas import Signal, Listing, RedFlag
from src.scoring import calculate_score, build_result

def signal(status="flagged", contribution=10):
    return Signal(id="x", title="test", status=status, evidence="observed text", score_contribution=contribution)

def test_score_is_deterministic_and_caps_at_100():
    signals = [signal(contribution=40), signal(contribution=70)]
    assert calculate_score(signals) == 100
    assert calculate_score(signals) == calculate_score(signals)

def test_unknown_signals_do_not_change_score():
    assert calculate_score([signal(contribution=10), signal(status="unknown", contribution=99)]) == 10

def test_risk_bands():
    listing = Listing(title="A useful product title", description="Description", listing_url="https://example.com")
    low = build_result(listing, [signal(contribution=10)], [], [])
    medium = build_result(listing, [signal(contribution=30)], [], [])
    high = build_result(listing, [signal(contribution=60)], [], [])
    assert low.risk_band == "lower_observed_risk"
    assert medium.risk_band == "caution"
    assert high.risk_band == "high_suspicion"
