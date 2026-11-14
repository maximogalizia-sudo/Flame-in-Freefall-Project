"""LLM drafter (criterion 1): writes the answer ONLY from a numbered evidence block built from the tool outputs.

The LLM receives: the question, what was understood, the template answer (the facts, already correct), and the
evidence items [T1..] (models / tables), [E1..] (closest NASA tests) and [D1..] (report passages). It must start
with [[DECISION:ANSWER|PARTIAL|ABSTAIN]], cite every fact, add nothing, and write in the question's language.
`verify.verify` checks the draft; any problem -> the template answer is kept (criterion 4).
"""
from __future__ import annotations

from .llm import get_llm
from .ml_insight import drivers, relevant_rows
from .verify import dumps, parse_decision, verify

SYSTEM = """You are the assistant of a NASA microgravity-combustion data tool. You write the final answer for the user.

YOUR ROLE: explain what the team's MACHINE-LEARNING SYSTEM understood. The ML models are the brain: lead with their
conclusion and WHY (the 'WHY_according_to_the_model' drivers and what the models learned about each variable). NASA test
tables [T..], closest real tests [E..] and report passages [D..] are SUPPORT for that explanation, not the answer itself.

STRICT RULES
1. Use ONLY the facts in the EVIDENCE block. Do not add numbers, tests or materials that are not there, and no outside physics.
   SUPPORTED INFERENCE IS ALLOWED AND EXPECTED: a conclusion that follows directly from what the cited evidence says, even if
   it is not written in those exact words (e.g. "the needles had a coating that dissolved and made bubbles" implies "the
   break-ups were largely an experimental problem, not flame physics"). Say it as an inference ("this indicates that...",
   "esto indica que..."), cite the passage it comes from, and never attach a new number to it.
   When several passages point the same way, give the MAIN explanation they support first, then the examples; do not just
   list passages, and do not refuse to conclude when the evidence clearly supports a conclusion.
2. Every number you write must appear in the EVIDENCE (you may write a fraction like 0.98 as 98 %). Never round differently.
3. Cite the evidence after each fact with its label: [T1], [E1], [D2]. Never cite a label that does not exist.
4. The FIRST line must be exactly one of: [[DECISION:ANSWER]] / [[DECISION:PARTIAL]] / [[DECISION:ABSTAIN]]
   - ANSWER: the evidence answers the central question.
   - PARTIAL: only part of it, or only related evidence (e.g. NASA observations without a model).
   - ABSTAIN: the central question cannot be answered with this evidence (e.g. the conditions were never tested).
   Use the SUGGESTED DECISION unless the evidence clearly says otherwise.
5. If something was never tested or is outside the data, say so plainly and explain why, using the evidence. Then do NOT
   stop there: give the TENDENCY the models learned for the variables involved (from 'what the ML models learned'),
   clearly labelled as a tendency from other tested conditions, not a prediction for this case, and the closest real tests.
6. Mention the assumptions and limits that the evidence lists (assumed values, moderate confidence, effects that cannot be separated).
   CONFIDENCE MATCHES THE EVIDENCE: when the SUGGESTED DECISION is ANSWER, the FIRST sentence states the answer directly and
   confidently (e.g. "Sí, se apaga: 98 %…", "Una llama fría es…"). Do not open with what the evidence does not say, and do
   not add generic doubts ("however, we cannot be sure"). Put the real limits from the evidence in ONE short closing line.
   Hedging is only for what the evidence itself marks as uncertain (wide interval, moderate confidence, not tested).
7. Scenario sweeps: give the probability at the start and at the end of the TESTED part of the range (they are in the
   evidence) and say clearly where NASA's data end; do not refuse just because part of the range was not tested.
   Risk indicator: give the overall level, the scenarios behind it, the untested ones, and always the disclaimer.
8. Analytic evidence (profile, ranking, gaps) summarises ALL the NASA tests. For a range/threshold question say plainly
   whether an "always" or "never" zone exists in the tested range (if none exists, say so and give the lowest/highest
   value where it was observed). For a ranking, list the configurations in order with their number of tests. For future
   experiments, propose them from the gaps listed. Respect the number of items the user asked for.
9. Write in {lang_name}. Be clear for a non-expert: short paragraphs, no jargon without a one-line explanation. At most ~170 words (up to ~250 for rankings, lists or profiles).
"""
LANG = {'es': 'Spanish (Rioplatense, neutral)', 'en': 'English'}


def build_evidence(u, plan, g) -> tuple[str, set[str]]:
    items, labels = [], set()
    k = 0
    for label, p in g.predictions:
        k += 1; labels.add(f'T{k}')
        d = {'about': label or (u.conditions.fuel or u.conditions.material), 'target': p.target, 'probability': p.probability,
             'interval_95': [p.ci_low, p.ci_high], 'sensitivity_range': p.range_sensitivity, 'in_the_data': p.in_domain,
             'why_not_in_data': p.out_of_domain, 'assumptions': p.assumptions, 'notes': p.notes,
             'WHY_according_to_the_model': drivers(p)}       # what the ML understood for this case, strongest first
        items.append(f'[T{k}] ML model prediction: {dumps(d)}')
    for label, req, resp in g.counts:
        k += 1; labels.add(f'T{k}')
        items.append(f'[T{k}] NASA test table query {dumps({"about": label, "filters": {x: v for x, v in req.items() if x not in ("limit", "group_by")}})}: '
                     f'{resp.n_total} tests; outcome counts {dumps(resp.counts[:8])}')
    if g.risk is not None:                       # v3: early-warning indicator
        k += 1; labels.add(f'T{k}')
        r = g.risk
        items.append(f'[T{k}] early-warning risk indicator for atmosphere {dumps(r.atmosphere.model_dump())}: overall level {r.level}; '
                     f'{r.headline} Per scenario: ' + '; '.join(f'{i.scenario}: {i.level} ({i.reasons[0]})' for i in r.items)
                     + f'. DISCLAIMER (must be stated): {r.disclaimer}')
    if g.sweep is not None:                      # v3: hypothetical scenario
        k += 1; labels.add(f'T{k}')
        sw = g.sweep
        pts = ', '.join(f'{p.x:g}->{p.probability if p.probability is not None else "not tested"}' for p in sw.points)
        items.append(f'[T{k}] scenario sweep of {sw.variable} (target {sw.target}; probability that the flame goes out): {pts}; '
                     f'tested range {sw.tested_range}; notes {dumps(sw.notes)}')
    for e in g.effects:                          # v3: what the models learned about a variable
        if u.intent not in ('explain_variable', 'scenario'):
            e = e.model_copy(update={'rows': relevant_rows(e, u.conditions)})
        k += 1; labels.add(f'T{k}')
        items.append(f'[T{k}] what the ML models learned about {e.variable} (ratio > 1 = more likely / sooner to go out): '
                     + '; '.join(f'{r.scenario}, {r.effect}: x{r.ratio} (95% {r.ci_low}-{r.ci_high}) {r.direction}' for r in e.rows)
                     + f'. Tested: {dumps(e.tested)}. Never tested: {dumps(e.not_tested)}. Caveats: {dumps(e.caveats)}')
    for kind, data in g.analytics:               # analytic results over ALL the data (observed NASA tests, with counts)
        k += 1; labels.add(f'T{k}')
        if kind == 'profile':
            groups = [x for x in data['groups'] if x['group'].endswith('- all')] or data['groups']
            body = '; '.join(f"{x['group']} [outcome = {x['meaning']}]: n={x['n']}, tested {x['tested_range']}, observed from "
                             f"{x['min_where_observed']} to {x['max_where_observed']}, ALWAYS zones {x['zones_always']}, NEVER zones "
                             f"{x['zones_never']}, bands {[(b['from'], b['to'], b['n'], b['share']) for b in x['bands']]}"
                             for x in groups[:6])
            items.append(f"[T{k}] profile of {data['label']} over all NASA tests (zones are [from, to, n_tests]; bands are "
                         f"(from, to, n_tests, share where the outcome happened)): {body}. Notes: {dumps(data['notes'])}")
        elif kind == 'rank':
            body = '; '.join(f"{r['configuration']} -> n_tests {r['n_tests']}, kept burning {r['share_kept_burning']}, went out "
                             f"{r['share_went_out']}, median burn time s {r['median_burn_time_s']}"
                             + (' (lower bound: still burning when cut)' if r['burn_time_is_lower_bound'] else '') for r in data['rows'])
            items.append(f"[T{k}] ranking of tested configurations by '{data['target']}' (observed NASA data, per family): {body}. "
                         f"Notes: {dumps(data['notes'])}")
        else:
            items.append(f"[T{k}] gaps in the NASA data and the experiment that would cover each: "
                         + '; '.join(f"{i['gap']} -> suggested: {i['suggested_test']}" for i in data['items']))
    for j, e in enumerate(g.evidence[:5], 1):
        labels.add(f'E{j}')
        items.append(f'[E{j}] closest real NASA test {e.test_id} ({e.psi_id}): outcome {e.outcome}; source {e.source[:160]}')
    for j, d in enumerate(g.passages[:5], 1):
        labels.add(f'D{j}')
        page = f" p.{d['page_from']}" if d.get('page_from') else ''
        items.append(f"[D{j}] NASA report '{d.get('doc_title') or d['file_name']}'{page}: \"{d['content'][:520]}\"")
    if 'rag_search' in g.unavailable:
        items.append('[note] Search in NASA reports is not available right now.')
    if u.unknown_terms:
        items.append(f'[note] NASA did not measure: {", ".join(u.unknown_terms)}.')
    return '\n'.join(items) or '[note] No tool returned evidence for this question.', labels


def make_drafter(llm=None):
    llm = llm or get_llm()
    if llm is None:
        return None

    def drafter(u, plan, g, template_text: str, template_decision: str):
        evidence, labels = build_evidence(u, plan, g)
        # The template text is NOT sent (it repeats the evidence and costs tokens on the free tier); it is still
        # passed to the verifier as an allowed source of numbers.
        user = (f'QUESTION: {u.text}\n\nUNDERSTOOD: {dumps(u.conditions.model_dump(exclude_none=True))} intent={u.intent}\n\n'
                f'SUGGESTED DECISION: {template_decision.upper()}\n\nEVIDENCE:\n{evidence}')
        raw = llm.chat(SYSTEM.format(lang_name=LANG.get(u.lang, 'English')), user)
        if raw is None:
            return None                       # LLM unavailable -> template
        decision, text = parse_decision(raw)
        problems = verify(text, decision, u.text, evidence + ' ' + template_text, labels, template_decision)
        return text, (decision or template_decision), problems

    return drafter
