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

from flame.contracts import CompareRequest, Conditions, RagSearchRequest, RiskRequest, SweepRequest, TestsSearchRequest

from .ml_insight import variables_to_explain
from .understand import SOLID_FAMILY_NAME, Understanding, normalize

FAMILY_ACCESSIONS = {'flex': ['PSI-69'], 'cfig': ['PSI-159'], 'solids': ['PSI-25', 'PSI-26', 'PSI-98']}
ACCESSION_FAMILY = {'PSI-69': 'flex', 'PSI-159': 'cfig', 'PSI-25': 'solids', 'PSI-26': 'solids'}
ANALYTIC_VAR = {'o2_frac': 'o2', 'co2_frac': 'co2', 'he_frac': 'he', 'pressure_kpa': 'pressure', 'droplet_d0_mm': 'd0'}
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
    if (c.gravity_g or 0) > 0 and u.intent not in ('predict', 'coverage'):
        p.notes.append('partial_gravity')
        p.calls.append(ToolCall('predict', Conditions(**{**c.model_dump(exclude_none=True), 'family': fam or 'flex',
                                                          'fuel': c.fuel or ('methanol' if not fam else c.fuel),
                                                          'o2_frac': c.o2_frac or 0.21}), label='gravity_check'))

    # Analytic questions over ALL the data (flame.analytics): ranges/thresholds, rankings, gaps
    if u.intent == 'profile':
        var = ANALYTIC_VAR.get(u.variable or 'o2_frac', 'o2')
        p.calls.append(ToolCall('profile', (var, u.analytic_family)))
        return p
    if u.intent == 'rank':
        m = re.search(r'\b(\d{1,2})\b', normalize(u.text))
        k = min(max(int(m.group(1)), 1), 10) if m else 5
        p.calls.append(ToolCall('rank', (u.rank_target or 'persistence', u.analytic_family, k)))
        return p
    if u.intent == 'measure':          # DT-025: what NASA measured in the tests (+ the report, for the "why")
        prefer = [c.fuel or '', ', co2,' if c.co2_frac or re.search(r'co2|dioxido', normalize(u.text)) else '',
                  ', he,' if c.he_frac or re.search(r'helio|helium', normalize(u.text)) else '',
                  '0.7 atm' if (c.pressure_kpa or 101) < 85 else ('1 atm' if c.pressure_kpa else '')]
        p.calls.append(ToolCall('measure', (u.quantity, u.analytic_family, [w for w in prefer if w])))
        p.calls.append(ToolCall('rag_search', RagSearchRequest(query=u.text, accessions=accessions, k=30)))
        return p

    if u.intent == 'gaps':
        p.calls.append(ToolCall('gaps', u.analytic_family))
        p.calls.append(ToolCall('rag_search', RagSearchRequest(query='future work recommendations limitations further tests needed',
                                                               accessions=FAMILY_ACCESSIONS.get(u.analytic_family, []), k=30)))
        return p

    # v3 (mentors): early-warning risk by atmosphere, hypothetical scenarios, per-variable explanation
    if u.intent == 'risk':
        if c.o2_frac is None:
            p.notes.append('risk_needs_o2')
            # never "no data" without looking: the NASA reports may answer it (e.g. "should we turn off the ventilation?")
            p.calls.append(ToolCall('rag_search', RagSearchRequest(query=u.text, accessions=accessions, k=30)))
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
        unit = r' ?%' if u.variable in ('o2_frac', 'co2_frac', 'he_frac') else r' ?(?:kpa|mm)?'
        if m := re.search(rf'de (\d+(?:[.,]\d+)?)(?:{unit})? a (\d+(?:[.,]\d+)?){unit}|from (\d+(?:[.,]\d+)?)(?:{unit})? to (\d+(?:[.,]\d+)?){unit}',
                          normalize(u.text)):
            div = 100 if u.variable.endswith('_frac') else 1
            a, b = [float(x.replace(',', '.')) / div for x in m.groups() if x][:2]
            if a != b and u.variable in ('o2_frac', 'co2_frac', 'he_frac', 'pressure_kpa', 'droplet_d0_mm'):
                lo, hi = min(a, b), max(a, b)
                values = [round(lo + k * (hi - lo) / 8, 4) for k in range(9)]
                # v5: "from A to B" = a planned value and a change -> compare both with the models (direction matters)
                p.calls.append(ToolCall('compare', CompareRequest(plan=c.model_copy(update={u.variable: a}),
                                                                  change=c.model_copy(update={u.variable: b}))))
        cond = c.model_copy(update={u.variable: None}) if values else c
        p.calls.append(ToolCall('sweep', SweepRequest(conditions=cond, variable=u.variable, values=values)))
        p.calls.append(ToolCall('effects', u.variable))
        return p

    if u.intent == 'explain_variable':
        p.calls.append(ToolCall('effects', u.variable))
        q = f'{u.variable.replace("_frac", "").replace("_kpa", "").replace("_", " ")} effect on flame extinction microgravity'
        p.calls.append(ToolCall('rag_search', RagSearchRequest(query=q, accessions=accessions, k=30)))
        return p

    # Questions about a whole experiment ("how many FLEX tests?", "which fuels did FLEX use?", "at what pressure?"):
    # answered from that experiment's table, grouped by what is asked.
    exp_fam = next((ACCESSION_FAMILY[a] for a in u.accessions if a in ACCESSION_FAMILY), None)
    t = normalize(u.text)
    table_q = re.search(r'cuant[ao]s|how many|que (?:combustibles?|gases|materiales|presion\w*)|which (?:fuels?|gases|materials)|'
                        r'what (?:fuels?|gases|materials|pressures?)|a que presion|at what pressure', t)
    if exp_fam and not fam and table_q:
        gb = (['fuel'] if re.search(r'combustible|fuel|gas', t) and exp_fam != 'solids' else
              ['material'] if re.search(r'material', t) else
              (['p_level'] if exp_fam == 'flex' else ['pressure_bar']) if re.search(r'presion|pressure', t) and exp_fam != 'solids' else
              [OUTCOME_COL[exp_fam]] if exp_fam != 'cfig' else ['ignited', 'end'])
        p.calls.append(ToolCall('tests_search', TestsSearchRequest(family=exp_fam, group_by=gb, limit=5),
                                label={'flex': 'FLEX', 'cfig': 'CFI-G', 'solids': 'BASS / BASS-II'}[exp_fam]))
        if u.intent == 'explain_doc':
            p.calls.append(ToolCall('rag_search', RagSearchRequest(query=u.text, accessions=accessions, k=30)))
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
    # No fuel or material identified: never answer "no data" without searching the NASA reports first (DT-024). If the
    # passages really cover the question it is answered from them; if not, the "not identified / not tested" text stays.
    if not fam and c.gravity_g is None and not u.unknown_terms:
        p.notes.append('no_subject')     # e.g. a material NASA did not test (Kevlar), or no material at all (ventilation)
        p.calls.append(ToolCall('rag_search', RagSearchRequest(query=u.text, accessions=accessions, k=30)))
        return p
    if not fam and not (c.gravity_g or 0) > 0:
        p.notes.append('no_subject')     # e.g. "Kevlar on the space station": microgravity is fine, the material was not tested
        p.calls.append(ToolCall('rag_search', RagSearchRequest(query=u.text, accessions=accessions, k=30)))
        return p
    if fam or (c.gravity_g or 0) > 0:   # gravity check only when there IS gravity (Moon, Mars): always out of the data
        p.calls.append(ToolCall('predict', c if fam else Conditions(**{**c.model_dump(exclude_none=True), 'family': 'flex', 'fuel': 'methanol', 'o2_frac': c.o2_frac or 0.21}),
                                label='gravity_check' if not fam else ''))
    if fam:
        if c.gravity_g in (None, 0):     # the ML is the brain: what the models learned about the variables the user set
            for var in variables_to_explain(c, fam):
                p.calls.append(ToolCall('effects', var))
        p.calls.append(ToolCall('coverage', c))
        if u.intent == 'coverage':
            sub = u.substances[0]
            p.calls.append(ToolCall('tests_search', _tests_request(u, fam, sub[2] if fam != 'solids' else None,
                                                                    sub[2] if fam == 'solids' else None), label=sub[2]))
    if asks_why or fam == 'solids':
        p.calls.append(ToolCall('rag_search', RagSearchRequest(query=u.text, accessions=accessions, k=30)))
    return p
