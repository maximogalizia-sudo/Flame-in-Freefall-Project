"""Contract between the two halves of the project (docs/DOCUMENTO_TECNICO.md §7.2).

Source of truth, owned by Lautaro [LC]. `Backend/api/contracts.py` was the provisional mirror.
Field names follow R5. Changes are logged in NOTAS.md as "CAMBIO DE CONTRATO".

v1 (2026-11-14) adds optional model inputs (all default to None = "use the typical tested value and say so"):
  support (flex: "free" / "fiber"), droplet_d0_mm (flex), fuel_flow_mg_s (cfig), jet_fuel_frac (cfig ignition),
  flame_type (cfig: "normal" / "inverse"), atm_fuel_frac (cfig inverse), horizon_s (cfig survival horizon).
Prediction gains `range_sensitivity` (FLEX: estimate if every Disruption were counted one way or the other),
`assumptions` (defaults that were filled in) and `notes`.
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
    # v1 optional model inputs
    support: Literal["free", "fiber"] | None = None          # flex: free-floating droplet or tethered on a fiber
    droplet_d0_mm: float | None = Field(None, gt=0)           # flex: initial droplet diameter
    flame_type: Literal["normal", "inverse"] | None = None   # cfig: fuel into O2 (normal) or O2 into fuel (inverse)
    fuel_flow_mg_s: float | None = Field(None, gt=0)          # cfig: fuel flow = flame size
    jet_fuel_frac: float | None = Field(None, ge=0, le=1)     # cfig normal: fuel fraction in the burner jet
    atm_fuel_frac: float | None = Field(None, ge=0, le=1)     # cfig inverse: fuel fraction in the chamber
    horizon_s: float | None = Field(None, gt=0)               # cfig: "goes out by itself within this many seconds"


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
    range_sensitivity: list[float] | None = None   # [low, high] under alternative labelings (FLEX Disruption)
    assumptions: list[str] = []                    # typical values filled in for inputs the user did not give
    notes: list[str] = []


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
