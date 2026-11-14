"""Orchestrates one answer: understand -> plan -> tools -> (rerank) -> draft -> verify -> Answer v2 + trace.

Step 1 (this version): drafting is done by templates. Step 3 plugs the LLM drafter + verifier in `_draft`;
if the LLM is missing or its draft fails verification, the template text is used (criterion 4).
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field

from flame.contracts import Answer, AskRequest, Citation, Conditions, EvidenceItem, Prediction

from . import VERSION
from . import templates as TP
from .config import get_settings
from .plan import OUTCOME_COL, Plan, make_plan
from .ml_insight import drivers, relevant_rows
from .retrieve import select_passages, translate_query
from .tools import ToolResult, get_tools
from .understand import Understanding, understand


@dataclass
class Gathered:
    """Everything the tools returned, numbered so a drafted text can cite it ([T1], [D1]...)."""
    predictions: list[tuple[str, Prediction]] = field(default_factory=list)   # (label, prediction)
    evidence: list[EvidenceItem] = field(default_factory=list)
    counts: list[tuple[str, dict, object]] = field(default_factory=list)      # (label, request dict, TestsSearchResponse)
    passages: list[dict] = field(default_factory=list)                        # rag candidates (after rerank)
    explain: dict | None = None
    failures: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    results: list[ToolResult] = field(default_factory=list)
    query_translation: str | None = None                                      # as_is / llm / glossary
    risk: object | None = None                                                # RiskResponse (v3)
    sweep: object | None = None                                               # SweepResponse (v3)
    effects: list = field(default_factory=list)                               # EffectsResponse list (v3)


def merge_history(u: Understanding, req: AskRequest) -> Understanding:
    """Follow-ups like "¿y con 30 % de CO2?": inherit the previous user question's subject and conditions."""
    if u.conditions.family or not req.history:
        return u
    prev_user = [h.text for h in req.history if h.role == 'user']
    if not prev_user:
        return u
    pu = understand(prev_user[-1])
    if not pu.conditions.family:
        return u
    merged = {**pu.conditions.model_dump(exclude_none=True), **u.conditions.model_dump(exclude_none=True)}
    u.conditions = Conditions(**merged)
    u.substances = pu.substances
    if u.intent in ('off_topic', 'explain_doc', 'coverage') and u.intent != 'explain_doc' or u.intent == 'off_topic':
        u.intent = pu.intent if pu.intent != 'off_topic' else 'predict'
    u.notes.append('conditions inherited from the previous question')
    return u


def run_tools(plan: Plan, tools, lang: str = 'en', llm=None) -> Gathered:
    g = Gathered()
    for call in plan.calls:
        if call.tool == 'rag_search':     # English-only index: translate the search query, keep the user's question
            q, method = translate_query(call.args.query, lang, llm)
            call.args = call.args.model_copy(update={'query': q})
            g.query_translation = method
        if call.tool == 'predict':
            r = tools.predict(call.args)
            if r.ok:
                g.predictions += [(call.label, p) for p in r.data]
        elif call.tool == 'coverage':
            r = tools.coverage(call.args, k=5)
            if r.ok:
                g.evidence += r.data
        elif call.tool == 'explain':
            r = tools.explain(call.args)
            if r.ok:
                g.explain = r.data
        elif call.tool == 'tests_search':
            r = tools.tests_search(call.args)
            if r.ok:
                g.counts.append((call.label, call.args.model_dump(exclude_none=True), r.data))
        elif call.tool in ('risk', 'sweep', 'effects'):
            r = getattr(tools, call.tool)(call.args)
            if r.ok:
                if call.tool == 'effects':
                    g.effects.append(r.data)
                else:
                    setattr(g, call.tool, r.data)
        elif call.tool == 'rag_search':
            r = tools.rag_search(call.args)
            if r.ok:
                g.passages = select_passages([c.model_dump() for c in r.data.candidates], k=6)
        else:
            continue
        g.results.append(r)
        if not r.ok:
            (g.unavailable if (r.error or '').startswith('unavailable') else g.failures).append(r.name)
    return g


def _understood_text(c: Conditions, lang: str) -> str:
    parts = []
    if c.fuel or c.material:
        parts.append(f"{'combustible' if lang == 'es' and c.fuel else 'material' if c.material else 'fuel'} = {c.fuel or c.material}")
    for fld, lab in (('o2_frac', 'O₂'), ('co2_frac', 'CO₂'), ('he_frac', 'He')):
        v = getattr(c, fld)
        if v is not None:
            parts.append(f'{lab} = {v * 100:g} %')
    if c.pressure_kpa is not None:
        parts.append(f'{"presión" if lang == "es" else "pressure"} = {c.pressure_kpa:g} kPa ({c.pressure_kpa / 101.325:.2f} atm)')
    if c.droplet_d0_mm is not None:
        parts.append(f'{"gota" if lang == "es" else "droplet"} = {c.droplet_d0_mm:g} mm')
    if c.gravity_g is not None:
        parts.append(f'{"gravedad" if lang == "es" else "gravity"} = {c.gravity_g:g} g')
    return ', '.join(parts) or TP.t('nothing_understood', lang)


def _counts_sentence(label: str, req: dict, resp, lang: str) -> str:
    flt = ''
    if req.get('o2_min') is not None:
        flt = TP.t('o2_window', lang, a=f"{req['o2_min'] * 100:g}", b=f"{req['o2_max'] * 100:g}")
    name = label or req.get('material') or req.get('fuel') or req['family']
    if resp.n_total == 0:
        return TP.t('count_zero', lang, label=name, flt=flt)
    parts = []
    for row in resp.counts[:6]:
        n = row.get('n'); key = ', '.join(f'{v}' for k, v in row.items() if k != 'n')
        parts.append(f'{key}: {n}')
    return TP.t('count', lang, label=name, n=resp.n_total, flt=flt, x='; '.join(parts) or '—')


LEVEL_WORD = {'es': {'red': 'ROJO (tiende a seguir quemando)', 'yellow': 'AMARILLO (depende)', 'green': 'VERDE (tiende a apagarse)',
                     'gray': 'GRIS (NASA no lo probó: no se sabe)'},
              'en': {'red': 'RED (tends to keep burning)', 'yellow': 'YELLOW (it depends)', 'green': 'GREEN (tends to go out)',
                     'gray': 'GRAY (not tested by NASA: unknown)'}}


def risk_sentences(r, lang: str) -> list[str]:
    a = r.atmosphere
    head = {'es': f'Indicador temprano de riesgo para O₂ {a.o2_frac * 100:g} %, {a.pressure_kpa:g} kPa: nivel {LEVEL_WORD[lang][r.level]}.',
            'en': f'Early-warning indicator for O₂ {a.o2_frac * 100:g} %, {a.pressure_kpa:g} kPa: level {LEVEL_WORD[lang][r.level]}.'}[lang]
    out = [head]
    for i in r.items:
        out.append(f'· {i.scenario}: {LEVEL_WORD[lang][i.level]} — {i.reasons[0]}.')
    out.append(r.disclaimer)
    return out


def sweep_sentences(sw, lang: str) -> list[str]:
    pts = [p for p in sw.points if p.in_domain]
    if not pts:
        return [{'es': 'Para ese escenario NASA no tiene pruebas en ningún punto del rango: no se sabe.',
                 'en': 'NASA has no tests anywhere in that range for this scenario: unknown.'}[lang]]
    v = sw.variable
    fmt = (lambda x: f'{x * 100:g} %') if v.endswith('_frac') else (lambda x: f'{x:g}')
    first, last = pts[0], pts[-1]
    out = [{'es': f'Escenario: variando {v} de {fmt(first.x)} a {fmt(last.x)}, la probabilidad pasa de {first.probability * 100:.0f} % a {last.probability * 100:.0f} %.',
            'en': f'Scenario: varying {v} from {fmt(first.x)} to {fmt(last.x)}, the probability goes from {first.probability * 100:.0f} % to {last.probability * 100:.0f} %.'}[lang]]
    gaps = [p.x for p in sw.points if not p.in_domain]
    if gaps:
        out.append({'es': f'Fuera de {fmt(sw.tested_range[0])}–{fmt(sw.tested_range[1])} NASA no probó esta combinación: ahí no hay número.',
                    'en': f'Outside {fmt(sw.tested_range[0])}–{fmt(sw.tested_range[1])} NASA did not test this combination: no number there.'}[lang])
    return out


def effects_sentences(e, lang: str) -> list[str]:
    word = {'es': {'helps extinction': 'ayuda a que se apague', 'keeps it burning': 'la mantiene prendida', 'no clear effect': 'sin efecto claro'},
            'en': {'helps extinction': 'helps it go out', 'keeps it burning': 'keeps it burning', 'no clear effect': 'no clear effect'}}[lang]
    out = [{'es': f'Cómo actúa {e.variable} según los modelos:', 'en': f'How {e.variable} acts according to the models:'}[lang]]
    for r in e.rows:
        out.append(f'· {r.scenario}, {r.effect}: {word[r.direction]} (×{r.ratio:g}, 95 %: {r.ci_low:g}–{r.ci_high:g}).')
    if e.tested:
        out.append({'es': 'Dónde se probó: ', 'en': 'Where it was tested: '}[lang] + '; '.join(e.tested) + '.')
    if e.not_tested:
        out.append({'es': 'Nunca se probó: ', 'en': 'Never tested: '}[lang] + '; '.join(e.not_tested) + '.')
    out += e.caveats
    return out


def template_draft(u: Understanding, plan: Plan, g: Gathered) -> tuple[str, str]:
    """Returns (text, decision) built only from tool outputs."""
    lang = u.lang
    s = [TP.t('understood', lang, x=_understood_text(u.conditions, lang))]
    if u.unknown_terms:
        s.append(TP.t('unmeasured', lang, x=', '.join(u.unknown_terms)))
    if 'off_topic' in plan.notes:
        return TP.t('off_topic', lang), 'abstain'
    if 'no_subject' in plan.notes or 'count_without_subject' in plan.notes:
        s.append(TP.t('no_subject', lang))
    answered, partial = False, False
    for label, p in g.predictions:
        if label == 'gravity_check' and p.probability is None:
            s.append('; '.join(p.out_of_domain[:1]) + '.')
            continue
        prefix = f'[{label}] ' if label and label != 'gravity_check' else ''
        s += [prefix + x for x in TP.prediction_sentences(p, lang)]
        why = drivers(p, top=3)
        if why and p.target != 'droplet_breakup':
            s.append({'es': 'Por qué, según el modelo (frente a una prueba promedio de NASA): ',
                      'en': 'Why, according to the model (vs an average NASA test): '}[lang] + '; '.join(why) + '.')
        if p.probability is not None:
            answered = True
        elif p.target == 'ignition' or (p.target == 'no_model' and not any('above 22.7' in r for r in p.out_of_domain)):
            partial = True   # solids at > 22.7 % O2: nothing NASA tested is related enough -> stays an abstention
    first_in = next((p for _, p in g.predictions if p.probability is not None), None)
    if first_in and first_in.assumptions:
        s.append(TP.t('assumed', lang, x='; '.join(first_in.assumptions)))
    if first_in:
        s += [n for n in first_in.notes if not n.startswith('Contributions')][:2]
    for label, req, resp in g.counts:
        s.append(_counts_sentence(label, req, resp, lang))
        answered = answered or resp.n_total > 0
    if g.evidence:   # related evidence never upgrades an abstention (same rule as the base notebook's ABSTAIN)
        s.append(TP.t('nearest', lang, x=', '.join(f'{e.test_id} ({e.outcome})' for e in g.evidence[:3])))
    if any(c.tool == 'rag_search' for c in plan.calls):
        if g.passages:
            s.append(TP.t('docs', lang))
            for k, d in enumerate(g.passages[:2], 1):
                page = f"p. {d['page_from']}" if d.get('page_from') else ''
                s.append(f"[D{k}] {d.get('doc_title') or d['file_name']} {page}: \"{d['content'][:280].strip()}…\"")
            partial = True
        elif 'rag_search' in g.unavailable:
            s.append(TP.t('docs_unavailable', lang))
        else:
            s.append(TP.t('docs_none', lang))
    if 'risk_needs_o2' in plan.notes:
        s.append({'es': 'Para el indicador de riesgo necesito al menos el % de oxígeno de la atmósfera (y si podés, la presión).',
                  'en': 'For the risk indicator I need at least the oxygen % of the atmosphere (and the pressure if possible).'}[lang])
    if g.risk is not None:
        s += risk_sentences(g.risk, lang)
        answered = answered or g.risk.level != 'gray'
    if g.sweep is not None:
        s += sweep_sentences(g.sweep, lang)
        answered = answered or any(p.in_domain for p in g.sweep.points)
    for e in g.effects:
        if u.intent in ('explain_variable', 'scenario'):
            s += effects_sentences(e, lang)
            answered = answered or bool(e.rows)
        else:   # with a prediction, only what the model learned about the variables asked, for this fuel
            e2 = e.model_copy(update={'rows': relevant_rows(e, u.conditions)})
            s += effects_sentences(e2, lang)[:1 + len(e2.rows)]
    if g.failures:
        s.append(TP.t('tool_failed', lang, x=', '.join(sorted(set(g.failures)))))
    decision = 'answer' if answered else ('partial' if partial else 'abstain')
    return ' '.join(s), decision


def _citations(g: Gathered) -> list[Citation]:
    out = []
    for k, d in enumerate(g.passages[:5], 1):
        page = None if d.get('page_from') is None else (f"p. {d['page_from']}" + (f"-{d['page_to']}" if d.get('page_to') not in (None, d['page_from']) else ''))
        out.append(Citation(doc_id=f"D{k}:{d['chunk_id']}", psi_id=d['accession'], title=d.get('doc_title') or d['file_name'],
                            page_or_section=page, snippet=d['content'][:400], url=d.get('source_url')))
    return out


def answer(req: AskRequest, tools=None, drafter=None, llm=None) -> Answer:
    """`drafter` (step 3) = callable(u, plan, gathered, template_text) -> (text, decision, problems) or None."""
    t0 = time.perf_counter()
    tools = tools or get_tools()
    u = merge_history(understand(req.text), req)
    if req.lang:
        u.lang = req.lang
    plan = make_plan(u)
    g = run_tools(plan, tools, lang=u.lang, llm=llm)
    text, decision = template_draft(u, plan, g)
    verification, version = [], f'{VERSION}:template'
    if drafter is not None:
        drafted = drafter(u, plan, g, text, decision) if 'off_topic' not in plan.notes else None
        if drafted is None:
            verification = ['llm not used or unavailable: template answer']
        else:
            d_text, d_decision, problems = drafted
            if problems:
                verification = ['llm draft rejected: ' + p for p in problems]
            else:
                text, decision, version = d_text, d_decision, f'{VERSION}:llm'
    else:
        verification = ['no llm configured: template answer']
    ans = Answer(understood=u.conditions, intent=u.intent, unknown_terms=u.unknown_terms, text=text,
                 predictions=[p for _, p in g.predictions], evidence=g.evidence, citations=_citations(g), chart=None,
                 decision=decision, tools_used=[r.name for r in g.results], verification=verification,
                 trace_id=str(uuid.uuid4()), assistant_version=version)
    _trace(req, u, plan, g, ans, int((time.perf_counter() - t0) * 1000))
    return ans


def _trace(req, u, plan, g, ans, ms):
    try:
        rec = {'trace_id': ans.trace_id, 'ts': time.time(), 'ms': ms, 'question': req.text, 'lang': u.lang, 'intent': u.intent,
               'conditions': u.conditions.model_dump(exclude_none=True), 'plan': [c.tool for c in plan.calls], 'rag_query_translation': g.query_translation,
               'tools': [{'name': r.name, 'ok': r.ok, 'ms': r.ms, 'error': r.error} for r in g.results],
               'decision': ans.decision, 'version': ans.assistant_version, 'verification': ans.verification}
        with get_settings().trace_path.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
    except OSError:
        pass
