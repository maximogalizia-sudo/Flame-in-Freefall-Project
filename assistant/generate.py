"""LLM drafter (criterion 1): writes the answer ONLY from a numbered evidence block built from the tool outputs.

The LLM receives: the question, what was understood, the template answer (the facts, already correct), and the
evidence items [T1..] (models / tables), [E1..] (closest NASA tests) and [D1..] (report passages). It must start
with [[DECISION:ANSWER|PARTIAL|ABSTAIN]], cite every fact, add nothing, and write in the question's language.
`verify.verify` checks the draft; any problem -> the template answer is kept (criterion 4).
"""
from __future__ import annotations

from .llm import get_llm
from .verify import dumps, parse_decision, verify

SYSTEM = """You are the assistant of a NASA microgravity-combustion data tool. You write the final answer for the user.

STRICT RULES
1. Use ONLY the facts in the EVIDENCE block. Do not add numbers, tests, materials, causes or physics that are not there.
2. Every number you write must appear in the EVIDENCE (you may write a fraction like 0.98 as 98 %). Never round differently.
3. Cite the evidence after each fact with its label: [T1], [E1], [D2]. Never cite a label that does not exist.
4. The FIRST line must be exactly one of: [[DECISION:ANSWER]] / [[DECISION:PARTIAL]] / [[DECISION:ABSTAIN]]
   - ANSWER: the evidence answers the central question.
   - PARTIAL: only part of it, or only related evidence (e.g. NASA observations without a model).
   - ABSTAIN: the central question cannot be answered with this evidence (e.g. the conditions were never tested).
   Use the SUGGESTED DECISION unless the evidence clearly says otherwise.
5. If something was never tested or is outside the data, say so plainly and explain why, using the evidence.
6. Mention the assumptions and limits that the evidence lists (assumed values, moderate confidence, effects that cannot be separated).
7. Write in {lang_name}. Be clear for a non-expert: short paragraphs, no jargon without a one-line explanation. At most ~170 words.
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
             'top_contributions_log_odds': dict(sorted(p.contributions.items(), key=lambda kv: -abs(kv[1]))[:3])}
        items.append(f'[T{k}] model prediction: {dumps(d)}')
    for label, req, resp in g.counts:
        k += 1; labels.add(f'T{k}')
        items.append(f'[T{k}] NASA test table query {dumps({"about": label, "filters": {x: v for x, v in req.items() if x not in ("limit", "group_by")}})}: '
                     f'{resp.n_total} tests; outcome counts {dumps(resp.counts[:8])}')
    for j, e in enumerate(g.evidence[:5], 1):
        labels.add(f'E{j}')
        items.append(f'[E{j}] closest real NASA test {e.test_id} ({e.psi_id}): outcome {e.outcome}; source {e.source[:160]}')
    for j, d in enumerate(g.passages[:6], 1):
        labels.add(f'D{j}')
        page = f" p.{d['page_from']}" if d.get('page_from') else ''
        items.append(f"[D{j}] NASA report '{d.get('doc_title') or d['file_name']}'{page}: \"{d['content'][:650]}\"")
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
