"""Turns scored signals into the user-facing report parts: red flags, next steps,
limitations, and exportable text (Appendix B copy)."""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from .i18n import t
from .schemas import AnalysisReport, GroundedAnalysis, RedFlag, Signal
from .utils import clean_text, truncate

_SEVERITY_TO_CONFIDENCE = {"low": "low", "medium": "medium", "high": "high"}


def build_red_flags(signals: List[Signal], ai: Optional[GroundedAnalysis], lang: str) -> List[RedFlag]:
    flags: List[RedFlag] = []
    for s in sorted((x for x in signals if x.status == "flagged"), key=lambda x: (-x.score_contribution, x.id)):
        explanation = t(f"flagexp_{s.id}", lang)
        if s.id == "listing_consistency" and s.note:
            explanation = f"{explanation} {truncate(s.note, 300)}"
        flags.append(
            RedFlag(
                title=t(f"sig_{s.id}", lang),
                explanation=explanation,
                evidence=s.evidence,
                confidence=_SEVERITY_TO_CONFIDENCE.get(s.severity, "medium"),  # type: ignore[arg-type]
                origin="signal",
                signal_id=s.id,
            )
        )
    if ai:
        used = {s.evidence for s in signals if s.id == "listing_consistency"}
        for obs in ai.observations:
            evidence = f'"{obs.evidence_quote}"'
            if any(obs.evidence_quote in e for e in used):
                continue
            flags.append(
                RedFlag(
                    title=obs.title,
                    explanation=obs.explanation,
                    evidence=evidence,
                    confidence=obs.confidence,
                    origin="model_inference",
                )
            )
    return flags


def build_unavailable(signals: List[Signal], extra: Dict[str, str], lang: str) -> Tuple[List[str], Dict[str, str]]:
    """Checks that did not run or could not be completed, with their reasons."""
    ids: List[str] = []
    details: Dict[str, str] = {}
    for s in signals:
        if s.reason_unavailable:
            ids.append(s.id)
            details[s.id] = s.reason_unavailable
    for key, reason in extra.items():
        if key not in details:
            ids.append(key)
            details[key] = reason
    return ids, details


def build_actions(
    signals: List[Signal], ai: Optional[GroundedAnalysis], band: str, status: str, lang: str
) -> List[str]:
    by_id = {s.id: s for s in signals}
    keys: List[str] = ["act_verify_official", "act_buyer_protection"]
    if band == "high_suspicion":
        keys.append("act_hold_payment")
    contact = by_id.get("contact_returns")
    if contact and contact.status in ("flagged", "unknown"):
        keys.append("act_check_policies")
    domain = by_id.get("domain_age")
    if domain and domain.status == "flagged":
        keys.append("act_search_reviews")
    urgency = by_id.get("urgency_payment")
    if urgency and urgency.status == "flagged" and urgency.severity == "high":
        keys.append("act_avoid_transfer")
    price = by_id.get("price_check")
    if price and price.status in ("flagged", "unknown"):
        keys.append("act_compare_price")
    image = by_id.get("image_search")
    if image and image.status == "flagged":
        keys.append("act_inspect_images")
    actions = [t(k, lang) for k in dict.fromkeys(keys)]
    if ai:
        for extra in ai.recommended_actions[:3]:
            text = clean_text(extra, 220)
            if text and text not in actions:
                actions.append(text)
    return actions


def build_limitations(signals: List[Signal], listing_source: str, ai_used: bool, lang: str) -> List[str]:
    by_id = {s.id: s for s in signals}
    keys = ["lim_score_meaning"]
    price = by_id.get("price_check")
    if price and price.status != "clear" and price.status != "flagged":
        keys.append("lim_price_unverified")
    image = by_id.get("image_search")
    if image and image.status != "flagged":
        keys.append("lim_no_match")
    domain = by_id.get("domain_age")
    if domain and domain.status == "flagged":
        keys.append("lim_domain_weak")
    if listing_source not in ("manual", "demo"):
        keys.append("lim_partial_extraction")
    if ai_used:
        keys.append("lim_ai_unverified")
    return [t(k, lang) for k in keys]


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #


def share_text(report: AnalysisReport) -> str:
    """Appendix B result-page copy, localized."""
    lang = report.language
    flagged = [f.title for f in report.red_flags if f.origin == "signal"]
    unavailable = [t(f"sig_{c}", lang) for c in report.checks_unavailable]
    return t(
        "share_template",
        lang,
        n=len(flagged),
        flags="; ".join(flagged) or t("share_none", lang),
        unavailable="; ".join(unavailable) or t("share_none", lang),
    )


def report_to_markdown(report: AnalysisReport) -> str:
    lang = report.language
    lines: List[str] = [
        f"# {t('app_title', lang)}",
        "",
        f"- **{t('risk_score', lang)}:** {report.risk_score}/100"
        + (" (provisional)" if report.assessment_status == "insufficient_evidence" else ""),
        f"- **Band:** {t('band_' + report.risk_band, lang)}",
        f"- **Status:** {report.assessment_status}",
        f"- **Listing:** {report.listing_url or '—'}",
        f"- **Request ID:** {report.request_id}  |  Generated: {report.generated_at:%Y-%m-%d %H:%M UTC}" if report.generated_at else f"- **Request ID:** {report.request_id}",
        "",
        t("bandtext_" + report.risk_band, lang),
        "",
    ]
    if report.demo_notice:
        lines += [f"> {report.demo_notice}", ""]
    if report.insufficient_reason:
        lines += [f"**{t('insufficient_title', lang)}:** {report.insufficient_reason}", ""]
    lines.append(f"## {t('flags_title', lang)}")
    if report.red_flags:
        for f in report.red_flags:
            tag = f" _({t('inference_tag', lang)})_" if f.origin == "model_inference" else ""
            lines += [f"- **{f.title}**{tag}: {f.explanation}", f"  - {t('evidence_label', lang)}: {f.evidence}"]
    else:
        lines.append(t("no_flags", lang))
    lines += ["", f"## {t('checks_title', lang)}", "", "| Check | Result | Points | Evidence / note |", "|---|---|---|---|"]
    for s in report.signals:
        text = (s.evidence or s.reason_unavailable or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {t('sig_' + s.id, lang)} | {t('status_' + s.status, lang)} | {s.score_contribution}/{s.max_weight} | {text} |")
    lines += ["", f"## {t('unavailable_title', lang)}"]
    if report.checks_unavailable:
        for c in report.checks_unavailable:
            lines.append(f"- {t('sig_' + c, lang)}: {report.unavailable_details.get(c, '')}")
    else:
        lines.append(t("unavailable_none", lang))
    lines += ["", f"## {t('limits_title', lang)}"] + [f"- {x}" for x in report.limitations]
    lines += ["", f"## {t('steps_title', lang)}"] + [f"- {x}" for x in report.recommended_actions]
    lines += ["", "---", f"_{report.disclaimer}_", "", share_text(report)]
    return "\n".join(lines)


def report_to_json(report: AnalysisReport) -> str:
    """JSON export. Page text is never persisted or exported."""
    data = report.model_dump(mode="json")
    if data.get("listing"):
        data["listing"]["page_text"] = ""
    return json.dumps(data, indent=2, ensure_ascii=False)
