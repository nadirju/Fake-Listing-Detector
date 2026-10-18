"""Structured, redacting logger (AC-13, observability requirement)."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from typing import Any, Iterable, Set

from .url_safety import redact_url

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")
_KV_SECRET_RE = re.compile(r"(?i)\b(api[_-]?key|key|token|secret|authorization)=([^&\s\"']+)")
_ENV_SECRET_NAMES = ("GEMINI_API_KEY", "SERPAPI_API_KEY", "FIRECRAWL_API_KEY", "JINA_API_KEY")
_REGISTERED: Set[str] = set()


def register_secrets(values: Iterable[str]) -> None:
    for value in values:
        if value and len(value) >= 6:
            _REGISTERED.add(value)


def redact_text(text: str) -> str:
    text = _URL_RE.sub(lambda m: redact_url(m.group(0)), text)
    text = _KV_SECRET_RE.sub(lambda m: f"{m.group(1)}=***", text)
    secrets = set(_REGISTERED)
    for name in _ENV_SECRET_NAMES:
        value = os.environ.get(name)
        if value and len(value) >= 6:
            secrets.add(value)
    for secret in secrets:
        text = text.replace(secret, "***")
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_text(record.getMessage())
            record.args = ()
        except Exception:
            record.msg = "[log message could not be redacted]"
            record.args = ()
        return True


_LOGGER_NAME = "listing_detector"


def get_logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)
        logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
        logger.propagate = False
    return logger


def log_event(request_id: str, event: str, **fields: Any) -> None:
    payload = {"request_id": request_id, "event": event, **fields}
    get_logger().info(json.dumps(payload, default=str, sort_keys=True))
