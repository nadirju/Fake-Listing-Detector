# AI Fake Product Listing Detector

A lightweight Streamlit app that helps shoppers evaluate whether a product listing or seller looks suspicious before paying. Paste a product URL (or use the manual fallback / demo cases), and the app:

1. Validates the URL with SSRF protections.
2. Extracts listing fields via Jina Reader (title, price, description, seller, images, contact, return policy).
3. Collects independent signals: WHOIS domain age, optional reverse-image search (SerpAPI), and heuristic listing features.
4. Asks Gemini for a schema-constrained interpretation (flags + explanation + recommended actions).
5. Computes a **deterministic** risk score (0–100) from explicit signal weights.
6. Presents the result with evidence, unavailable checks, and a clear disclaimer.

> ⚠️ The score reflects **observed risk**, not the probability that a listing is counterfeit. This app is not a certification or legal finding.

## Requirements
- Python 3.11+
- (Optional) `GEMINI_API_KEY` for AI explanation.
- (Optional) `SERPAPI_KEY` for reverse-image search.

## Quick Start

```bash
git clone <repo> ai-fake-listing-detector
cd ai-fake-listing-detector
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Add your keys if you have them
streamlit run app.py