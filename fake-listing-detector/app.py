import os
import json
from pathlib import Path
from dotenv import load_dotenv
import streamlit as st
from src.analyzer import analyze_listing
from src.demo_cases import DEMO_CASES
from src.extractor import manual_listing
from src.url_safety import UnsafeURLError, validate_url

load_dotenv()
st.set_page_config(page_title="AI Fake Product Listing Detector", page_icon="🛡️", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
.block-container {max-width: 1180px; padding-top: 2rem; padding-bottom: 3rem;}
.hero {padding: 1.6rem 1.8rem; border-radius: 18px; background: linear-gradient(120deg,#151d3b,#393b85); color: white; margin-bottom: 1rem;}
.hero h1 {color:white; font-size:2.2rem; margin-bottom:.35rem;}
.hero p {color:#e3e7ff; margin-bottom:0; font-size:1.02rem;}
.metric-card {background:white; border:1px solid #e7eaf2; border-radius:14px; padding:1rem 1.2rem; min-height:115px;}
.flag-card {border:1px solid #e5e7eb; border-radius:12px; padding:1rem; margin:.5rem 0; background:white;}
.small-muted {color:#667085; font-size:.88rem;}
</style>
<div class="hero"><h1>🛡️ AI Fake Product Listing Detector</h1><p>Understand listing warning signs before you pay — with evidence, uncertainty, and safer next steps.</p></div>
""", unsafe_allow_html=True)

with st.sidebar:
    st.header("Try a demo")
    demo_choice = st.selectbox("Preloaded listing", ["Choose a demo…", *DEMO_CASES.keys()])
    if st.button("Load selected demo", use_container_width=True) and demo_choice in DEMO_CASES:
        demo = DEMO_CASES[demo_choice]
        st.session_state["url"] = demo.listing_url
        st.session_state["manual_demo"] = demo.model_dump()
        st.session_state["manual_mode"] = True
        st.rerun()
    st.divider()
    st.caption("Optional providers")
    st.write("Gemini:", "configured" if os.getenv("GEMINI_API_KEY") else "not configured — deterministic fallback")
    st.write("SerpAPI:", "configured" if os.getenv("SERPAPI_API_KEY") else "not configured")
    st.caption("No accounts, payment details, or personal information are requested. Reports are held in memory for this session only.")

st.subheader("1. Enter a product listing")
st.write("Paste a public product URL. If the site blocks extraction, switch to manual details below.")
with st.form("analyze_form"):
    url = st.text_input("Product listing URL", value=st.session_state.get("url", ""), placeholder="https://store.example/product")
    mode = st.radio("Input method", ["Try URL extraction", "Enter details manually"], horizontal=True, index=1 if st.session_state.get("manual_mode") else 0)
    with st.expander("Manual listing details", expanded=(mode == "Enter details manually")):
        demo_data = st.session_state.get("manual_demo", {})
        title = st.text_input("Product title", value=demo_data.get("title", ""))
        col_a, col_b = st.columns(2)
        with col_a: price = st.text_input("Price (number only)", value=str(demo_data.get("price") or ""))
        with col_b: currency = st.text_input("Currency (optional)", value=demo_data.get("currency", ""), placeholder="USD, PKR, EUR…")
        seller = st.text_input("Seller/store name", value=demo_data.get("seller_name", ""))
        description = st.text_area("Description / visible listing text", value=demo_data.get("description", ""), height=120)
        image_urls = st.text_area("Product image URL(s), one per line (optional)", value="\n".join(demo_data.get("image_urls", [])), height=70)
    submitted = st.form_submit_button("Analyze listing", type="primary", use_container_width=True)

if submitted:
    try:
        if not url.strip():
            raise ValueError("Enter a product URL. For a demo, select a preloaded listing in the sidebar.")
        validate_url(url, resolve_dns=(mode == "Try URL extraction"))
        manual = None
        if mode == "Enter details manually":
            manual = manual_listing(url, title, price, currency, description, seller, image_urls)
            if not any((manual.title, manual.description, manual.seller_name)):
                raise ValueError("Enter at least a product title, description, or seller name for a useful assessment.")
        with st.spinner("Checking available listing evidence… provider calls have bounded timeouts."):
            package = analyze_listing(url, manual=manual, use_cache=True)
        st.session_state["analysis_package"] = package
    except (ValueError, UnsafeURLError) as exc:
        st.error(str(exc))
    except Exception as exc:
        st.error(f"The analysis could not be completed ({type(exc).__name__}). Try manual input or a demo listing.")

package = st.session_state.get("analysis_package")
if package:
    listing, result = package["listing"], package["result"]
    st.divider()
    st.subheader("2. Assessment")
    st.caption("Listing: " + (listing.title or "Title not found"))
    if result.assessment_status == "insufficient_evidence":
        st.warning("Insufficient evidence — the provisional score below is based on limited observed signals.")
    elif result.risk_band == "high_suspicion":
        st.error("High suspicion — multiple or strong risk signals were detected. Consider avoiding payment until independently verified.")
    elif result.risk_band == "caution":
        st.warning("Caution — some risk signals need checking before purchase.")
    else:
        st.success("Lower observed risk — few configured risk signals were detected. This is not a guarantee of authenticity.")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Observed risk score", f"{result.risk_score}/100")
    m2.metric("Risk band", {"lower_observed_risk":"Lower observed risk", "caution":"Caution", "high_suspicion":"High suspicion"}[result.risk_band])
    m3.metric("Evidence completeness", f"{result.evidence_completeness}%")
    m4.metric("Assessment status", result.assessment_status.replace("_", " ").title())
    st.progress(result.risk_score / 100, text="Observed warning signals — not a probability that the product is fake")

    with st.expander("Extracted listing details", expanded=True):
        d1, d2 = st.columns(2)
        with d1:
            st.write("**Title:**", listing.title or "Not found")
            st.write("**Price:**", f"{listing.currency} {listing.price:g}" if listing.price is not None else "Not found")
            st.write("**Seller/store:**", listing.seller_name or "Not found")
        with d2:
            st.write("**Domain:**", listing.domain or "Not found")
            st.write("**Description:**", listing.description or "Not found")
            st.write("**Images found:**", len(listing.image_urls))
        if listing.extraction_notes:
            st.caption("Extraction notes: " + " ".join(listing.extraction_notes))
        if listing.image_urls:
            for img in listing.image_urls[:3]: st.markdown(f"[Open product image]({img})")

    st.subheader("Evidence-backed red flags")
    if result.red_flags:
        for flag in result.red_flags:
            with st.container(border=True):
                st.markdown(f"**{flag.title}** · confidence: `{flag.confidence}`")
                st.write(flag.explanation)
                st.caption("Evidence: " + flag.evidence)
                if flag.source_url: st.markdown(f"[Review source]({flag.source_url})")
    else:
        st.info("No configured red flags were detected in the available evidence. This does not establish that the listing is safe.")

    st.subheader("Signal-by-signal results")
    rows = [{"Signal": s.title, "Status": s.status.title(), "Severity": s.severity.title(), "Score contribution": s.score_contribution, "Evidence": s.evidence} for s in result.signals]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.subheader("Checks unavailable or inconclusive")
    if result.checks_unavailable:
        for item in result.checks_unavailable: st.write("• " + item.replace("_", " "))
    else:
        st.write("No provider failures were recorded. Some signals can still be inconclusive.")
    st.caption("Domain lookup: " + package["domain_result"].get("message", "Unavailable"))
    st.caption("Image search: " + package["image_result"].get("message", "Unavailable"))
    st.subheader("AI explanation")
    st.write(result.ai_summary)
    st.caption("Gemini status: " + result.ai_provider_status + ". AI text does not determine the score.")

    st.subheader("Safer next steps")
    for action in result.recommended_actions: st.write("☐ " + action)
    st.info(result.disclaimer + " Missing data is not treated as evidence of fraud. A low score is not a certification.")
    report = {"listing": listing.model_dump(exclude={"raw_text"}), "assessment": result.model_dump(), "domain_lookup": package["domain_result"], "image_search": package["image_result"]}
    st.download_button("Download report as JSON", data=json.dumps(report, indent=2, ensure_ascii=False), file_name="listing-risk-report.json", mime="application/json")
    st.code(f"Risk score: {result.risk_score}/100\nRisk band: {result.risk_band}\nStatus: {result.assessment_status}\n\n{result.ai_summary}", language="text")

st.divider()
st.caption("Assistive assessment only. The score reflects configured warning signs, not the probability that a product is counterfeit. Confirm claims independently before paying.")
