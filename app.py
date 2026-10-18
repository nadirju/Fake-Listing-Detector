"""Streamlit UI for the AI Fake Product Listing Detector.

The UI only gathers input and renders an `AnalysisReport`; all analysis lives in `src/`.
Untrusted text (scraped content, model output) is escaped before rendering.
"""
from __future__ import annotations

import time
from typing import List, Optional

import streamlit as st

from src.config import load_settings
from src.demo_data import DEMO_CASES
from src.errors import ExtractionFailed, InvalidInputError
from src.i18n import LANGUAGES, t
from src.logging_utils import get_logger
from src.pipeline import AnalysisRequest, analyze
from src.reporting import report_to_json, report_to_markdown, share_text
from src.schemas import AnalysisReport, ManualInput, ReferencePrice
from src.utils import escape_md

st.set_page_config(page_title="AI Fake Product Listing Detector", page_icon="🛡️", layout="centered")

BAND_COLOR = {"lower_observed_risk": "green", "caution": "orange", "high_suspicion": "red"}
BAND_ICON = {"lower_observed_risk": "🟢", "caution": "🟠", "high_suspicion": "🔴"}
STATUS_ICON = {"flagged": "🚩", "clear": "✅", "unknown": "❔"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _rate_limit_ok(limit: int, lang: str) -> bool:
    now = time.time()
    stamps: List[float] = [x for x in st.session_state.get("analysis_times", []) if now - x < 3600]
    if stamps and now - stamps[-1] < 2:
        st.warning(t("err_cooldown", lang))
        return False
    if len(stamps) >= limit:
        st.error(t("err_rate", lang))
        return False
    stamps.append(now)
    st.session_state["analysis_times"] = stamps
    return True


def _reference_from_inputs(amount: float, currency: str, source: str, lang: str) -> Optional[ReferencePrice]:
    if not amount or amount <= 0 or not currency.strip():
        return None
    try:
        return ReferencePrice(amount=amount, currency=currency.strip(), source_url=source.strip() or None)
    except Exception:
        return None


def run_analysis(request: AnalysisRequest, lang: str, settings) -> None:
    if not _rate_limit_ok(settings.session_hourly_limit, lang):
        return
    bar = st.progress(0.0, text="Starting…")

    def on_progress(fraction: float, message: str) -> None:
        bar.progress(min(max(fraction, 0.0), 1.0), text=message)

    try:
        report = analyze(request, settings, progress=on_progress)
        st.session_state["report"] = report
    except InvalidInputError as exc:
        st.session_state.pop("report", None)
        st.error(f"{t('err_invalid_url', lang)} {exc}")
    except ExtractionFailed as exc:
        st.session_state.pop("report", None)
        st.warning(t("err_extraction", lang))
        st.info(t("err_extraction_hint", lang))
        with st.expander(t("tech_title", lang)):
            for s in exc.statuses:
                st.write(f"`{s.provider}` — {s.status}: {escape_md(s.detail)}")
    except Exception as exc:  # last-resort guard: never show a stack trace to users
        get_logger().error("unexpected_failure type=%s", type(exc).__name__)
        st.session_state.pop("report", None)
        st.error(t("err_unexpected", lang))
    finally:
        bar.empty()


# --------------------------------------------------------------------------- #
# Result rendering
# --------------------------------------------------------------------------- #


def render_report(report: AnalysisReport) -> None:
    lang = report.language
    insufficient = report.assessment_status == "insufficient_evidence"
    st.divider()
    if report.demo_notice:
        st.info(report.demo_notice)

    c1, c2 = st.columns([1, 2])
    with c1:
        st.metric(t("provisional_score" if insufficient else "risk_score", lang), f"{report.risk_score}/100")
    with c2:
        color = BAND_COLOR[report.risk_band]
        st.markdown(f"### {BAND_ICON[report.risk_band]} :{color}[{t('band_' + report.risk_band, lang)}]")
        st.write(t("bandtext_" + report.risk_band, lang))
    st.progress(report.risk_score / 100)
    st.caption(t("score_hint", lang))
    if insufficient:
        st.warning(f"**{t('insufficient_title', lang)}.** {t('insufficient_body', lang)} {escape_md(report.insufficient_reason)}")
    st.info(t("assistive_notice", lang))

    st.subheader(t("flags_title", lang))
    if not report.red_flags:
        st.write(t("no_flags", lang))
    for flag in report.red_flags:
        with st.expander(f"🚩 {escape_md(flag.title)}", expanded=flag.origin == "signal"):
            if flag.origin == "model_inference":
                st.caption(t("inference_tag", lang))
            st.markdown(escape_md(flag.explanation))
            st.markdown(f"**{t('evidence_label', lang)}:**")
            st.text(flag.evidence)
            st.caption(f"{t('confidence_label', lang)}: {flag.confidence}")

    st.subheader(t("unavailable_title", lang))
    if report.checks_unavailable:
        for check in report.checks_unavailable:
            st.markdown(f"- **{t('sig_' + check, lang)}** — {escape_md(report.unavailable_details.get(check, ''))}")
    else:
        st.write(t("unavailable_none", lang))

    if report.ai_summary:
        st.subheader(t("ai_summary_title", lang))
        st.markdown(escape_md(report.ai_summary))
        st.caption(t("ai_unverified", lang))

    st.subheader(t("steps_title", lang))
    for action in report.recommended_actions:
        st.markdown(f"- {escape_md(action)}")

    with st.expander(t("checks_title", lang)):
        rows = [
            {
                t("col_check", lang): t("sig_" + s.id, lang),
                t("col_status", lang): f"{STATUS_ICON[s.status]} {t('status_' + s.status, lang)}",
                t("col_points", lang): f"{s.score_contribution}/{s.max_weight}",
                t("col_evidence", lang): " ".join(x for x in (s.evidence, s.note or "") if x),
            }
            for s in report.signals
        ]
        st.dataframe(rows, hide_index=True)

    with st.expander(t("matches_title", lang)):
        if not report.image_matches:
            st.write(t("matches_none", lang))
        for m in report.image_matches:
            st.markdown(f"**{escape_md(m.title) or escape_md(m.source)}**")
            st.text(m.link)
            if m.snippet:
                st.caption(m.snippet)

    with st.expander(t("listing_title", lang)):
        listing = report.listing
        if listing:
            nf = t("not_found", lang)

            def tri(v: Optional[bool]) -> str:
                return t("yes", lang) if v is True else (t("no", lang) if v is False else t("unknown_word", lang))

            fields = [
                (t("field_title", lang), listing.title or nf),
                (t("field_price", lang), listing.price_text or nf),
                (t("field_seller", lang), listing.seller or nf),
                (t("field_brand", lang), listing.brand or nf),
                (t("field_domain", lang), listing.domain or nf),
                (t("field_description", lang), (listing.description or nf)[:500]),
                (t("field_images", lang), ", ".join(listing.image_urls[:3]) or nf),
                (t("field_returns", lang), tri(listing.has_return_policy)),
                (t("field_contact", lang), tri(listing.has_contact_info)),
            ]
            for label, value in fields:
                st.markdown(f"**{label}:**")
                st.text(value)

    st.subheader(t("limits_title", lang))
    for item in report.limitations:
        st.markdown(f"- {escape_md(item)}")

    st.subheader(t("share_title", lang))
    st.code(share_text(report), language=None)
    d1, d2 = st.columns(2)
    d1.download_button(t("download_md", lang), report_to_markdown(report), file_name="listing-risk-report.md", mime="text/markdown")
    d2.download_button(t("download_json", lang), report_to_json(report), file_name="listing-risk-report.json", mime="application/json")

    with st.expander(t("tech_title", lang)):
        st.write(f"Request ID: `{report.request_id}` · {report.elapsed_ms} ms · mode: {report.mode}")
        st.dataframe(
            [{"provider": s.provider, "status": s.status, "detail": s.detail, "ms": s.elapsed_ms} for s in report.provider_statuses],
            hide_index=True,
        )
    st.caption(report.disclaimer)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> None:
    settings = load_settings()

    with st.sidebar:
        choice = st.radio("Language / زبان", list(LANGUAGES), format_func=lambda k: LANGUAGES[k], horizontal=True)
        lang = choice
        st.markdown(f"**{t('sidebar_about', lang)}**")
        st.caption(t("sidebar_about_body", lang))
        st.markdown(f"**{t('sidebar_status', lang)}**")
        status = settings.public_status()
        for name, label in (("gemini", "Gemini"), ("serpapi", "SerpAPI"), ("firecrawl", "Firecrawl")):
            st.caption(f"{'✅' if status[name] else '⚪'} {label}: {t('configured' if status[name] else 'not_configured', lang)}")

    if lang == "ur":
        st.markdown(
            "<style>.stMarkdown, .stAlert, .stCaption, h1, h2, h3 {direction: rtl; text-align: right;}</style>",
            unsafe_allow_html=True,
        )

    st.title(f"🛡️ {t('app_title', lang)}")
    st.write(t("subtitle", lang))

    with st.expander(t("ref_expander", lang)):
        st.caption(t("ref_help", lang))
        r1, r2 = st.columns(2)
        ref_amount = r1.number_input(t("ref_amount", lang), min_value=0.0, value=0.0, step=1.0)
        ref_currency = r2.text_input(t("ref_currency", lang), value="USD", max_chars=8)
        ref_source = st.text_input(t("ref_source", lang), placeholder="https://")
    reference = _reference_from_inputs(ref_amount, ref_currency, ref_source, lang)

    tab_url, tab_manual, tab_demo = st.tabs([t("tab_url", lang), t("tab_manual", lang), t("tab_demo", lang)])

    with tab_url:
        url = st.text_input(t("url_label", lang), placeholder=t("url_placeholder", lang), key="url_input")
        st.caption(t("privacy_note", lang))
        if st.button(t("analyze_btn", lang), type="primary", key="btn_url"):
            run_analysis(AnalysisRequest(mode="url", url=url, language=lang, reference=reference), lang, settings)

    with tab_manual:
        st.caption(t("manual_intro", lang))
        m_url = st.text_input(t("manual_url", lang), key="m_url")
        m_title = st.text_input(t("manual_title", lang), key="m_title", max_chars=300)
        m_price = st.text_input(t("manual_price", lang), key="m_price", max_chars=80)
        m_desc = st.text_area(t("manual_desc", lang), key="m_desc", max_chars=3000, height=140)
        m_seller = st.text_input(t("manual_seller", lang), key="m_seller", max_chars=200)
        m_images = st.text_area(t("manual_images", lang), key="m_images", height=80)
        if st.button(t("manual_btn", lang), type="primary", key="btn_manual"):
            manual = ManualInput(
                url=m_url or None,
                title=m_title,
                price_text=m_price,
                description=m_desc,
                seller=m_seller,
                image_urls=[x.strip() for x in m_images.splitlines() if x.strip()],
            )
            run_analysis(AnalysisRequest(mode="manual", manual=manual, language=lang, reference=reference), lang, settings)

    with tab_demo:
        st.caption(t("demo_intro", lang))
        demo_id = st.selectbox(t("demo_pick", lang), list(DEMO_CASES), format_func=lambda k: DEMO_CASES[k].title)
        st.caption(DEMO_CASES[demo_id].summary)
        if st.button(t("demo_btn", lang), type="primary", key="btn_demo"):
            run_analysis(AnalysisRequest(mode="demo", demo_id=demo_id, language=lang), lang, settings)

    report = st.session_state.get("report")
    if report is not None:
        render_report(report)


main()
