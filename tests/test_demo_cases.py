from src.pipeline import AnalysisRequest, analyze
from src.reporting import report_to_json, report_to_markdown, share_text


def run(settings, case, lang="en"):
    return analyze(AnalysisRequest(mode="demo", demo_id=case, language=lang), settings)


def test_suspicious_case_is_high_suspicion_with_evidence(settings):
    r = run(settings, "suspicious_watch")
    assert r.risk_band == "high_suspicion" and r.risk_score >= 60
    assert r.demo_notice
    for f in r.red_flags:  # AC-05 / evidence visibility 100%
        assert f.explanation and f.evidence
        assert f.origin == "signal" or f.origin == "model_inference"
    assert {f.signal_id for f in r.red_flags if f.origin == "signal"} >= {"image_search", "price_check", "domain_age"}


def test_legitimate_case_is_low_risk(settings):
    r = run(settings, "legitimate_mug")
    assert r.risk_band == "lower_observed_risk" and r.risk_score < 30 and not r.red_flags


def test_demo_runs_without_any_keys():
    from src.config import Settings

    r = analyze(AnalysisRequest(mode="demo", demo_id="legitimate_mug"), Settings())
    assert r.risk_score < 30


def test_exports_have_disclaimer_and_no_page_text(settings):
    r = run(settings, "suspicious_watch")
    md, js = report_to_markdown(r), report_to_json(r)
    assert "not proof" in md.lower() or "not the probability" in md.lower()
    assert '"page_text": ""' in js
    assert "risk signal(s)" in share_text(r)


def test_urdu_output(settings):
    r = run(settings, "suspicious_watch", "ur")
    assert r.language == "ur" and "شبہ" in report_to_markdown(r)
    assert any("\u0600" <= ch <= "\u06ff" for ch in r.red_flags[0].explanation)


def test_unknown_demo_id(settings):
    import pytest
    from src.errors import InvalidInputError

    with pytest.raises(InvalidInputError):
        run(settings, "nope")
