"""Shared exception types. Messages must never contain secrets or full URLs."""
from __future__ import annotations

from typing import List, Optional


class DetectorError(Exception):
    """Base class for all application errors."""


class InvalidInputError(DetectorError, ValueError):
    """User supplied input that cannot be analysed (shown verbatim in the UI)."""


class UnsafeURLError(DetectorError, ValueError):
    """URL failed validation (scheme, host, private IP, redirect target...)."""


class UnresolvableHostError(UnsafeURLError):
    """Hostname could not be resolved via DNS."""


class FetchError(DetectorError):
    """A page could not be fetched (HTTP error, bad content type, too many redirects)."""


class BlockedByPageError(FetchError):
    """The site refused automated access (CAPTCHA, 403...). We never try to bypass it."""


class ProviderTimeout(DetectorError):
    """A provider call exceeded its time budget."""


class ProviderError(DetectorError):
    """A third-party provider returned an error. `category` is safe to display/log."""

    def __init__(self, message: str, category: str = "provider_error") -> None:
        super().__init__(message)
        self.category = category


class AnalysisError(DetectorError):
    """The AI analysis failed after the bounded retry."""

    def __init__(self, message: str, category: str = "ai_error") -> None:
        super().__init__(message)
        self.category = category


class ExtractionFailed(DetectorError):
    """No usable listing could be extracted; the UI should offer manual input."""

    def __init__(self, message: str, statuses: Optional[List[object]] = None) -> None:
        super().__init__(message)
        self.statuses = statuses or []
