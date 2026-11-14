"""Step 3: decide which tools to call, with RULES (criterion 3). Deterministic and testable.

intent      -> tools
predict     -> predict + coverage (k=5)
count       -> tests_search (grouped by the family's outcome column; O2 window if O2 was given)
coverage    -> predict (to get the "in the data?" verdict and reasons) + tests_search (what was tested) + coverage
compare     -> predict for each substance mentioned (same conditions) + tests_search per substance
explain_doc -> rag_search (+ explain for the family when a substance is named)
off_topic   -> nothing (polite refusal)
Every question about predictions also gets rag_search when the text asks "why" (mixed questions).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from flame.contracts import Conditions, RagSearchRequest, RiskRequest, SweepRequest, TestsSearchRequest

from .understand import SOLID_FAMILY_NAME, Understanding, normalize

FAMILY_ACCESSIONS = {'flex': ['PSI-69'], 'cfig': ['PSI-159'], 'solids': ['PSI-25', 'PSI-26', 'PSI-98']}
OUTCOME_COL = {'flex': 'test_end', 'cfig': 'end', 'solids': 'outcome_group'}


@dataclass
class ToolCall:
    tool: str                    # predict / coverage / explain / tests_search / rag_search
    args: object                 # Conditions, TestsSearchRequest, RagSearchRequest or family name
    label: str = ''              # e.g. the substance this call is about (for compare)


@dataclass
class Plan:
    calls: list[ToolCall] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _tests_request(u: Understanding, family: str, fuel: str | None, material: str | None) -> TestsSearchRequest:
    c = u.conditions
    o2 = c.o2_frac
    return TestsSearchRequest(
        family=family,
        fuel=fuel if family != 'solids' else None,
        material=SOLID_FAMILY_NAME.get(material) if family == 'solids' and material else None,
        flame_type=c.flame_type if family == 'cfig' else None,
        o2_min=round(o2 - 0.02, 4) if o2 is not None else None,
        o2_max=round(o2 + 0.02, 4) if o2 is not None else None,
        group_by=[OUTCOME_COL[family]] if family != 'cfig' else ['ignited', 'end'],
        limit=10)


def _substance_conditions(c: Conditions, sub: tuple[str, str, str]) -> Conditions:
    family, fld, code = sub
    d = c.model_dump(exclude_none=True)
    d.pop('fuel', None); d.pop('material', None)
    d.update(family=family, **{fld: code})
    return Conditions(**d)


def make_plan(u: Understanding) -> Plan:
    c, p = u.conditions, Plan()
    fam = c.family
    accessions = u.accessions or (FAMILY_ACCESSIONS.get(fam, []) if fam else [])
    asks_why = bool(re.search(r'por que|why|que dice (?:la )?nasa|what does nasa say|explica|explain', normalize(u.text)))

    if u.intent == 'off_topic':
        p.notes.append('off_topic')
        return p

    # v3 (mentors): early-warning risk by atmosphere, hypothetical scenarios, per-variable explanation
    if u.intent == 'risk':
        if c.o2_frac is None:
            p.notes.append('risk_needs_o2')
            return p
        p.calls.append(ToolCall('risk', RiskRequest(o2_frac=c.o2_frac, pressure_kpa=c.pressure_kpa or 101.325,
                                                    co2_frac=c.co2_frac or 0.0, he_frac=c.he_frac or 0.0,
                                                    horizon_s=c.horizon_s or 30.0)))
        return p

    if u.intent == 'scenario':
        if not fam:
            p.notes.append('no_subject')
            return p
        values = None
        if m := re.search(r'de (\d+(?:[.,]\d+)?) a (\d+(?:[.,]\d+)?) ?%|from (\d+(?:[.,]\d+)?) ?%? to (\d+(?:[.,]\d+)?) ?%', normalize(u.text)):
            a, b = [float(x.replace(',', '.')) / 100 for x in m.groups() if x][:2]
            if u.variable in ('o2_frac', 'co2_frac', 'he_frac') and b > a:
                values = [round(a + k * (b - a) / 8, 4) for k in range(9)]
        cond = c.model_copy(update={u.variable: None}) if u.variable in ('co2_frac', 'he_frac', 'o2_frac') and values else c
        p.calls.append(ToolCall('sweep', SweepRequest(conditions=cond, variable=u.variable, values=values)))
        p.calls.append(ToolCall('effects', u.variable))
        return p

    if u.intent == 'explain_variable':
        p.calls.append(ToolCall('effects', u.variable))
        q = f'{u.variable.replace("_frac", "").replace("_kpa", "").replace("_", " ")} effect on flame extinction microgravity'
        p.calls.append(ToolCall('rag_search', RagSearchRequest(query=q, accessions=accessions, k=30)))
        return p

    if u.intent == 'explain_doc':
        p.calls.append(ToolCall('rag_search', RagSearchRequest(query=u.text, accessions=accessions, k=30)))
        if fam:
            p.calls.append(ToolCall('explain', fam))
        return p

    if u.intent == 'count':
        if not fam:
            p.notes.append('count_without_subject')
            p.calls.append(ToolCall('rag_search', RagSearchRequest(query=u.text, accessions=accessions, k=30)))
            return p
        for sub in (u.substances or []):
            sf, _, code = sub
            p.calls.append(ToolCall('tests_search', _tests_request(u, sf, code if sf != 'solids' else None,
                                                                    code if sf == 'solids' else None), label=code))
        return p

    if u.intent == 'compare' and len(u.substances) >= 2:
        for sub in u.substances[:3]:
            sc = _substance_conditions(c, sub)
            p.calls.append(ToolCall('predict', sc, label=sub[2]))
            sf, _, code = sub
            p.calls.append(ToolCall('tests_search', _tests_request(u, sf, code if sf != 'solids' else None,
                                                                    code if sf == 'solids' else None), label=code))
        return p

    # predict, coverage and compare-with-one-substance
    if not fam and c.gravity_g is None and not u.unknown_terms:
        p.notes.append('no_subject')     # e.g. a material NASA did not test (Kevlar): say what was tested, call nothing
        return p
    if fam or c.gravity_g is not None:
        p.calls.append(ToolCall('predict', c if fam else Conditions(**{**c.model_dump(exclude_none=True), 'family': 'flex', 'fuel': 'methanol', 'o2_frac': c.o2_frac or 0.21}),
                                label='gravity_check' if not fam else ''))
    if fam:
        p.calls.append(ToolCall('coverage', c))
        if u.intent == 'coverage':
            sub = u.substances[0]
            p.calls.append(ToolCall('tests_search', _tests_request(u, fam, sub[2] if fam != 'solids' else None,
                                                                    sub[2] if fam == 'solids' else None), label=sub[2]))
    if asks_why or fam == 'solids':
        p.calls.append(ToolCall('rag_search', RagSearchRequest(query=u.text, accessions=accessions, k=30)))
    return p
