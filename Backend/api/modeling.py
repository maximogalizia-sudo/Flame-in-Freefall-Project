"""Model-side functions exposed by the API: predict, explain, coverage, ask.

PLACEHOLDERS. The real implementations belong to Lautaro [LC] (models, coverage
logic and natural-language layer, docs/DOCUMENTO_TECNICO.md §7.2 and §9). These
versions only keep the API and the UI working end to end with the agreed shapes:

  * coverage(): nearest real tests from the database (a transparent baseline query).
  * predict(): share of self-extinguished outcomes among those neighbours, with a
    Wilson interval. It is NOT a model and says so in `model_version`.
  * ask(): regex parser for the question (or the LLM service as D2 fallback), then
    predict + coverage + document citations from the RAG service, with templated text.

Swap each function for LC's implementation without changing the API.
"""
from __future__ import annotations

import math
import re

from . import db
from .contracts import Answer, Conditions, EvidenceItem, Prediction
from .services import get_llm, get_rag

MODEL_VERSION = "placeholder-knn-v0"
FAMILY_GROUP = {"flex": "droplet", "solids": "solid", "cfig": "gas"}
FAMILY_TABLE = {"flex": "PSI-69", "cfig": "PSI-159", "solids": None}
SCALES = {"o2_frac": 0.1, "pressure_kpa": 50.0, "flow_cm_s": 10.0}   # one "unit" of distance per variable


def _envelope(family: str | None) -> dict | None:
    if not family:
        return None
    return db.query_one("SELECT * FROM mart.v_tested_envelope WHERE family_group = %s", (FAMILY_GROUP[family],))


def out_of_domain(c: Conditions) -> list[str]:
    reasons = []
    if c.gravity_g not in (None, 0):
        reasons.append(f"gravity_g={c.gravity_g} > 0: PSI has no partial-gravity combustion data")
    env = _envelope(c.family)
    if env:
        for var, lo, hi in (("o2_frac", "o2_frac_min", "o2_frac_max"),
                            ("pressure_kpa", "pressure_kpa_min", "pressure_kpa_max"),
                            ("flow_cm_s", "flow_cm_s_min", "flow_cm_s_max")):
            v = getattr(c, var)
            if v is None:
                continue
            if env[lo] is None:
                reasons.append(f"{var} was not measured for {env['family_group_name'].lower()}")
            elif not env[lo] <= v <= env[hi]:
                reasons.append(f"{var}={v:g} outside measured range {env[lo]:g}–{env[hi]:g} "
                               f"for {env['family_group_name'].lower()}")
    return reasons


def coverage(c: Conditions, k: int = 10) -> list[EvidenceItem]:
    """Nearest valid tests of the same family group, by normalized distance on the given conditions."""
    where, params = ["is_valid", "row_type = 'test'"], []
    if c.family:
        where.append("family_group = %s")
        params.append(FAMILY_GROUP[c.family])
    if c.material:
        where.append("material_code = %s")
        params.append(c.material)
    rows = db.query(f"SELECT test_id, psi_id, outcome, o2_frac, pressure_kpa, flow_cm_s, source_file, source_row "
                    f"FROM mart.v_tests WHERE {' AND '.join(where)}", tuple(params))
    given = {v: getattr(c, v) for v in SCALES if getattr(c, v) is not None}
    scored = []
    for r in rows:
        if any(r[v] is None for v in given):
            continue
        d = math.sqrt(sum(((r[v] - x) / SCALES[v]) ** 2 for v, x in given.items())) if given else 0.0
        scored.append((d, r))
    scored.sort(key=lambda t: t[0])
    return [EvidenceItem(test_id=r["test_id"], psi_id=r["psi_id"], distance=round(d, 3), outcome=r["outcome"],
                         source=f"{r['source_file']} row {r['source_row']}") for d, r in scored[:k]]


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def predict(c: Conditions) -> list[Prediction]:
    family = c.family or ("solids" if c.material in {"sibal", "pmma", "nomex"} else None)
    if family is None:
        return []
    c = c.model_copy(update={"family": family})
    near = coverage(c, k=25)
    labelled = [e for e in near if e.outcome in {"extinction", "no_propagation", "blowoff", "complete", "flow_terminated"}]
    k = sum(e.outcome in {"extinction", "no_propagation", "blowoff"} for e in labelled)
    n = len(labelled)
    lo, hi = _wilson(k, n)
    ood = out_of_domain(c) + ([] if n else ["no labelled tests near these conditions"])
    estimate = not ood            # §2.1: never extrapolate outside the tested range
    return [Prediction(
        family=family, target="self_extinguished",
        probability=round(k / n, 3) if estimate else None,
        ci_low=round(lo, 3) if estimate else None, ci_high=round(hi, 3) if estimate else None,
        in_domain=estimate, out_of_domain=ood,
        contributions={}, nearest_tests=[e.test_id for e in labelled[:5]], model_version=MODEL_VERSION,
    )]


def explain(family: str) -> dict:
    env = _envelope(family) or {}
    counts = db.query(
        "SELECT outcome, count(*) AS n FROM mart.v_tests WHERE family_group = %s AND is_valid AND row_type = 'test' "
        "GROUP BY outcome ORDER BY n DESC", (FAMILY_GROUP[family],))
    return {"family": family, "model_version": MODEL_VERSION, "measured_ranges": env,
            "outcome_counts": counts, "coefficients": None, "partial_dependence": None,
            "note": "Placeholder: coefficients and curves come from LC's interpretable models."}


# ---------------------------------------------------------------- ask
GRAVITY = {"lunar": 0.165, "moon": 0.165, "martian": 0.38, "mars": 0.38, "microgravity": 0.0, "orbit": 0.0}
MATERIALS = {"cotton": "sibal", "sibal": "sibal", "fabric": "sibal", "pmma": "pmma", "acrylic": "pmma",
             "plexiglas": "pmma", "nomex": "nomex", "methanol": "methanol", "heptane": "n_heptane",
             "ethane": "ethane", "propane": "propane", "butane": "n_butane"}
FAMILY_OF = {"sibal": "solids", "pmma": "solids", "nomex": "solids", "methanol": "flex", "n_heptane": "flex",
             "ethane": "cfig", "propane": "cfig", "n_butane": "cfig"}
UNMEASURED = ["humidity", "temperature outside", "radiation", "dust", "regolith"]
PRESSURE_UNITS = {"psi": 6.89476, "psia": 6.89476, "kpa": 1.0, "atm": 101.325, "bar": 100.0}


def parse_question(text: str) -> tuple[Conditions, str, list[str]]:
    """Rule-based placeholder for LC's parser: extracts O2, pressure, flow, material and gravity."""
    t = text.lower()
    vals: dict = {}
    if m := re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:of\s+)?(?:oxygen|o2)", t):
        vals["o2_frac"] = float(m[1]) / 100
    if m := re.search(r"(\d+(?:\.\d+)?)\s*(psia|psi|kpa|atm|bar)\b", t):
        vals["pressure_kpa"] = round(float(m[1]) * PRESSURE_UNITS[m[2]], 3)
    if m := re.search(r"(\d+(?:\.\d+)?)\s*cm/s", t):
        vals["flow_cm_s"] = float(m[1])
    for word, code in MATERIALS.items():
        if re.search(rf"\b{word}\b", t):
            vals["material"] = code if FAMILY_OF.get(code) == "solids" else None
            vals["fuel"] = code if FAMILY_OF.get(code) != "solids" else None
            vals["family"] = FAMILY_OF[code]
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
        parts.append(f"material = {c.material or c.fuel}")
    if c.o2_frac is not None:
        parts.append(f"O2 = {c.o2_frac * 100:g} %")
    if c.pressure_kpa is not None:
        parts.append(f"pressure = {c.pressure_kpa:g} kPa")
    if c.flow_cm_s is not None:
        parts.append(f"airflow = {c.flow_cm_s:g} cm/s")
    if c.gravity_g is not None:
        parts.append(f"gravity = {c.gravity_g:g} g")
    return ", ".join(parts) or "no conditions"


def ask(text: str) -> Answer:
    parsed = parse_question(text)
    if not (parsed[0].o2_frac or parsed[0].material or parsed[0].fuel):
        llm = get_llm().parse(text)            # D2 fallback: input translation only
        if llm:
            parsed = (llm[0], llm[1], parsed[2])
    c, intent, unknown = parsed
    preds = predict(c)
    evidence = coverage(c, k=5) if c.family else []
    citations = get_rag().search(text, psi_ids=sorted({e.psi_id for e in evidence}), k=3) or []

    sentences = [f"Understood: {_understood_text(c)}."]
    if unknown:
        sentences.append(f"NASA did not measure: {', '.join(unknown)}.")
    p = preds[0] if preds else None
    if p is None:
        sentences.append("I could not match the question to an experiment family (droplets, gas flames or solids).")
    elif not p.in_domain:
        sentences.append("This is outside the NASA data, so no estimate is given: " + "; ".join(p.out_of_domain) + ".")
    else:
        sentences.append(f"Among the {len(p.nearest_tests)} closest labelled NASA tests, "
                         f"{p.probability * 100:.0f} % self-extinguished (95 % CI {p.ci_low * 100:.0f}–{p.ci_high * 100:.0f} %).")
    if evidence:
        e = evidence[0]
        sentences.append(f"Closest real test: {e.test_id} ({e.outcome}). Source: {e.source}.")
    sentences.append("Placeholder answer: the interpretable models are not plugged in yet.")
    return Answer(understood=c, intent=intent, unknown_terms=unknown, text=" ".join(sentences),
                  predictions=preds, evidence=evidence, citations=citations, chart=None)
