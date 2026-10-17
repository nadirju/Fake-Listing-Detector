# AI Fake Product Listing Detector

A Python/Streamlit MVP for the NYLP AI Hackathon 2026. It extracts public product-page metadata when possible, evaluates explicit warning signals, optionally consults WHOIS, SerpAPI reverse-image search, and Gemini, then calculates a deterministic 0–100 observed-risk score.

## Features

- URL validation for HTTP/HTTPS only; blocks private, loopback, link-local, reserved, and metadata targets; validates redirect destinations; limits redirects, response bytes, and request timeouts.
- HTML metadata and JSON-LD product extraction with an explicit manual-entry fallback.
- Independent, optional WHOIS domain-age and SerpAPI reverse-image checks. Provider failure is reported and does not crash analysis.
- Optional Gemini JSON-mode explanation with bounded retry. AI does not choose the score and is not allowed to add unsupported flags to the deterministic score.
- Transparent rule-based scoring, per-signal evidence, unknown/unavailable states, risk bands, report JSON download, and two preloaded demo examples.
- Process-local TTL cache; no report database or persistent page-content storage.
- Automated tests for URL safety, scoring, schema validation, and provider failure handling.

## Requirements

- Python 3.11 or 3.12 recommended.
- Optional API keys: Gemini (`GEMINI_API_KEY`) and SerpAPI (`SERPAPI_API_KEY`). The application works without either key.
- Network access is only needed for live extraction/provider checks. Set `DISABLE_NETWORK=true` to disable live page fetching; demo/manual mode remains usable.

## Local quick start

### Windows PowerShell

```powershell
cd fake-listing-detector
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
streamlit run app.py
```

If PowerShell blocks activation, use `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that terminal, then activate again. Alternatively run `.venv\Scripts\python.exe -m streamlit run app.py` without activation.

### macOS / Linux

```bash
cd fake-listing-detector
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

Streamlit prints a local URL, usually http://localhost:8501.

## Configure optional providers

Edit `.env` and set keys without committing the file:

```dotenv
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash
SERPAPI_API_KEY=your_serpapi_api_key
REQUEST_TIMEOUT_SECONDS=8
CACHE_TTL_SECONDS=300
DISABLE_NETWORK=false
```

Gemini is used only to summarize supplied evidence. Its response is parsed as JSON and retried once if invalid. When missing or failing, deterministic analysis remains available. SerpAPI reverse-image support depends on account access and quotas. No image match is not proof that an image is original. WHOIS services can rate-limit or redact creation dates.

For Streamlit Community Cloud, add keys to the app's Secrets settings. Do not commit `.env` or `.streamlit/secrets.toml`.

## Run tests

```bash
pytest -q
```

## Docker

```bash
docker build -t fake-listing-detector .
docker run --rm -p 8501:8501 --env-file .env fake-listing-detector
```

Open http://localhost:8501.

## Demo walkthrough

1. Start the app and choose **Suspicious example — pressure and unsafe payment** in the sidebar.
2. Click **Load selected demo**, then **Analyze listing** with **Enter details manually** selected. It uses synthetic `.example` data and does not need live site access.
3. Review the pressure/payment evidence and missing contact/return indicators.
4. Repeat with **Lower-signal example — ordinary product listing**. It should produce a lower score based on the same deterministic rules.
5. Download the JSON report and show unavailable provider states.

The sample cases are illustrative synthetic fixtures, not claims about real merchants. In the manual demo, the domain-age and image-search providers may be unavailable and are not treated as fraud evidence.

## Scoring model

Initial transparent weights are heuristic, not empirically calibrated:

| Signal | Initial contribution |
|---|---:|
| Unsafe payment or urgency language | 10 |
| Contact details not detected in extracted text | 5 |
| Return/refund policy not detected in extracted text | 5 |
| Domain created less than 180 days ago (only when WHOIS gives a usable date) | 10 |
| Explicit zero price metadata | 5 |
| Very short product title | 5 |

Contributions are additive and capped at 100. Unknown signals contribute zero. Price plausibility is not scored without a verified comparable reference. Reverse-image results are displayed for review, not automatically scored, because context matters and stock images can be legitimate. Bands: 0–29 lower observed risk; 30–59 caution; 60–100 high suspicion. Insufficient evidence can override the status label without changing the meaning of the provisional score.

## Architecture

- `app.py`: Streamlit user interface and workflow.
- `src/url_safety.py`: URL/IP validation, redirect checking, bounded fetching.
- `src/extractor.py`: HTML metadata and JSON-LD parsing; manual listing model.
- `src/signals/`: independent WHOIS, image search, and listing heuristics.
- `src/gemini_analyzer.py`: optional structured AI explanation.
- `src/scoring.py`: deterministic scoring and result assembly.
- `src/schemas.py`: Pydantic data contracts.
- `src/cache.py`: process-local TTL cache.
- `tests/`: URL safety, scoring, schema, and failure-path tests.

## Security and limitations

- Do not deploy an arbitrary-URL fetcher without network egress controls. DNS validation reduces SSRF risk, but DNS rebinding and unusual infrastructure can require stronger isolation; for production, run extraction in a restricted egress worker/container and enforce network-level blocks for private/metadata ranges.
- Redirects are followed manually and revalidated, and response bodies are bounded. Sites may block extraction, render product data with JavaScript, or change markup; use manual entry when necessary.
- Domain age and reverse-image results are clues, not proof. Missing provider data is not fraud evidence. The score is not a counterfeit probability or seller certification.
- Listing text is untrusted data. Never execute page scripts or follow instructions embedded in scraped content.
- No accounts, payment data, user profile data, or community reporting database are implemented; these were optional/out of core MVP scope.
- Use provider APIs according to their terms and local law. Add production monitoring, a representative labeled evaluation dataset, and security review before making accuracy claims or opening the app to untrusted high-volume traffic.
