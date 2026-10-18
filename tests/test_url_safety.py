import socket

import pytest
import requests

from src import url_safety
from src.errors import FetchError, UnresolvableHostError, UnsafeURLError
from src.url_safety import (
    is_public_ip,
    normalize_url_for_cache,
    redact_url,
    safe_get,
    validate_url,
)


@pytest.fixture
def fake_dns(monkeypatch):
    table = {}

    def fake_getaddrinfo(host, port, *a, **k):
        if host not in table:
            raise socket.gaierror("nope")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in table[host]]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    return table


@pytest.mark.parametrize(
    "raw",
    [
        "", "   ", "not a url", "example.com/path", "ftp://example.com/file", "file:///etc/passwd",
        "javascript:alert(1)", "http://", "https://user:pw@example.com/", "http://exa mple.com/",
        "http://example.com\\@127.0.0.1/", "http://example.com:8080/", "http://localhost/", "http://app.localhost/",
        "http://printer.local/", "http://intranet/", "http://metadata.google.internal/",
        "http://127.0.0.1/", "http://10.0.0.5/", "http://192.168.1.1/", "http://172.16.0.1/",
        "http://169.254.169.254/latest/meta-data/", "http://100.100.100.200/", "http://0.0.0.0/",
        "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://[fe80::1]/", "http://[fd00:ec2::254]/",
        "http://[64:ff9b::7f00:1]/", "https://" + "a" * 2100 + ".com/",
    ],
)
def test_rejects_unsafe_or_malformed(raw, fake_dns):
    with pytest.raises(UnsafeURLError):
        validate_url(raw)


def test_non_string_rejected():
    with pytest.raises(UnsafeURLError):
        validate_url(None)  # type: ignore[arg-type]


def test_accepts_public_url_and_normalizes(fake_dns):
    fake_dns["shop.example.com"] = ["93.184.216.34"]
    safe = validate_url("  HTTPS://Shop.Example.com/p?id=1#frag ")
    assert safe.url == "https://shop.example.com/p?id=1"
    assert safe.ips == ("93.184.216.34",)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1"])
def test_hostname_resolving_to_private_ip_is_blocked(ip, fake_dns):
    fake_dns["evil.example.com"] = [ip]
    with pytest.raises(UnsafeURLError):
        validate_url("http://evil.example.com/")


def test_mixed_public_and_private_resolution_is_blocked(fake_dns):
    fake_dns["rebind.example.com"] = ["93.184.216.34", "127.0.0.1"]
    with pytest.raises(UnsafeURLError):
        validate_url("http://rebind.example.com/")


def test_decimal_and_hex_ip_forms_blocked_via_resolution(monkeypatch):
    # The OS resolver turns 2130706433 / 0x7f000001 into 127.0.0.1; we validate the result.
    def fake(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    for host in ("2130706433", "0x7f000001", "017700000001", "127.1"):
        with pytest.raises(UnsafeURLError):
            validate_url(f"http://{host}/")


def test_unresolvable_host(fake_dns):
    with pytest.raises(UnresolvableHostError):
        validate_url("http://does-not-exist.example.com/")


def test_is_public_ip():
    assert is_public_ip("93.184.216.34")
    assert not is_public_ip("not-an-ip")
    assert not is_public_ip("224.0.0.1")


def test_redact_url_masks_sensitive_params():
    out = redact_url("https://u:p@shop.com/x?token=abc&utm_source=a&color=red&k=" + "z" * 30)
    assert "abc" not in out and "u:p" not in out and "z" * 30 not in out
    assert "color=red" in out


def test_cache_normalization_drops_tracking():
    a = normalize_url_for_cache("https://Shop.com/p?utm_source=x&b=2&a=1#top")
    b = normalize_url_for_cache("https://shop.com/p?a=1&b=2&fbclid=zzz")
    assert a == b


# ---- safe_get with a fake session --------------------------------------------------------


class FakeResp:
    def __init__(self, status=200, headers=None, body=b"<html></html>"):
        self.status_code = status
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self._body = body
        self.closed = False

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requested = []

    def get(self, url, **kw):
        self.requested.append(url)
        assert kw["allow_redirects"] is False
        return self.responses.pop(0)


def test_safe_get_revalidates_redirect_targets(fake_dns):
    fake_dns["shop.example.com"] = ["93.184.216.34"]
    session = FakeSession([FakeResp(302, {"Location": "http://169.254.169.254/latest/meta-data/"})])
    with pytest.raises(UnsafeURLError):
        safe_get("https://shop.example.com/p", session=session)
    assert session.requested == ["https://shop.example.com/p"]  # metadata URL never requested


def test_safe_get_blocks_redirect_to_hostname_resolving_private(fake_dns):
    fake_dns["shop.example.com"] = ["93.184.216.34"]
    fake_dns["internal.example.com"] = ["10.0.0.9"]
    session = FakeSession([FakeResp(301, {"Location": "https://internal.example.com/"})])
    with pytest.raises(UnsafeURLError):
        safe_get("https://shop.example.com/p", session=session)


def test_safe_get_follows_safe_redirect_and_returns_final_url(fake_dns):
    fake_dns["a.example.com"] = ["93.184.216.34"]
    fake_dns["b.example.com"] = ["93.184.216.35"]
    session = FakeSession([FakeResp(302, {"Location": "https://b.example.com/x"}), FakeResp(body=b"<p>hi</p>")])
    res = safe_get("https://a.example.com/", session=session)
    assert res.final_url == "https://b.example.com/x" and "hi" in res.text


def test_safe_get_too_many_redirects(fake_dns):
    fake_dns["a.example.com"] = ["93.184.216.34"]
    session = FakeSession([FakeResp(302, {"Location": "https://a.example.com/n"}) for _ in range(10)])
    with pytest.raises(FetchError):
        safe_get("https://a.example.com/", max_redirects=3, session=session)


def test_safe_get_truncates_large_body(fake_dns):
    fake_dns["a.example.com"] = ["93.184.216.34"]
    session = FakeSession([FakeResp(body=b"x" * 50_000)])
    res = safe_get("https://a.example.com/", max_bytes=10_000, session=session)
    assert res.truncated and len(res.text) == 10_000


def test_safe_get_rejects_binary_content_type(fake_dns):
    fake_dns["a.example.com"] = ["93.184.216.34"]
    session = FakeSession([FakeResp(headers={"Content-Type": "application/zip"})])
    with pytest.raises(FetchError):
        safe_get("https://a.example.com/", session=session)


def test_safe_get_reports_blocked_sites(fake_dns):
    from src.errors import BlockedByPageError

    fake_dns["a.example.com"] = ["93.184.216.34"]
    session = FakeSession([FakeResp(status=403)])
    with pytest.raises(BlockedByPageError):
        safe_get("https://a.example.com/", session=session)


def test_connect_time_guard_blocks_rebinding(monkeypatch):
    """Validation passes (simulated), but the address used at connect time is private."""
    monkeypatch.setattr(url_safety, "validate_url", lambda u, resolve=True: url_safety.SafeURL(u, "http", "localhost", 80))
    with pytest.raises(UnsafeURLError):
        safe_get("http://localhost:9/", timeout=2)
