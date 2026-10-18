"""Defensive URL validation and redirect-safe fetching helpers."""
import ipaddress
import os
import socket
from urllib.parse import urlparse, urljoin
import requests

MAX_REDIRECTS = 4
MAX_RESPONSE_BYTES = 2_000_000
BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata.google"}

class UnsafeURLError(ValueError):
    pass

def _is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return ip.is_global and not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)

def validate_url(url: str, resolve_dns: bool = True) -> str:
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("Enter a product URL.")
    value = url.strip()
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURLError("The URL is malformed.") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeURLError("Only HTTP and HTTPS URLs are supported.")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise UnsafeURLError("Use a public URL with a hostname and without embedded credentials.")
    host = parsed.hostname.rstrip(".").lower()
    if host in BLOCKED_HOSTS or host.endswith(".localhost") or host.endswith(".local"):
        raise UnsafeURLError("Local and metadata hosts are blocked for safety.")
    if port is not None and not (1 <= port <= 65535):
        raise UnsafeURLError("The URL port is invalid.")
    try:
        ipaddress.ip_address(host)
        if not _is_public_ip(host):
            raise UnsafeURLError("Private, reserved, loopback, and link-local IP addresses are blocked.")
    except ValueError as exc:
        if isinstance(exc, UnsafeURLError):
            raise
        if resolve_dns:
            try:
                records = socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
                ips = {record[4][0].split("%", 1)[0] for record in records}
                if not ips or any(not _is_public_ip(ip) for ip in ips):
                    raise UnsafeURLError("This hostname resolves to a non-public IP address and is blocked.")
            except socket.gaierror as err:
                raise UnsafeURLError("The hostname could not be resolved. Check the URL or use manual input.") from err
    return value

def safe_fetch(url: str, timeout: int = 8, max_bytes: int = MAX_RESPONSE_BYTES) -> tuple[str, bytes, str]:
    """Fetch only public URLs, validating every redirect and streaming a bounded body."""
    if os.getenv("DISABLE_NETWORK", "false").lower() in {"1", "true", "yes"}:
        raise RuntimeError("Network fetching is disabled by configuration.")
    current = validate_url(url)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ListingRiskChecker/1.0; +https://example.invalid/bot)"}
    session = requests.Session()
    try:
        for _ in range(MAX_REDIRECTS + 1):
            validate_url(current)
            response = session.get(current, headers=headers, timeout=(3, timeout), allow_redirects=False, stream=True)
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise RuntimeError("The page returned a redirect without a destination.")
                current = validate_url(urljoin(current, location))
                continue
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type and content_type:
                response.close()
                raise RuntimeError("The URL did not return an HTML product page.")
            chunks, total = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > max_bytes:
                    response.close()
                    raise RuntimeError("The page exceeded the 2 MB extraction limit.")
                chunks.append(chunk)
            final_url = response.url
            response.close()
            validate_url(final_url)
            return final_url, b"".join(chunks), content_type
        raise RuntimeError("The page exceeded the redirect limit.")
    finally:
        session.close()
