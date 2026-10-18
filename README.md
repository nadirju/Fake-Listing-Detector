# 🛡️ AI Fake Product Listing Detector

NYLP AI Hackathon 2026 — MVP built from PRD v1.0.

Paste a product URL and get an **evidence-backed risk assessment**: a 0–100 score, a risk band, the red flags that triggered it (each with the evidence), the checks that could **not** be run, and safer next steps. It is an *assistive* tool: it never claims to prove that an item is genuine or fake.

> **Score meaning:** higher = more warning signs observed. It is **not** a probability that the item is counterfeit. Missing data is never treated as evidence of fraud (or of safety).

## How it works

```
URL ─► validate (SSRF-safe) ─► extract listing ─┬─► WHOIS domain age ──┐
        manual / demo fallback                  ├─► reverse image search ─┤
                                                └─► text rules            ├─► Gemini (schema-constrained JSON,
                                                                          │    grounded against the evidence)
                                                                          ▼
                                         deterministic scoring engine (documented weights) ─► report
```

* **Gemini never produces the score.** It classifies image-match context, finds contradictions and writes a summary. Every claim must quote text that literally exists in the evidence (or reference a supplied image-match URL) or it is dropped.
* **The score is deterministic** (`src/scoring.py`): same normalized input + evidence ⇒ same score.

### Scoring weights (initial heuristics from the PRD — not empirically validated)

| Signal | Max | Rule |
|---|---|---|
| Reverse image search | 25 | Grounded "unrelated context" match: 2+ distinct sources 25, one source 15. Stock photos on retailer/manufacturer pages are **not** suspicious. No match = unknown, 0 pts |
| Price check | 20 | **Only** with a user-supplied comparable reference price *and* source URL, same currency: ≤40% of reference 20, ≤60% 10. Otherwise "heuristic/unverified", 0 pts |
| Listing consistency | 15 | Evidence-quoted contradictions: high 15 / medium 10 / low 5 |
| Domain age | 10 | <30 days 10, <90 days 5. WHOIS unavailable/redacted = unknown, 0 pts |
| Contact & returns | 10 | Neither found on a fully captured page 10, one missing 5 |
| Pressure & payment | 10 | Western Union / crypto / gift-card / WhatsApp-only ordering 10; urgency wording 3–5 |

Domain age + contact/returns are correlated and share a **cap of 15**. Total is capped at 100.
Bands: **0–29** Lower observed risk · **30–59** Caution · **60–100** High suspicion.
**Insufficient evidence** is shown when fewer than 2 checks produced usable evidence (the score is then labelled provisional).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                    # add keys (optional - see below)
python -m pytest                                        # 140 tests, no network needed
streamlit run app.py                                    # http://localhost:8501
```

**No keys? The app still works:** open the **Demo cases** tab (pre-recorded suspicious + legitimate examples) or use **Manual entry**. Gemini / SerpAPI / Firecrawl are optional and degrade gracefully.

### Configuration

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Structured AI interpretation (model via `GEMINI_MODEL`, default `gemini-2.5-flash`) |
| `SERPAPI_API_KEY` | Reverse image search (`SERPAPI_ENGINE`, default `google_lens` — verify your plan supports it) |
| `FIRECRAWL_API_KEY` | Optional extractor. Order via `EXTRACTOR_ORDER=direct,firecrawl,jina` |
| `JINA_API_KEY` | Optional; Jina Reader also works keyless at a low rate limit |
| Timeouts / limits | `EXTRACTOR_TIMEOUT`, `WHOIS_TIMEOUT`, `IMAGE_TIMEOUT`, `GEMINI_TIMEOUT`, `MAX_RESPONSE_BYTES`, `CACHE_TTL_SECONDS`, `SESSION_HOURLY_LIMIT` … (see `.env.example`) |

Keys are read from environment variables or Streamlit secrets (`.streamlit/secrets.toml`, git-ignored) and are never rendered or logged.

> Third-party readers (Firecrawl/Jina), SerpAPI and Gemini receive the listing URL / image URLs / listing text. Remove them from the config if that is not acceptable for your deployment.

## Demo cases (AC-14)

Both run offline through the *real* grounding + scoring code with bundled provider output (clearly labelled in the UI). Fictional brands, reserved `.example` domains.

| Case | Expected result | Why |
|---|---|---|
| `suspicious_watch` | **85 — High suspicion** | domain 12 days old; photo found in unrelated contexts; $129 vs $1,450 reference; contradictory claims; no returns/contact; bank-transfer/Bitcoin/WhatsApp + pressure language |
| `legitimate_mug` | **0 — Lower observed risk** | domain from 2011; returns & contact present; matches are manufacturer/retailer pages; price ≈ reference; no contradictions |

Known provider failure modes are visible in *Technical details* of every report (blocked site, WHOIS redacted, SerpAPI quota, Gemini timeout …).

## Deploy

**Streamlit Community Cloud:** push the repo, set the main file to `app.py`, paste the keys into *Settings → Secrets* using `.streamlit/secrets.toml.example` as the template.

**Docker:**
```bash
docker build -t listing-detector .
docker run --rm -p 8501:8501 --env-file .env listing-detector
```

## Security notes

* **SSRF (AC-01):** http/https only, ports 80/443, no credentials; DNS results must all be public (loopback, private, link-local/metadata, CGNAT, multicast, NAT64/6to4 blocked); every redirect re-validated; connect-time re-resolution guard against DNS rebinding; streaming byte cap; proxies ignored for direct fetches.
* **Prompt injection:** listing text is passed only as JSON data between delimiters (`<`/`>` escaped); the model cannot set the score; ungrounded claims are dropped.
* **Rendering:** all untrusted text is markdown-escaped (no remote images/links injected).
* **Privacy (AC-13):** no credentials/payment/personal data requested; logs redact URL query tokens and secrets; page text is never stored or exported; results are ephemeral.
* CAPTCHAs / anti-bot pages are reported as "blocked", never bypassed.

## Acceptance-criteria map

| AC | Where | Tests |
|---|---|---|
| 01 URL validation / SSRF | `src/url_safety.py` | `test_url_safety.py` |
| 02 Extraction, "Not found" | `src/extractor.py`, `app.py` | `test_extractor.py` |
| 03 Manual fallback + demo | `extractor.listing_from_manual`, `demo_data.py` | `test_failure_paths.py`, `test_demo_cases.py` |
| 04 Schema + one bounded retry | `src/gemini_analyzer.py` | `test_schema.py` |
| 05 Evidence-backed flags | `ground_analysis`, `reporting.py` | `test_schema.py`, `test_demo_cases.py` |
| 06 Deterministic score | `src/scoring.py` | `test_scoring.py` |
| 07 Unknowns / insufficient evidence | `scoring.assess_status` | `test_scoring.py` |
| 08 Price heuristic only | `scoring._price_signal` | `test_scoring.py` |
| 09 Image search | `signals/image_search.py` | `test_failure_paths.py` |
| 10 Domain age | `signals/domain_age.py` | `test_failure_paths.py` |
| 11 Result UI + notice | `app.py` | AppTest smoke |
| 12 Failure isolation | `pipeline.py` | `test_failure_paths.py` |
| 13 Privacy | `logging_utils.py`, `url_safety.redact_url` | `test_misc.py` |
| 14 Demo readiness | `demo_data.py` | `test_demo_cases.py` |
| 15 Performance | per-provider timeouts, parallel signals, caching | `test_failure_paths.py` |

## Scope notes

Implemented: all P0 items plus the **P1 Urdu/English toggle** (UI, flags, next steps, report; Gemini's summary in the chosen language). **Not implemented by design:** community reporting/database (P1, default MVP is ephemeral results) and the Chrome extension (P2). The suggested Streamlit-secrets path is supported.

After the hackathon: build a labelled evaluation set before making any accuracy claims, then tune weights.

## Layout

```
app.py                      Streamlit UI
src/pipeline.py             orchestration + provider isolation
src/url_safety.py           SSRF-safe validation/fetch, redaction
src/extractor.py            direct / Firecrawl / Jina extraction + manual
src/signals/                domain_age.py, image_search.py, listing_rules.py
src/gemini_analyzer.py      schema-constrained AI + grounding
src/scoring.py              documented deterministic score
src/reporting.py            flags, actions, exports
src/schemas.py cache.py config.py i18n.py demo_data.py logging_utils.py utils.py errors.py
tests/                      140 tests
```
