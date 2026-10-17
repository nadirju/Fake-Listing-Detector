from src import analyzer
from src.schemas import Listing

def test_extraction_failure_degrades_to_insufficient_evidence(monkeypatch):
    monkeypatch.setattr("src.url_safety.socket.getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))])
    monkeypatch.setattr(analyzer, "extract_listing", lambda url: (_ for _ in ()).throw(TimeoutError("timeout")))
    monkeypatch.setattr(analyzer, "check_domain_age", lambda domain: {"status":"unavailable", "message":"unavailable", "age_days":None})
    monkeypatch.setattr(analyzer, "reverse_image_search", lambda image: {"status":"unavailable", "message":"unavailable", "matches":[]})
    monkeypatch.setattr(analyzer, "analyze_with_gemini", lambda listing, signals, flags: ("fallback", "not_configured"))
    result = analyzer.analyze_listing("https://example.com/product", use_cache=False)
    assert result["result"].assessment_status == "insufficient_evidence"
    assert "listing_extraction: TimeoutError" in result["result"].checks_unavailable

def test_manual_listing_is_supported(monkeypatch):
    monkeypatch.setattr(analyzer, "check_domain_age", lambda domain: {"status":"unavailable", "message":"unavailable", "age_days":None})
    monkeypatch.setattr(analyzer, "reverse_image_search", lambda image: {"status":"unavailable", "message":"unavailable", "matches":[]})
    monkeypatch.setattr(analyzer, "analyze_with_gemini", lambda listing, signals, flags: ("fallback", "not_configured"))
    manual = Listing(listing_url="https://example.com/product", title="Ceramic coffee mug", description="Contact us; return policy: 30 days", domain="example.com", contact_info_found=True, return_policy_found=True)
    result = analyzer.analyze_listing(manual.listing_url, manual=manual, use_cache=False)
    assert result["listing"].title == "Ceramic coffee mug"
    assert 0 <= result["result"].risk_score <= 100
