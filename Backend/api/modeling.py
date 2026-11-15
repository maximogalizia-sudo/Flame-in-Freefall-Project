"""Model-side functions exposed by the API: predict, coverage, explain (LC's `flame` package) and the
template answer used by /ask when the assistant service is not available.

predict / coverage / explain are LC's implementations (flame.models): interpretable models stored as JSON,
with the "we don't know" rule by combination built in. This module does not add any modelling logic.

`template_answer()` is the fallback for /ask (docs/PLAN_ASISTENTE.md, M4): a rule-based reading of the
question, then predict + coverage, with templated text. It is always marked decision="partial".
"""
from __future__ import annotations

import re

from flame import models as flame_models
from flame import scenarios as flame_scenarios

from .contracts import (Answer, CompareRequest, CompareResponse, Conditions, DurationResponse, EffectsResponse,
                        EvidenceItem, Prediction, RiskRequest, RiskResponse, SweepRequest, SweepResponse)

FALLBACK_NOTE = "assistant unavailable: template answer"


def predict(c: Conditions) -> list[Prediction]:
    return flame_models.predict(c)


def coverage(c: Conditions, k: int = 10) -> list[EvidenceItem]:
    return flame_models.coverage(c, k=k)


def explain(family: str) -> dict:
    return flame_models.explain(family)


# contract v3 (mentors' feedback): LC's flame.scenarios, no logic added here
def risk(req: RiskRequest) -> RiskResponse:
    return flame_scenarios.risk(req)


def sweep(req: SweepRequest) -> SweepResponse:
    return flame_scenarios.sweep(req)


def effects(variable: str) -> EffectsResponse:
    return flame_scenarios.effects(variable)


def duration(c: Conditions) -> DurationResponse:
    return flame_scenarios.duration(c)


def compare(req: CompareRequest) -> CompareResponse:
    return flame_scenarios.compare(req)


# ---------------------------------------------------------------- template answer (fallback for /ask)
GRAVITY = {"lunar": 0.165, "moon": 0.165, "martian": 0.38, "mars": 0.38, "microgravity": 0.0, "orbit": 0.0}
SUBSTANCES = {  # word in the question -> (family, field, code understood by flame.models)
    "cotton": ("solids", "material", "sibal"), "sibal": ("solids", "material", "sibal"),
    "fabric": ("solids", "material", "sibal"), "pmma": ("solids", "material", "pmma"),
    "acrylic": ("solids", "material", "pmma"), "plexiglas": ("solids", "material", "pmma"),
    "nomex": ("solids", "material", "nomex"), "ultem": ("solids", "material", "ultem"),
    "wax": ("solids", "material", "wax"), "candle": ("solids", "material", "wax"),
    "methanol": ("flex", "fuel", "methanol"), "heptane": ("flex", "fuel", "heptane"),
    "ethane": ("cfig", "fuel", "ethane"), "propane": ("cfig", "fuel", "propane"),
    "butane": ("cfig", "fuel", "butane"),
}
UNMEASURED = ["humidity", "temperature outside", "radiation", "dust", "regolith"]
PRESSURE_UNITS = {"psi": 6.89476, "psia": 6.89476, "kpa": 1.0, "atm": 101.325, "bar": 100.0}
PCT = r"(\d+(?:[.,]\d+)?)\s*%\s*(?:of\s+)?"


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def parse_question(text: str) -> tuple[Conditions, str, list[str]]:
    """Rule-based reading of the question: O2, CO2, He, pressure, flow, fuel/material and gravity."""
    t = text.lower()
    vals: dict = {}
    for field, gas in (("o2_frac", r"(?:oxygen|o2|o₂)"), ("co2_frac", r"(?:co2|co₂|carbon dioxide)"),
                       ("he_frac", r"(?:helium|he)\b")):
        if m := re.search(PCT + gas, t):
            vals[field] = _num(m[1]) / 100
    if m := re.search(r"(\d+(?:[.,]\d+)?)\s*(psia|psi|kpa|atm|bar)\b", t):
        vals["pressure_kpa"] = round(_num(m[1]) * PRESSURE_UNITS[m[2]], 3)
    if m := re.search(r"(\d+(?:[.,]\d+)?)\s*cm/s", t):
        vals["flow_cm_s"] = _num(m[1])
    for word, (family, field, code) in SUBSTANCES.items():
        if re.search(rf"\b{word}\b", t):
            vals.update(family=family, **{field: code})
            break
    for word, g in GRAVITY.items():
        if word in t:
            vals["gravity_g"] = g
            break
    intent = ("which_variable" if re.search(r"which (variable|factor)|matters most", t) else
              "evidence" if re.search(r"which (experiment|test)|evidence|source", t) else
              "coverage" if re.search(r"no data|gap|not tested", t) else "will_it_burn")
    unknown = [w for w in UNMEASURED if w in t]
    return Conditions(**vals), intent, unknown


def _understood_text(c: Conditions) -> str:
    parts = []
    if c.material or c.fuel:
        parts.append(f"{'material' if c.material else 'fuel'} = {c.material or c.fuel}")
    for field, label in (("o2_frac", "O2"), ("co2_frac", "CO2"), ("he_frac", "He")):
        v = getattr(c, field)
        if v is not None:
            parts.append(f"{label} = {v * 100:g} %")
    if c.pressure_kpa is not None:
        parts.append(f"pressure = {c.pressure_kpa:g} kPa")
    if c.flow_cm_s is not None:
        parts.append(f"airflow = {c.flow_cm_s:g} cm/s")
    if c.gravity_g is not None:
        parts.append(f"gravity = {c.gravity_g:g} g")
    return ", ".join(parts) or "no conditions"


def _prediction_sentence(p: Prediction) -> str:
    target = p.target.replace("_", " ")
    if p.probability is None:
        return f"No estimate for '{target}': " + "; ".join(p.out_of_domain) + "."
    s = (f"Estimated probability of '{target}': {p.probability * 100:.0f} % "
         f"(95 % interval {p.ci_low * 100:.0f}–{p.ci_high * 100:.0f} %).")
    if p.range_sensitivity:
        lo, hi = p.range_sensitivity
        s += f" Counting interrupted tests differently: {lo * 100:.0f}–{hi * 100:.0f} %."
    return s


def template_answer(text: str, note: str = FALLBACK_NOTE) -> Answer:
    c, intent, unknown = parse_question(text)
    has_subject = bool(c.material or c.fuel)
    preds = predict(c) if has_subject else []
    evidence = coverage(c, k=5) if has_subject else []

    sentences = [f"Understood: {_understood_text(c)}."]
    if unknown:
        sentences.append(f"NASA did not measure: {', '.join(unknown)}.")
    if not preds:
        sentences.append("I could not match the question to a NASA experiment (droplets, gas flames or solids).")
    elif all(not p.in_domain for p in preds):
        sentences.append("This is outside the NASA data, so no estimate is given.")
    sentences += [_prediction_sentence(p) for p in preds]
    if preds and preds[0].assumptions:
        sentences.append("Assumed: " + "; ".join(preds[0].assumptions) + ".")
    if evidence:
        e = evidence[0]
        sentences.append(f"Closest real test: {e.test_id} ({e.outcome}). Source: {e.source}.")
    sentences.append("This is an automatic template answer: the assistant service is not available right now.")
    return Answer(understood=c, intent=intent, unknown_terms=unknown, text=" ".join(sentences),
                  predictions=preds, evidence=evidence, citations=[], chart=None,
                  decision="partial", tools_used=["predict", "coverage"] if has_subject else [],
                  verification=[note], assistant_version="template-v1")
