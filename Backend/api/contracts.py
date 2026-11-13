"""Contract between the two halves of the project (docs/DOCUMENTO_TECNICO.md §7.2).

PROVISIONAL MIRROR. The source of truth is `flame/contracts.py`, owned by Lautaro [LC].
When it is published, import from there and delete this file. Field names follow R5.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Conditions(BaseModel):
    family: Literal["flex", "solids", "cfig"] | None = None
    o2_frac: float | None = Field(None, ge=0, le=1)
    pressure_kpa: float | None = Field(None, gt=0)
    flow_cm_s: float | None = Field(None, ge=0)
    co2_frac: float | None = Field(None, ge=0, le=1)
    he_frac: float | None = Field(None, ge=0, le=1)
    material: str | None = None          # e.g. "pmma", "sibal", "nomex"
    thickness_mm: float | None = Field(None, gt=0)
    fuel: str | None = None              # e.g. "methanol", "heptane", "ethane"
    gravity_g: float | None = Field(None, ge=0)  # 0 = microgravity; anything else is outside the data


class Prediction(BaseModel):
    family: str
    target: str                          # e.g. "extinction_before_burnout"
    probability: float | None
    ci_low: float | None
    ci_high: float | None
    in_domain: bool                      # False = no-data zone
    out_of_domain: list[str]
    contributions: dict[str, float]      # per-variable contribution (log-odds)
    nearest_tests: list[str]             # test_id of the most similar real tests
    model_version: str


class EvidenceItem(BaseModel):
    test_id: str
    psi_id: str
    distance: float                      # normalized similarity of the conditions
    outcome: str | None
    source: str                          # file + row


class Citation(BaseModel):
    doc_id: str
    psi_id: str | None = None
    title: str | None = None
    page_or_section: str | None = None
    snippet: str
    url: str | None = None


class Answer(BaseModel):
    understood: Conditions
    intent: str
    unknown_terms: list[str]
    text: str                            # English answer built from templates
    predictions: list[Prediction]
    evidence: list[EvidenceItem]
    citations: list[Citation]
    chart: dict | None


class AskRequest(BaseModel):
    text: str = Field(..., min_length=3, max_length=500)
