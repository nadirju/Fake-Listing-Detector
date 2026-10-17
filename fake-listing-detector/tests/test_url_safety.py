import pytest
from src.url_safety import validate_url, UnsafeURLError

@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/a", "javascript:alert(1)", "http://127.0.0.1", "http://localhost", "http://169.254.169.254/latest/meta-data", "http://192.168.1.2", "http://[::1]/"])
def test_rejects_unsafe_urls(url):
    with pytest.raises(UnsafeURLError):
        validate_url(url, resolve_dns=False)

def test_accepts_public_https_url_without_dns_lookup():
    assert validate_url("https://example.com/product", resolve_dns=False) == "https://example.com/product"

def test_rejects_embedded_credentials():
    with pytest.raises(UnsafeURLError):
        validate_url("https://user:pass@example.com", resolve_dns=False)
