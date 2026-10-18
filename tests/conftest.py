import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.cache import clear_all_caches
from src.config import Settings


@pytest.fixture(autouse=True)
def _clean_caches():
    clear_all_caches()
    yield
    clear_all_caches()


@pytest.fixture
def settings():
    return Settings(
        gemini_api_key="test-gemini-key-123456",
        serpapi_api_key="test-serp-key-123456",
        extractor_timeout=1.0,
        extraction_budget=5.0,
        whois_timeout=0.5,
        image_timeout=0.5,
        gemini_timeout=4.0,
    )
