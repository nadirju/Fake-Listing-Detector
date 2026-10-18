from typing import Literal, Optional
from pydantic import BaseModel, Field, HttpUrl, ConfigDict

RiskBand = Literal["lower_observed_risk", "caution", "high_suspicion"]
SignalStatus = Literal["flagged", "clear", "unknown"]
Severity = Literal["low", "medium", "high"]

class Listing(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    listing_url: str = ""
    title: str = ""
    price: Optional[float] = None
    currency: str = ""
    description: str = ""
    seller_name: str = ""
    domain: str = ""
    image_urls: list[str] = Field(default_factory=list)
    contact_info_found: bool = False
    return_policy_found: bool = False
    raw_text: str = ""
    extraction_notes: list[str] = Field(default_factory=list)

class Signal(BaseModel):
    id: str
    title: str
    status: SignalStatus
    severity: Severity = "low"
    evidence: str
    source_url: Optional[str] = None
    score_contribution: int = 0

class RedFlag(BaseModel):
    title: str
    explanation: str
    evidence: str
    confidence: Literal["low", "medium", "high"] = "medium"
    source_url: Optional[str] = None

class AnalysisResult(BaseModel):
    listing_url: str
    risk_score: int = Field(ge=0, le=100)
    risk_band: RiskBand
    assessment_status: Literal["complete", "partial", "insufficient_evidence"]
    signals: list[Signal] = Field(default_factory=list)
    red_flags: list[RedFlag] = Field(default_factory=list)
    checks_unavailable: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    ai_summary: str = ""
    ai_provider_status: str = "not_configured"
    evidence_completeness: int = Field(ge=0, le=100, default=0)
    disclaimer: str = "This is an assistive risk assessment, not proof of counterfeit status."
    model_config = ConfigDict(extra="forbid")
