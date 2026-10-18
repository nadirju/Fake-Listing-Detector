"""WHOIS domain-age lookup (AC-10). Missing/redacted data is 'unavailable', never evidence."""
from __future__ import annotations

import ipaddress
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Tuple

from ..schemas import DomainAgeResult, ProviderStatus
from ..utils import error_category, registrable_domain, run_with_timeout

EARLIEST_PLAUSIBLE = datetime(1985, 1, 1, tzinfo=timezone.utc)


def _to_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, (list, tuple, set)):
        parsed = [d for d in (_to_datetime(v) for v in value) if d is not None]
        return min(parsed) if parsed else None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%b-%Y", "%Y.%m.%d"):
            try:
                dt = datetime.strptime(text[:25] if "%z" in fmt else text, fmt)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _default_whois(domain: str) -> Any:
    import whois  # python-whois

    return whois.whois(domain)


def lookup_domain_age(
    host: str,
    *,
    timeout: float = 8.0,
    lookup_fn: Optional[Callable[[str], Any]] = None,
    now: Optional[datetime] = None,
) -> Tuple[DomainAgeResult, ProviderStatus]:
    """Look up the creation date of the registrable domain of `host`."""
    started = time.monotonic()
    now = now or datetime.now(timezone.utc)
    domain = registrable_domain(host)

    def finish(result: DomainAgeResult, status: str, detail: str) -> Tuple[DomainAgeResult, ProviderStatus]:
        return result, ProviderStatus(
            provider="whois",
            status=status,  # type: ignore[arg-type]
            detail=detail,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    try:
        ipaddress.ip_address(host)
        return finish(
            DomainAgeResult(domain=host, status="unavailable", reason="The listing uses an IP address, not a domain name."),
            "skipped",
            "ip_literal",
        )
    except ValueError:
        pass
    if not domain:
        return finish(
            DomainAgeResult(domain=host or "", status="unavailable", reason="No registrable domain could be determined."),
            "skipped",
            "no_domain",
        )

    try:
        record = run_with_timeout(lookup_fn or _default_whois, timeout, domain)
    except Exception as exc:  # isolated: never propagates
        category = error_category(exc)
        return finish(
            DomainAgeResult(domain=domain, status="unavailable", reason=f"WHOIS lookup failed ({category})."),
            "timeout" if category == "timeout" else "error",
            category,
        )

    raw_date = None
    registrar = None
    try:
        raw_date = record.get("creation_date") if hasattr(record, "get") else getattr(record, "creation_date", None)
        registrar = record.get("registrar") if hasattr(record, "get") else getattr(record, "registrar", None)
    except Exception:
        raw_date = None
    created = _to_datetime(raw_date)
    if created is None:
        return finish(
            DomainAgeResult(
                domain=domain,
                status="unavailable",
                reason="WHOIS returned no creation date (the record may be redacted or missing).",
            ),
            "unavailable",
            "no_creation_date",
        )
    if created > now or created < EARLIEST_PLAUSIBLE:
        return finish(
            DomainAgeResult(domain=domain, status="unavailable", reason="WHOIS returned an implausible creation date."),
            "unavailable",
            "implausible_date",
        )
    if isinstance(registrar, (list, tuple)):
        registrar = registrar[0] if registrar else None
    return finish(
        DomainAgeResult(
            domain=domain,
            status="ok",
            creation_date=created,
            registrar=str(registrar)[:120] if registrar else None,
        ),
        "ok",
        "found",
    )
