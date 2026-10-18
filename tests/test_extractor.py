import pytest

from src.config import Settings
from src.extractor import (
    extract_direct, extract_firecrawl, extract_jina, find_price, is_sufficient, listing_from_manual,
    merge_listings, parse_amount, parse_html, parse_markdown,
)
from src.schemas import ManualInput

HTML = """<html><head><title>Mug page</title>
<meta property="og:site_name" content="Northfield"><meta property="og:image" content="/img/m.jpg">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Product","name":"Stoneware Mug",
"description":"<p>Nice <b>mug</b></p>","brand":{"@type":"Brand","name":"Northfield"},"image":["/a.jpg",{"url":"https://cdn.example.com/b.jpg"}],
"offers":[{"@type":"Offer","price":"1,299.50","priceCurrency":"PKR","seller":{"@type":"Organization","name":"Northfield Co"}}]}]}</script>
<script>var x = 1;</script></head><body><h1>Stoneware Mug</h1>
<a href="/policies/refund-policy">Refund policy</a><a href="mailto:hi@x.com">Contact</a></body></html>"""


def test_parse_html_structured():
    l = parse_html(HTML, "https://shop.example.com/p/1", "direct")
    assert (l.title, l.price, l.currency, l.seller, l.brand) == ("Stoneware Mug", 1299.5, "PKR", "Northfield Co", "Northfield")
    assert l.description == "Nice mug" and l.price_source == "structured"
    assert "https://shop.example.com/a.jpg" in l.image_urls and "https://cdn.example.com/b.jpg" in l.image_urls
    assert l.has_return_policy is True and l.has_contact_info is True
    assert "var x" not in l.page_text
    assert is_sufficient(l)


def test_parse_html_fallbacks_and_unknowns_not_fabricated():
    l = parse_html("<html><head><title>Only title</title></head><body><p>hi</p></body></html>", "https://a.example.com/", "direct")
    assert l.title == "Only title" and l.price is None and l.seller is None and l.image_urls == []
    assert l.has_return_policy is None and l.has_contact_info is None  # short page -> unknown, not False
    assert not is_sufficient(l)


def test_missing_policies_false_only_on_complete_page():
    body = "<p>" + ("Great kettle with many features and a long description. " * 30) + "</p>"
    l = parse_html(f"<html><head><title>K</title></head><body>{body}</body></html>", "https://a.example.com/", "direct")
    assert l.has_return_policy is False and l.has_contact_info is False


def test_text_pattern_price_marked():
    body = "<p>Kettle now only $24.99 today</p>"
    l = parse_html(f"<html><head><title>K</title></head><body>{body}</body></html>", "https://a.example.com/", "direct")
    assert l.price == 24.99 and l.price_source == "text_pattern"


def test_parse_amount_and_find_price():
    assert parse_amount("1,299.00") == 1299 and parse_amount("1.299,00") == 1299 and parse_amount("12,50") == 12.5
    assert parse_amount("abc") is None and parse_amount("0") is None
    assert find_price("Now Rs. 2,499 only")[:2] == (2499.0, "PKR")
    assert find_price("no price here") == (None, None, None)


def test_parse_markdown():
    md = "# Great Kettle\n\n![k](https://cdn.example.com/k.jpg)\n\nA long paragraph about a kettle that is really quite comfortable to hold and use daily.\n\nPrice: €29,90"
    l = parse_markdown(md, "https://a.example.com/p", "jina")
    assert l.title == "Great Kettle" and l.price == 29.9 and l.currency == "EUR" and l.image_urls == ["https://cdn.example.com/k.jpg"]


def test_merge_listings_fills_gaps():
    a = parse_html("<html><head><title>T</title></head><body></body></html>", "https://a.example.com/", "direct")
    b = parse_markdown("# T\n\nPrice $5.00 for this item", "https://a.example.com/", "jina")
    m = merge_listings(a, b)
    assert m.price == 5.0 and m.sources == ["direct", "jina"]


def test_manual_listing():
    l = listing_from_manual(ManualInput(title="K", price_text="Rs. 2,499", description="d", image_urls=["https://a.example.com/i.jpg", "javascript:x", "https://a.example.com/i.jpg"]))
    assert l.price == 2499 and l.currency == "PKR" and l.price_source == "manual" and l.image_urls == ["https://a.example.com/i.jpg"]


def test_extract_direct_detects_captcha():
    from src.errors import BlockedByPageError

    class R:
        text = "<html><body>Please verify you are a human. CAPTCHA</body></html>"
        final_url = "https://a.example.com/"

    with pytest.raises(BlockedByPageError):
        extract_direct("https://a.example.com/", Settings(), fetch=lambda *a, **k: R())


class Resp:
    def __init__(self, payload=None, text="", status=200):
        self._p, self.text, self.status_code = payload, text, status

    def raise_for_status(self):
        pass

    def json(self):
        if self._p is None:
            raise ValueError("no json")
        return self._p


def test_firecrawl_html_and_markdown():
    s = Settings(firecrawl_api_key="k" * 8)
    seen = {}

    def post(url, headers=None, json=None, timeout=None):
        seen.update(headers=headers, json=json)
        return Resp({"success": True, "data": {"html": HTML, "metadata": {}}})

    l = extract_firecrawl("https://shop.example.com/p/1", s, http_post=post)
    assert l.title == "Stoneware Mug" and l.source == "firecrawl" and seen["headers"]["Authorization"].startswith("Bearer ")
    l = extract_firecrawl("https://a.example.com/", s, http_post=lambda *a, **k: Resp({"success": True, "data": {"markdown": "# Hi\n\nPrice $3.00 for it", "metadata": {"title": "Hi"}}}))
    assert l.title == "Hi" and l.price == 3.0


def test_jina_json_and_text():
    s = Settings()
    l = extract_jina("https://a.example.com/", s, http_get=lambda *a, **k: Resp({"data": {"title": "T", "content": "Price $9.99 body", "images": {"a": "https://c.example.com/a.jpg"}}}))
    assert l.title == "T" and l.price == 9.99 and l.image_urls == ["https://c.example.com/a.jpg"]
    l = extract_jina("https://a.example.com/", s, http_get=lambda *a, **k: Resp(None, text="# Plain Title\nPrice $1.00 stuff"))
    assert l.title == "Plain Title"
