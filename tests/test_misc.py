import json
import logging
import re

from src.cache import TTLCache, fingerprint
from src.i18n import EN, UR, t
from src.logging_utils import redact_text, register_secrets
from src.signals.listing_rules import scan_text
from src.utils import escape_md


def test_ttl_cache_expiry_and_eviction():
    now = [0.0]
    c = TTLCache(10, maxsize=2, clock=lambda: now[0])
    c.set("a", 1); c.set("b", 2); c.set("c", 3)
    assert c.get("a") is None and c.get("b") == 2
    now[0] = 11
    assert c.get("b") is None and len(c) <= 2


def test_fingerprint_stable():
    assert fingerprint({"a": 1, "b": [1, 2]}) == fingerprint({"b": [1, 2], "a": 1})
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})


def test_log_redaction():
    register_secrets(["super-secret-value-123"])
    out = redact_text("call https://api.x.com/a?api_key=abcdef&q=1 with super-secret-value-123 token=zzz")
    assert "abcdef" not in out and "super-secret-value-123" not in out and "zzz" not in out


def test_escape_md_neutralizes_markup():
    out = escape_md("![t](http://evil.example/p.png) [x](http://e.com) :red[a] <b>x</b> $x$ https://e.com")
    assert "![" not in out and "](" not in out and "<b>" not in out and "https://" not in out


def test_urdu_has_all_keys_except_placeholder():
    assert set(EN) - set(UR) <= {"url_placeholder"}
    assert t("missing_key_x", "ur") == "missing_key_x"
    assert t("band_caution", "ur") != t("band_caution", "en")


def test_ui_keys_exist():
    src = open("app.py", encoding="utf-8").read() + open("src/reporting.py", encoding="utf-8").read()
    for key in re.findall(r"""\bt\(\s*["']([a-z_0-9]+)["']""", src):
        if not key.endswith("_"):  # dynamic prefixes such as t("band_" + value)
            assert key in EN, key


def test_rules_no_false_positive_on_cod_and_plain_text():
    f = scan_text({"d": "Cash on delivery available. Free shipping. Returns accepted within 14 days."})
    assert not f.payment_hits and not f.urgency_hits and f.text_checked
    assert not scan_text({"d": ""}).text_checked


def test_no_secrets_in_repo_files():
    import pathlib

    for p in pathlib.Path(".").rglob("*"):
        if p.is_file() and p.suffix in {".py", ".toml", ".example", ".md", ".txt"} and ".pytest_cache" not in str(p):
            text = p.read_text(encoding="utf-8", errors="ignore")
            assert not re.search(r"AIza[0-9A-Za-z_-]{30,}", text), p
