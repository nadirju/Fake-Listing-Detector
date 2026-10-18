"""Configuration: environment variables first, then Streamlit secrets.

Secrets are only ever read server-side and are never rendered in the UI.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

try:  # python-dotenv is a convenience for local development only
    from dotenv import load_dotenv

    load_dotenv(override=False)
except Exception:  # pragma: no cover - optional dependency
    pass


def _secret(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value:
        return value
    try:
        import streamlit as st

        if name in st.secrets:
            candidate = st.secrets[name]
            if candidate:
                return str(candidate)
    except Exception:
        # No secrets.toml, or not running inside Streamlit.
        pass
    return default


def _int(name: str, default: int) -> int:
    raw = _secret(name)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = _secret(name)
    try:
        return float(raw) if raw is not None else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # --- credentials (never rendered) ---
    gemini_api_key: Optional[str] = None
    serpapi_api_key: Optional[str] = None
    firecrawl_api_key: Optional[str] = None
    jina_api_key: Optional[str] = None
    # --- providers ---
    gemini_model: str = "gemini-2.5-flash"
    serpapi_engine: str = "google_lens"
    extractor_order: Tuple[str, ...] = ("direct", "firecrawl", "jina")
    # --- timeouts (seconds) ---
    extractor_timeout: float = 12.0
    extraction_budget: float = 25.0
    whois_timeout: float = 8.0
    image_timeout: float = 15.0
    gemini_timeout: float = 25.0
    # --- limits ---
    max_response_bytes: int = 2_000_000
    max_redirects: int = 4
    max_images_to_search: int = 2
    # --- cache / abuse control ---
    cache_ttl_seconds: int = 900
    session_hourly_limit: int = 20
    log_level: str = "INFO"

    def public_status(self) -> Dict[str, bool]:
        """Booleans only - safe to show in the UI."""
        return {
            "gemini": bool(self.gemini_api_key),
            "serpapi": bool(self.serpapi_api_key),
            "firecrawl": bool(self.firecrawl_api_key),
            "jina_key": bool(self.jina_api_key),
        }

    def secret_values(self) -> Tuple[str, ...]:
        return tuple(
            v
            for v in (
                self.gemini_api_key,
                self.serpapi_api_key,
                self.firecrawl_api_key,
                self.jina_api_key,
            )
            if v
        )


def load_settings() -> Settings:
    order_raw = _secret("EXTRACTOR_ORDER", "direct,firecrawl,jina") or "direct,firecrawl,jina"
    order = tuple(
        p.strip().lower()
        for p in order_raw.split(",")
        if p.strip().lower() in {"direct", "firecrawl", "jina"}
    ) or ("direct",)
    settings = Settings(
        gemini_api_key=_secret("GEMINI_API_KEY"),
        serpapi_api_key=_secret("SERPAPI_API_KEY"),
        firecrawl_api_key=_secret("FIRECRAWL_API_KEY"),
        jina_api_key=_secret("JINA_API_KEY"),
        gemini_model=_secret("GEMINI_MODEL", "gemini-2.5-flash") or "gemini-2.5-flash",
        serpapi_engine=_secret("SERPAPI_ENGINE", "google_lens") or "google_lens",
        extractor_order=order,
        extractor_timeout=_float("EXTRACTOR_TIMEOUT", 12.0),
        extraction_budget=_float("EXTRACTION_BUDGET", 25.0),
        whois_timeout=_float("WHOIS_TIMEOUT", 8.0),
        image_timeout=_float("IMAGE_TIMEOUT", 15.0),
        gemini_timeout=_float("GEMINI_TIMEOUT", 25.0),
        max_response_bytes=_int("MAX_RESPONSE_BYTES", 2_000_000),
        max_redirects=_int("MAX_REDIRECTS", 4),
        max_images_to_search=_int("MAX_IMAGES_TO_SEARCH", 2),
        cache_ttl_seconds=_int("CACHE_TTL_SECONDS", 900),
        session_hourly_limit=_int("SESSION_HOURLY_LIMIT", 20),
        log_level=(_secret("LOG_LEVEL", "INFO") or "INFO").upper(),
    )
    from .logging_utils import register_secrets

    register_secrets(settings.secret_values())
    return settings
