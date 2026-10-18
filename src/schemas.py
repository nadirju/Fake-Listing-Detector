"""Pydantic models: normalized result schema (PRD section 8), provider results and
the schema-constrained Gemini output.

Free-text fields coming from scraped pages or from the model are clipped to a
maximum length so that they can never blow up the UI or the prompt.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

RiskBand = Literal["lower_observed_risk", "caution", "high_suspicion"]
AssessmentStatus = Literal["complete", "partial", "insufficient_evidence"]
SignalStatus = Literal["flagged", "clear", "unknown"]
Severity = Literal["low", "medium", "high"]
Confidence = Literal["low", "medium", "high"]


def _clip(value: object, limit: int) -> object:
    if value is None:
        return None
    text = str(value).replace("\x00", "").strip()
    return text[:limit]


# --------------------------------------------------------------------------- #
# Provider bookkeeping
# --------------------------------------------------------------------------- #


class ProviderStatus(BaseModel):
    provider: str
    status: Literal["ok", "unavailable", "error", "timeout", "skipped"]
    detail: str = ""
    elapsed_ms: int = 0


# --------------------------------------------------------------------------- #
# Listing
# --------------------------------------------------------------------------- #


class Listing(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: Optional[str] = None
    final_url: Optional[str] = None
    domain: Optional[str] = None
    title: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    price_text: Optional[str] = None
    price_source: Optional[Literal["structured", "text_pattern", "manual"]] = None
    description: Optional[str] = None
    seller: Optional[str] = None
    brand: Optional[str] = None
    image_urls: List[str] = Field(default_factory=list)
    has_return_policy: Optional[bool] = None
    has_contact_info: Optional[bool] = None
    page_text: str = ""
    page_text_length: int = 0
    source: str = "unknown"
    sources: List[str] = Field(default_factory=list)

    @field_validator("title", "seller", "brand", mode="before")
    @classmethod
    def _short(cls, v):
        return _clip(v, 300) or None

    @field_validator("description", mode="before")
    @classmethod
    def _desc(cls, v):
        return _clip(v, 3000) or None

    @field_validator("price_text", "currency", mode="before")
    @classmethod
    def _tiny(cls, v):
        return _clip(v, 80) or None

    @field_validator("image_urls", mode="before")
    @classmethod
    def _imgs(cls, v):
        return [str(u)[:2048] for u in (v or [])][:12]

    @field_validator("page_text", mode="before")
    @classmethod
    def _page(cls, v):
        return (str(v) if v else "")[:20000]


class ManualInput(BaseModel):
    url: Optional[str] = None
    title: str = ""
    price_text: str = ""
    description: str = ""
    seller: str = ""
    image_urls: List[str] = Field(default_factory=list)


class ReferencePrice(BaseModel):
    """A comparable price the *user* supplies, with the source it came from."""

    amount: float = Field(gt=0)
    currency: str = Field(min_length=1, max_length=8)
    source_url: Optional[str] = None

    @field_validator("currency", mode="before")
    @classmethod
    def _cur(cls, v):
        return str(v).strip().upper()


# --------------------------------------------------------------------------- #
# Signal collectors
# --------------------------------------------------------------------------- #


class DomainAgeResult(BaseModel):
    domain: str
    status: Literal["ok", "unavailable"]
    creation_date: Optional[datetime] = None
    registrar: Optional[str] = None
    reason: str = ""


class ImageMatch(BaseModel):
    image_url: str
    title: str = ""
    source: str = ""
    link: str
    domain: str = ""
    snippet: str = ""
    price_text: str = ""

    @field_validator("title", "source", "snippet", "price_text", mode="before")
    @classmethod
    def _t(cls, v):
        return _clip(v or "", 300) or ""


class ImageSearchResult(BaseModel):
    status: Literal["ok", "unavailable", "error"]
    reason: str = ""
    searched_images: List[str] = Field(default_factory=list)
    matches: List[ImageMatch] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Gemini output contract (AC-04)
# --------------------------------------------------------------------------- #

EvidenceField = Literal["title", "description", "seller", "brand", "price", "page_text"]
ImageContext = Literal["manufacturer_or_official", "same_product_listing", "unrelated_context", "unclear"]
PriceLooks = Literal["looks_typical", "looks_unusually_low", "looks_unusually_high", "cannot_tell"]


class Inconsistency(BaseModel):
    description: str
    evidence_field: EvidenceField
    evidence_quote: str
    confidence: Confidence

    @field_validator("description", mode="before")
    @classmethod
    def _d(cls, v):
        return _clip(v, 400)

    @field_validator("evidence_quote", mode="before")
    @classmethod
    def _q(cls, v):
        return _clip(v, 300)


class ImageMatchAssessment(BaseModel):
    source_url: str
    context: ImageContext
    reason: str

    @field_validator("source_url", mode="before")
    @classmethod
    def _u(cls, v):
        return _clip(v, 2048)

    @field_validator("reason", mode="before")
    @classmethod
    def _r(cls, v):
        return _clip(v, 300)


class PriceObservation(BaseModel):
    plausibility: PriceLooks = "cannot_tell"
    note: str = ""

    @field_validator("note", mode="before")
    @classmethod
    def _n(cls, v):
        return _clip(v or "", 300)


class Observation(BaseModel):
    title: str
    explanation: str
    evidence_quote: str
    confidence: Confidence

    @field_validator("title", mode="before")
    @classmethod
    def _t(cls, v):
        return _clip(v, 120)

    @field_validator("explanation", mode="before")
    @classmethod
    def _e(cls, v):
        return _clip(v, 400)

    @field_validator("evidence_quote", mode="before")
    @classmethod
    def _q(cls, v):
        return _clip(v, 300)


class GeminiAnalysis(BaseModel):
    """What Gemini is allowed to return. It never returns a numeric risk score."""

    summary: str = ""
    inconsistencies: List[Inconsistency] = Field(default_factory=list)
    image_match_assessments: List[ImageMatchAssessment] = Field(default_factory=list)
    price_observation: PriceObservation = Field(default_factory=PriceObservation)
    additional_observations: List[Observation] = Field(default_factory=list)
    recommended_actions: List[str] = Field(default_factory=list)

    @field_validator("summary", mode="before")
    @classmethod
    def _s(cls, v):
        return _clip(v or "", 700)

    @field_validator("inconsistencies", "image_match_assessments", mode="before")
    @classmethod
    def _cap_lists(cls, v):
        return v[:8] if isinstance(v, list) else v

    @field_validator("additional_observations", mode="before")
    @classmethod
    def _cap_obs(cls, v):
        return v[:5] if isinstance(v, list) else v

    @field_validator("recommended_actions", mode="before")
    @classmethod
    def _actions(cls, v):
        if isinstance(v, list):
            return [str(_clip(a, 220)) for a in v[:5] if str(a).strip()]
        return v


class GroundedAnalysis(BaseModel):
    """GeminiAnalysis after every claim was checked against the provided evidence."""

    summary: str = ""
    inconsistencies: List[Inconsistency] = Field(default_factory=list)
    image_match_assessments: List[ImageMatchAssessment] = Field(default_factory=list)
    price_observation: PriceObservation = Field(default_factory=PriceObservation)
    observations: List[Observation] = Field(default_factory=list)
    recommended_actions: List[str] = Field(default_factory=list)
    dropped_claims: int = 0


# --------------------------------------------------------------------------- #
# Normalized result schema (PRD "Suggested normalized result schema")
# --------------------------------------------------------------------------- #


class Signal(BaseModel):
    id: str
    label: str
    status: SignalStatus = "unknown"
    severity: Severity = "low"
    evidence: str = ""
    source_url: Optional[str] = None
    score_contribution: int = 0
    raw_contribution: int = 0
    max_weight: int = 0
    note: Optional[str] = None
    reason_unavailable: Optional[str] = None


class RedFlag(BaseModel):
    title: str
    explanation: str
    evidence: str
    confidence: Confidence = "medium"
    origin: Literal["signal", "model_inference"] = "signal"
    signal_id: Optional[str] = None


class AnalysisReport(BaseModel):
    # --- PRD normalized fields ---
    listing_url: Optional[str] = None
    risk_score: int = Field(ge=0, le=100)
    risk_band: RiskBand
    assessment_status: AssessmentStatus
    signals: List[Signal] = Field(default_factory=list)
    red_flags: List[RedFlag] = Field(default_factory=list)
    checks_unavailable: List[str] = Field(default_factory=list)
    recommended_actions: List[str] = Field(default_factory=list)
    disclaimer: str = "This is a risk assessment, not proof of counterfeit status."
    # --- extras for reproducibility / UI ---
    report_version: str = "1.0"
    request_id: str = ""
    generated_at: Optional[datetime] = None
    elapsed_ms: int = 0
    mode: Literal["url", "manual", "demo"] = "url"
    language: Literal["en", "ur"] = "en"
    listing: Optional[Listing] = None
    image_matches: List[ImageMatch] = Field(default_factory=list)
    provider_statuses: List[ProviderStatus] = Field(default_factory=list)
    unavailable_details: Dict[str, str] = Field(default_factory=dict)
    ai_summary: Optional[str] = None
    price_note: Optional[str] = None
    limitations: List[str] = Field(default_factory=list)
    insufficient_reason: Optional[str] = None
    demo_notice: Optional[str] = None
