"""URL validation and SSRF-safe fetching.

Defence in depth (AC-01, section 8 of the PRD):
1. Syntax checks: http/https only, no credentials, ports 80/443, no whitespace,
   no obviously internal host names.
2. DNS resolution: every resolved address must be globally routable. Loopback,
   private, link-local (incl. cloud metadata 169.254.169.254), CGNAT, multicast
   and reserved ranges are blocked. IPv4-mapped / 6to4 / NAT64 IPv6 forms are
   unwrapped or blocked.
3. Redirects are followed manually and every hop is re-validated.
4. A connect-time guard re-resolves and re-validates the address actually used
   for the TCP connection (protects against DNS rebinding / TOCTOU).
5. Streaming reads with a hard byte limit (also bounds decompression bombs).
"""
from __future__ import annotations

import ipaddress
import re
import socket
import threading
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from urllib3.util import connection as _u3conn

from .errors import (
    BlockedByPageError,
    FetchError,
    UnresolvableHostError,
    UnsafeURLError,
)

MAX_URL_LENGTH = 2048
ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_PORTS = frozenset({80, 443})

BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
        "instance-data",
        "kubernetes.default.svc",
    }
)
BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".intranet",
    ".lan",
    ".home",
    ".corp",
    ".private",
    ".home.arpa",
    ".cluster.local",
)

_EXTRA_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "100.64.0.0/10",  # CGNAT (includes Alibaba metadata 100.100.100.200)
        "169.254.0.0/16",  # link-local incl. AWS/GCP/Azure metadata
        "192.0.0.0/24",
        "198.18.0.0/15",
        "240.0.0.0/4",
        "64:ff9b::/96",  # NAT64
        "64:ff9b:1::/48",
        "fd00:ec2::/32",  # AWS IPv6 metadata
    )
)

_BAD_CHARS = re.compile(r"[\s\x00-\x1f\x7f\\]")

# --------------------------------------------------------------------------- #
# IP / host validation
# --------------------------------------------------------------------------- #


def is_public_ip(value: str) -> bool:
    """True only for globally routable unicast addresses."""
    try:
        ip = ipaddress.ip_address(value.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
        elif ip.teredo is not None:
            return False
    if not ip.is_global or ip.is_multicast:
        return False
    return not any(ip in net for net in _EXTRA_BLOCKED_NETWORKS)


def resolve_host(host: str) -> List[str]:
    """Resolve `host` and ensure *every* address is public. Returns the IPs."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        raise UnresolvableHostError("The hostname could not be resolved.") from None
    ips: List[str] = []
    for info in infos:
        ip = str(info[4][0]).split("%")[0]
        if ip not in ips:
            ips.append(ip)
    if not ips:
        raise UnresolvableHostError("The hostname could not be resolved.")
    for ip in ips:
        if not is_public_ip(ip):
            raise UnsafeURLError(
                "This address points to a private, local or reserved network and is blocked."
            )
    return ips


@dataclass(frozen=True)
class SafeURL:
    url: str
    scheme: str
    host: str
    port: int
    ips: Tuple[str, ...] = field(default_factory=tuple)


def validate_url(raw: object, *, resolve: bool = True) -> SafeURL:
    """Validate a user-supplied URL. Raises UnsafeURLError with a user-safe message."""
    if not isinstance(raw, str) or not raw.strip():
        raise UnsafeURLError("Please enter a product URL.")
    raw = raw.strip()
    if len(raw) > MAX_URL_LENGTH:
        raise UnsafeURLError("The URL is too long.")
    if _BAD_CHARS.search(raw):
        raise UnsafeURLError("The URL contains spaces, backslashes or control characters.")
    try:
        parts = urlsplit(raw)
    except ValueError:
        raise UnsafeURLError("The URL is malformed.") from None
    scheme = parts.scheme.lower()
    if not scheme or not parts.netloc:
        raise UnsafeURLError("The URL must start with http:// or https:// and include a host name.")
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"Unsupported URL scheme '{scheme}'. Only http and https are allowed.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise UnsafeURLError("URLs containing credentials are not accepted.")
    try:
        port = parts.port
    except ValueError:
        raise UnsafeURLError("The URL contains an invalid port.") from None
    port = port or (443 if scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise UnsafeURLError("Only the standard web ports 80 and 443 are allowed.")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURLError("The URL is malformed.")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not is_public_ip(host):
            raise UnsafeURLError(
                "This address points to a private, local or reserved network and is blocked."
            )
    else:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise UnsafeURLError("The host name is not valid.") from None
        if host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_SUFFIXES):
            raise UnsafeURLError("Internal or local host names are blocked.")
        if "." not in host:
            raise UnsafeURLError("The host name must be a fully qualified domain name.")

    ips = tuple(resolve_host(host)) if resolve else ()
    netloc = host if ":" not in host else f"[{host}]"
    default_port = 443 if scheme == "https" else 80
    if parts.port and parts.port != default_port:
        netloc = f"{netloc}:{parts.port}"
    normalized = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
    return SafeURL(url=normalized, scheme=scheme, host=host, port=port, ips=ips)


# --------------------------------------------------------------------------- #
# Redaction / normalisation
# --------------------------------------------------------------------------- #

_SENSITIVE_PARAM = re.compile(
    r"(token|key|secret|sig|signature|auth|session|sess|sid|pass|pwd|password|code|jwt|"
    r"credential|bearer|otp|email|phone|nonce|csrf|hash|ticket)",
    re.IGNORECASE,
)
_TRACKING_PARAMS = frozenset(
    {"fbclid", "gclid", "msclkid", "dclid", "igshid", "mc_cid", "mc_eid", "ref", "ref_src", "_ga", "yclid"}
)


def redact_url(url: str) -> str:
    """Return the URL with credentials removed and sensitive query values masked."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parts.port:
            host = f"{host}:{parts.port}"
        pairs = []
        for k, v in parse_qsl(parts.query, keep_blank_values=True):
            if _SENSITIVE_PARAM.search(k) or len(v) >= 24:
                v = "REDACTED"
            pairs.append((k, v))
        return urlunsplit((parts.scheme, host, parts.path, urlencode(pairs), ""))
    except Exception:
        return "[unparseable-url]"


def normalize_url_for_cache(url: str) -> str:
    """Canonical form used as a cache key: lowercase host, no fragment/tracking params."""
    try:
        parts = urlsplit(url.strip())
        pairs = sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
        )
        host = (parts.hostname or "").lower()
        if parts.port and parts.port not in (80, 443):
            host = f"{host}:{parts.port}"
        path = parts.path or "/"
        return urlunsplit((parts.scheme.lower(), host, path, urlencode(pairs), ""))
    except Exception:
        return url.strip()


# --------------------------------------------------------------------------- #
# Connect-time guard (DNS rebinding protection)
# --------------------------------------------------------------------------- #


def _install_guard():
    current = _u3conn.create_connection
    if getattr(current, "_is_safe_guard", False):
        return current

    original = current
    local = threading.local()

    def guarded_create_connection(address, *args, **kwargs):
        if getattr(local, "active", False):
            host, port = address[0], address[1]
            host = str(host).strip("[]")
            ips = resolve_host(host)  # re-validates what we are about to connect to
            last_error: Optional[BaseException] = None
            for ip in ips:
                try:
                    return original((ip, port), *args, **kwargs)
                except OSError as exc:
                    last_error = exc
            raise last_error or OSError("connection failed")
        return original(address, *args, **kwargs)

    guarded_create_connection._is_safe_guard = True  # type: ignore[attr-defined]
    guarded_create_connection._local = local  # type: ignore[attr-defined]
    _u3conn.create_connection = guarded_create_connection
    return guarded_create_connection


_GUARD = _install_guard()

# --------------------------------------------------------------------------- #
# Safe fetch
# --------------------------------------------------------------------------- #

DEFAULT_ALLOWED_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "application/json",
    "application/ld+json",
)
_BLOCK_STATUSES = frozenset({401, 403, 429, 503})
USER_AGENT = (
    "Mozilla/5.0 (compatible; ListingRiskChecker/1.0) "
    "AppleWebKit/537.36 (KHTML, like Gecko)"
)


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    text: str
    truncated: bool = False
    redirects: List[str] = field(default_factory=list)


def safe_get(
    url: str,
    *,
    timeout: float = 10.0,
    max_bytes: int = 2_000_000,
    max_redirects: int = 4,
    headers: Optional[Dict[str, str]] = None,
    allowed_content_types: Iterable[str] = DEFAULT_ALLOWED_CONTENT_TYPES,
    session: Optional[requests.Session] = None,
) -> FetchResult:
    """GET `url` with SSRF protection, manual redirects and a hard size limit."""
    own_session = session is None
    if session is None:
        session = requests.Session()
        session.trust_env = False  # ignore proxies so the connect-time guard sees real IPs
    hdrs = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.8,*/*;q=0.5",
        "Accept-Language": "en;q=0.9",
    }
    if headers:
        hdrs.update(headers)
    allowed = tuple(allowed_content_types)
    redirects: List[str] = []
    current = url
    _GUARD._local.active = True  # type: ignore[attr-defined]
    try:
        for _ in range(max_redirects + 1):
            safe = validate_url(current)  # every hop is re-validated
            try:
                resp = session.get(
                    safe.url,
                    headers=hdrs,
                    timeout=(min(5.0, timeout), timeout),
                    allow_redirects=False,
                    stream=True,
                )
            except UnsafeURLError:
                raise
            except requests.RequestException as exc:
                cause = exc.__cause__ or exc.__context__
                if isinstance(cause, UnsafeURLError):
                    raise cause from None
                raise FetchError(f"Network error ({type(exc).__name__}).") from None
            try:
                status = resp.status_code
                if status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location")
                    if not location:
                        raise FetchError("Redirect without a Location header.")
                    current = urljoin(safe.url, location)
                    redirects.append(redact_url(current))
                    continue
                ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if status in _BLOCK_STATUSES:
                    raise BlockedByPageError(
                        f"The site returned HTTP {status} and appears to block automated access."
                    )
                if status >= 400:
                    raise FetchError(f"The site returned HTTP {status}.")
                if ctype and not any(ctype == a or ctype.startswith(a) for a in allowed):
                    raise FetchError(f"Unsupported content type '{ctype}'.")
                chunks: List[bytes] = []
                total = 0
                truncated = False
                for chunk in resp.iter_content(chunk_size=16384):
                    if not chunk:
                        continue
                    if total + len(chunk) > max_bytes:
                        chunks.append(chunk[: max_bytes - total])
                        truncated = True
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                body = b"".join(chunks)
                charset = None
                match = re.search(r"charset=([\w-]+)", resp.headers.get("Content-Type", ""), re.I)
                if match:
                    charset = match.group(1)
                try:
                    text = body.decode(charset or "utf-8", errors="replace")
                except LookupError:
                    text = body.decode("utf-8", errors="replace")
                return FetchResult(
                    url=url,
                    final_url=safe.url,
                    status_code=status,
                    content_type=ctype,
                    text=text,
                    truncated=truncated,
                    redirects=redirects,
                )
            finally:
                resp.close()
        raise FetchError("Too many redirects.")
    finally:
        _GUARD._local.active = False  # type: ignore[attr-defined]
        if own_session:
            session.close()
