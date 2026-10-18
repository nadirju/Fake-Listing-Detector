"""Deterministic text heuristics: pressure/urgency language and risky payment requests.

These rules are intentionally conservative and weak on their own. Sale banners are
common on legitimate shops, so urgency alone contributes very little; requests to
pay through channels without buyer protection contribute more.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Pattern, Tuple

from ..utils import clean_text


@dataclass(frozen=True)
class RuleHit:
    kind: str  # "urgency" | "payment"
    rule_id: str
    field: str
    quote: str


@dataclass
class RuleFindings:
    urgency_hits: List[RuleHit] = field(default_factory=list)
    payment_hits: List[RuleHit] = field(default_factory=list)
    text_checked: bool = False

    @property
    def distinct_urgency_rules(self) -> int:
        return len({h.rule_id for h in self.urgency_hits})


_I = re.IGNORECASE

URGENCY_RULES: Tuple[Tuple[str, Pattern[str]], ...] = (
    ("only_n_left", re.compile(r"\bonly\s+\d{1,2}\s+(?:left|remaining|in stock|pieces? left)\b", _I)),
    (
        "pressure_phrase",
        re.compile(
            r"\b(?:hurry(?: up)?|act now|last chance|today only|ends? (?:tonight|today|soon)|"
            r"limited[- ]time (?:offer|deal)|while (?:stocks?|supplies) last|selling out fast|"
            r"flash sale|clearance ends)\b",
            _I,
        ),
    ),
    (
        "countdown",
        re.compile(r"\b(?:offer|sale|deal|discount)\s+(?:expires|ends)\s+(?:in|at|on)\b", _I),
    ),
)

PAYMENT_RULES: Tuple[Tuple[str, Pattern[str]], ...] = (
    (
        "untraceable_transfer",
        re.compile(
            r"\b(?:western union|moneygram|wire transfer|zelle only|cash ?app only|venmo only|"
            r"bank transfer only)\b",
            _I,
        ),
    ),
    (
        "crypto_giftcard",
        re.compile(
            r"\b(?:pay(?:ment)?s?\s+(?:only\s+)?(?:in|with|via|by)\s+(?:bitcoin|btc|crypto(?:currency)?|usdt|gift ?cards?)|"
            r"(?:bitcoin|btc|usdt|crypto|gift ?card)s?\s+only|bitcoin payment|accept(?:s|ing)? (?:bitcoin|crypto))\b",
            _I,
        ),
    ),
    (
        "off_platform_order",
        re.compile(
            r"\b(?:order|buy|purchase|contact|message)\s+(?:us\s+)?(?:only\s+)?(?:via|on|through|by)\s+(?:whatsapp|telegram)\b",
            _I,
        ),
    ),
    (
        "outside_platform",
        re.compile(r"\bpay(?:ment)?\s+outside\s+(?:of\s+)?(?:the\s+)?(?:platform|site|marketplace)\b", _I),
    ),
)


def _quote(text: str, match: re.Match, radius: int = 40) -> str:
    start = max(match.start() - radius, 0)
    end = min(match.end() + radius, len(text))
    snippet = clean_text(text[start:end])
    return snippet[:140]


def scan_text(fields: Dict[str, str]) -> RuleFindings:
    """Scan named text fields; returns hits with the quoted evidence."""
    findings = RuleFindings()
    for name, raw in fields.items():
        text = clean_text(raw)
        if not text:
            continue
        findings.text_checked = True
        for rule_id, pattern in URGENCY_RULES:
            m = pattern.search(text)
            if m:
                findings.urgency_hits.append(RuleHit("urgency", rule_id, name, _quote(text, m)))
        for rule_id, pattern in PAYMENT_RULES:
            m = pattern.search(text)
            if m:
                findings.payment_hits.append(RuleHit("payment", rule_id, name, _quote(text, m)))
    return findings
