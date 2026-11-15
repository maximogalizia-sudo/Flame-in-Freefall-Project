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
    text: str                            # answer written by the assistant ONLY from tool outputs (v2)
    predictions: list[Prediction]
    evidence: list[EvidenceItem]
    citations: list[Citation]
    chart: dict | None
    # v2 (2026-11-14): grounded assistant
    decision: Literal["answer", "partial", "abstain"] = "answer"   # abstain = the data cannot answer the central question
    tools_used: list[str] = []           # e.g. ["predict", "rag_search"]
    verification: list[str] = []         # problems found by the verifier (empty = passed)
    trace_id: str | None = None          # to find the full trace in the assistant's audit log
    assistant_version: str | None = None


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class AskRequest(BaseModel):
    text: str = Field(..., min_length=3, max_length=500)
    history: list[ChatTurn] = Field(default_factory=list, max_length=10)   # v2: previous turns of the chat (optional)
    lang: Literal["es", "en"] | None = None                                # v2: answer language; None = same as the question


# ============================================================================ v2: tool endpoints
# The assistant (LLM service, LC) calls these endpoints of the backend API (MG). The LLM never gets DB credentials
# and never writes SQL: only these whitelisted, typed requests.

class TestsSearchRequest(BaseModel):
    """Tabular tool over the curated tables (data/processed/*.parquet loaded into Postgres)."""
    family: Literal["flex", "cfig", "solids"]
    fuel: str | None = None              # flex: methanol / heptane ; cfig: ethane / propane / butane
    material: str | None = None          # solids: material family as in bass_tests.material (e.g. "Nomex", "SIBAL fabric")
    flame_type: Literal["normal", "inverse"] | None = None
    o2_min: float | None = Field(None, ge=0, le=1)
    o2_max: float | None = Field(None, ge=0, le=1)
    pressure_kpa_min: float | None = None
    pressure_kpa_max: float | None = None
    diluent: Literal["CO2", "He", "N2 only"] | None = None   # flex
    outcome: str | None = None           # value of the family's outcome column (test_end / end / outcome_group)
    only_model_rows: bool = False        # flex: in_main_model ; cfig: ext_model
    group_by: list[str] = Field(default_factory=list, max_length=3)   # whitelisted columns -> counts per group
    limit: int = Field(20, ge=1, le=200)


class TestsSearchResponse(BaseModel):
    family: str
    n_total: int                         # rows matching the filters
    counts: list[dict]                   # one dict per group: {<group cols>..., "n": int} (empty if no group_by)
    rows: list[dict]                     # up to `limit` rows with test_id, conditions, outcome, source (+ observation for solids)


class RagSearchRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=500)
    accessions: list[str] = Field(default_factory=list)   # e.g. ["PSI-69"]; empty = all documents
    k: int = Field(30, ge=1, le=60)                       # candidates per channel before fusion


class RagCandidate(BaseModel):
    chunk_id: int
    accession: str                       # 'PSI-69'
    doc_title: str | None
    file_name: str
    page_from: int | None
    page_to: int | None
    content: str
    source_url: str | None
    rank_dense: int | None               # position in the vector search (None = not retrieved there)
    rank_lexical: int | None             # position in the full-text search
    score_rrf: float                     # reciprocal-rank fusion score (diagnostic, not a probability)


class RagSearchResponse(BaseModel):
    query: str
    embedding_model: str                 # must be the same model used to index
    candidates: list[RagCandidate]       # sorted by score_rrf; reranking happens in the assistant


# ============================================================================ v3 (2026-11-14): mentors' feedback
# Early-warning risk indicator by atmosphere, hypothetical scenarios (sweeps) and per-variable explanations.
# Served by MG's API as thin wrappers of flame.scenarios (like /predict); also used by the assistant.

RiskLevel = Literal["green", "yellow", "red", "gray"]


class RiskRequest(BaseModel):
    """A cabin / habitat atmosphere to evaluate."""
    o2_frac: float = Field(..., ge=0.05, le=1)
    pressure_kpa: float = Field(101.325, gt=0)
    co2_frac: float = Field(0.0, ge=0, le=1)
    he_frac: float = Field(0.0, ge=0, le=1)
    horizon_s: float = Field(30.0, gt=0)          # gas flames: "goes out by itself within this time"


class RiskItem(BaseModel):
    scenario: str                        # e.g. "methanol droplet", "propane gas flame", "Nomex (fabric)"
    family: Literal["flex", "cfig", "solids"]
    level: RiskLevel                     # red = flames tend to keep burning; green = tend to go out; gray = not tested
    persistence: float | None            # probability that the flame keeps burning (1 - P(goes out)); None = no model
    ci_low: float | None
    ci_high: float | None
    in_domain: bool
    reasons: list[str]                   # why this level (incl. why "not tested")
    nearest_tests: list[str] = []


class RiskResponse(BaseModel):
    atmosphere: RiskRequest
    level: RiskLevel                     # worst level among scenarios that have data ("gray" if none has)
    items: list[RiskItem]
    headline: str                        # one sentence, English, built from the items (no new numbers)
    disclaimer: str                      # always shown: indicator from microgravity lab experiments, not a certified alarm
    model_version: str


SweepVariable = Literal["o2_frac", "co2_frac", "he_frac", "pressure_kpa", "droplet_d0_mm", "horizon_s"]


class SweepRequest(BaseModel):
    """Hypothetical scenario: vary one input, keep the rest of `conditions`."""
    conditions: Conditions
    variable: SweepVariable
    values: list[float] | None = Field(None, max_length=60)   # None = a grid over the tested range


class SweepPoint(BaseModel):
    x: float
    probability: float | None            # None where the combination was never tested ("we don't know")
    ci_low: float | None
    ci_high: float | None
    in_domain: bool


class SweepResponse(BaseModel):
    variable: str
    target: str
    points: list[SweepPoint]
    tested_range: list[float] | None     # [min, max] of the variable in clean NASA tests of this combination
    notes: list[str] = []
    model_version: str


class EffectRow(BaseModel):
    scenario: str                        # e.g. "methanol droplets", "heptane droplets", "normal gas flames"
    effect: str                          # e.g. "+5 points of CO2"
    ratio: float                         # odds ratio (droplets) or hazard ratio (gas flames); > 1 = goes out more / sooner
    ci_low: float
    ci_high: float
    direction: Literal["helps extinction", "keeps it burning", "no clear effect"]


class EffectsResponse(BaseModel):
    """Per-variable explanation built from the models + the data (the assistant adds NASA report passages)."""
    variable: str
    rows: list[EffectRow]
    tested: list[str]                    # where NASA tested this variable (ranges per fuel / pressure)
    not_tested: list[str]                # combinations never tested
    caveats: list[str]                   # what cannot be separated, moderate confidence, etc.
    model_version: str


# ============================================================================ v4 (2026-11-14): flame duration
class DurationPoint(BaseModel):
    t_s: float
    still_burning: float                 # share of flames still burning at t (model), 0..1
    ci_low: float
    ci_high: float


class DurationResponse(BaseModel):
    """How long a lit flame lasts before going out by itself (gas flames: survival model; droplets: observed NASA times)."""
    family: str
    in_domain: bool
    reasons: list[str] = []              # why no curve (not tested combination)
    median_s: float | None = None        # model: time at which half of such flames have gone out by themselves
    curve: list[DurationPoint] = []      # empty when there is no model for this case
    observed: list[dict] = []            # closest real NASA tests: test_id, conditions, burn_time_s, how it ended
    notes: list[str] = []
    model_version: str


# ============================================================================ v5 (2026-11-14): planned experiment vs a change
# A researcher plans an experiment (one fire + atmosphere) and, BEFORE changing a parameter (e.g. lowering CO2), checks
# what the models expect with the change. Both sides are model predictions (never a comparison against real results).

class CompareRequest(BaseModel):
    plan: Conditions                     # the planned experiment (one fire: fuel + atmosphere)
    change: Conditions                   # the same experiment with the parameter(s) changed


class CompareSide(BaseModel):
    level: RiskLevel
    persistence: float | None            # chance the flame keeps burning (droplets: burns completely; gas: still lit at horizon)
    ci_low: float | None
    ci_high: float | None
    in_domain: bool
    median_s: float | None = None        # gas flames: median time until it goes out by itself
    reasons: list[str] = []


class ChangeRow(BaseModel):
    variable: str                        # o2_frac, pressure_kpa, co2_frac, he_frac, ...
    label: str                           # e.g. "CO2 15 % -> 10 %"
    alone: float | None                  # persistence if ONLY this variable changed (None = not tested there)
    delta: float | None                  # alone - plan persistence
    direction: Literal["keeps it burning", "helps extinction", "no clear effect", "unknown"]
    text: str


class CompareResponse(BaseModel):
    scenario: str                        # e.g. "propane gas flame"
    family: str
    meaning: str                         # what "persistence" means for this fire
    plan: CompareSide
    change: CompareSide
    verdict: Literal["worse", "better", "about the same", "unknown"]   # worse = more likely to keep burning
    headline: str                        # one sentence, English, built from the numbers above
    changes: list[ChangeRow]
    notes: list[str] = []
    disclaimer: str
    model_version: str
