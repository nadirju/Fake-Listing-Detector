import pytest
from pydantic import ValidationError
from src.schemas import AnalysisResult, Listing, Signal

def test_result_score_bounds_are_validated():
    with pytest.raises(ValidationError):
        AnalysisResult(listing_url="https://example.com", risk_score=101, risk_band="caution", assessment_status="partial")

def test_listing_defaults_are_safe():
    listing = Listing()
    assert listing.image_urls == []
    assert listing.price is None

def test_signal_rejects_unknown_status():
    with pytest.raises(ValidationError):
        Signal(id="a", title="A", status="maybe", evidence="e")
