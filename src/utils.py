"""Small shared helpers: timeouts, text cleaning, markdown escaping, domains."""
from __future__ import annotations

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import requests
import tldextract

from .errors import ProviderError, ProviderTimeout, UnsafeURLError

# --------------------------------------------------------------------------- #
# Timeouts / error categories
# --------------------------------------------------------------------------- #


def run_with_timeout(fn: Callable[..., Any], timeout: float, *args: Any, **kwargs: Any) -> Any:
    """Run `fn` in a worker thread; raise ProviderTimeout if it exceeds `timeout`."""
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="provider")
    future = executor.submit(fn, *args, **kwargs)
    try:
        done, _ = wait([future], timeout=timeout)
        if not done:
            future.cancel()
            raise ProviderTimeout(f"Timed out after {timeout:.0f}s")
        return future.result()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def error_category(exc: BaseException) -> str:
    """Map an exception to a short, safe category (never includes URLs or secrets)."""
    if isinstance(exc, ProviderTimeout) or isinstance(exc, requests.Timeout):
        return "timeout"
    if isinstance(exc, UnsafeURLError):
        return "blocked_url"
    if isinstance(exc, ProviderError):
        return exc.category
    if isinstance(exc, requests.HTTPError):
        code = getattr(exc.response, "status_code", None)
        if code == 429:
            return "quota_or_rate_limit"
        return "http_error"
    if isinstance(exc, requests.ConnectionError):
        return "connection_error"
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return "invalid_response"
    return "unexpected_error"


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #

_WS = re.compile(r"\s+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value: Optional[str], limit: Optional[int] = None) -> str:
    if not value:
        return ""
    text = _CONTROL.sub("", str(value))
    text = _WS.sub(" ", text).strip()
    return text[:limit] if limit else text


def truncate(value: Optional[str], limit: int) -> str:
    value = value or ""
    return value if len(value) <= limit else value[: max(limit - 1, 0)].rstrip() + "…"


def normalize_for_match(value: str) -> str:
    """Casefold + NFKC + collapsed whitespace, for evidence-grounding checks."""
    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = value.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    return _WS.sub(" ", value).strip()


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-!|>~$<&:])")


def escape_md(value: Optional[str]) -> str:
    """Escape untrusted text before it is passed to st.markdown.

    Prevents injected markdown (links, remote images, colour directives) from rendering.
    """
    text = clean_text(value)
    text = _MD_SPECIAL.sub(r"\\\1", text)
    return text.replace("\\://", "\\:\u200b//")


# --------------------------------------------------------------------------- #
# Domains
# --------------------------------------------------------------------------- #

_TLD = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)  # bundled snapshot, no network


def registrable_domain(host_or_url: Optional[str]) -> str:
    """Return the registrable domain (example.co.uk) for a host or URL; '' if unknown."""
    if not host_or_url:
        return ""
    value = host_or_url.strip()
    if "://" in value:
        value = urlsplit(value).hostname or ""
    value = value.lower().rstrip(".")
    if not value:
        return ""
    ext = _TLD(value)
    if ext.suffix and ext.domain:
        return f"{ext.domain}.{ext.suffix}"
    labels = value.split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else value
