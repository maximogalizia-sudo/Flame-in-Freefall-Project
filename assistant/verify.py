"""Verifier (criterion 1): an LLM draft is accepted only if it does not add anything the tools did not give.

Checks: decision mark present and coherent with the evidence; every number in the text appears in the question
or in a tool output (also accepted as % of a fraction, with rounding, or as the sum, difference or percentage of two
counts of the tools, recomputed here: DT-028); every citation [Tn]/[Dn] exists; no fuel
or material is named that is not in the question or the tool outputs. Any problem -> the template answer is used.
"""
from __future__ import annotations

import json
import re

from .understand import SUBSTANCES, normalize

NUMRE = re.compile(r'(?<![\w.])\d+(?:[.,]\d+)?')
DECISION = re.compile(r'^\s*\[\[DECISION:(ANSWER|PARTIAL|ABSTAIN)\]\]\s*', re.IGNORECASE)
CITE = re.compile(r'\[(T|D|E)(\d+)\]')


def _norm_num(n: str) -> str:
    n = n.replace(',', '.')
    return n.rstrip('0').rstrip('.') if '.' in n else n


def numbers(text: str) -> set[str]:
    return {_norm_num(n) for n in NUMRE.findall(text)}


def allowed_numbers(*sources: str) -> set[str]:
    blob = ' '.join(sources)
    out = numbers(blob)
    for v in re.findall(r'\d+(?:\.\d+)?', blob):
        x = float(v)
        for y in (x, x * 100):
            out |= {_norm_num(f'{y:.0f}'), _norm_num(f'{y:.1f}'), _norm_num(f'{y:.2f}'), _norm_num(f'{y:g}')}
    return out


COUNT_RE = re.compile(r'(\d+)\s+(?:tests|pruebas|runs|ensayos|llamas|flames|droplets|gotas)\b|"n(?:_total|_tests)?"\s*:\s*(\d+)|'
                      r'\bn(?:_tests)?\s*=\s*(\d+)|"count"\s*:\s*(\d+)|\b(\d+)\s+(?:of|de)\s+(\d+)\b')


def derived(n: str, blob: str) -> bool:
    """DT-028, tightened by DT-035 (night inspection C4): a number the LLM computed from two COUNTS of the tools (sum,
    difference or percentage), e.g. "272 lit" from "333 tests" and "n": 61. Only labelled counts are combined (number of
    tests, "n": .., n=.., "x of y"), never any integer of the evidence (years, pages, test ids), which had let most
    invented numbers pass. The verifier recomputes it; anything else is rejected."""
    try:
        x = float(n)
    except ValueError:
        return False
    counts = sorted({int(v) for m in COUNT_RE.finditer(blob) for v in m.groups() if v and 0 < int(v) < 100000})[:200]
    for a in counts:
        for b in counts:
            cands = [a + b, a - b] + ([a / b * 100] if a <= b else [])
            if any(abs(c - x) < 1e-9 or abs(round(c, 1) - x) < 1e-9 or abs(round(c, 2) - x) < 1e-9 or abs(round(c) - x) < 1e-9
                   for c in cands if c > 0):
                return True
    return False


def parse_decision(raw: str) -> tuple[str | None, str]:
    m = DECISION.match(raw or '')
    if not m:
        return None, (raw or '').strip()
    return m.group(1).lower(), raw[m.end():].strip()


MODEL_CLAIM = re.compile(
    r"(?:aprendi\w*|aprende\w*) (?:por |de )?(?:el |los )?modelos?|tendencia aprendida|segun (?:el |los )?modelos?|"
    r"(?:el|los) modelos? (?:predic\w*|indica\w*|muestra\w*|estima\w*|aprend\w*)|"
    r"sistema de (?:ml|aprendizaje automatico) (?:aprendi\w*|muestra\w*|predic\w*|indica\w*)|"
    r"models? learned|learned by the models?|according to the models?|the models? (?:predicts?|shows?|indicates?|estimates?)|"
    r"(?:ml|machine.learning) (?:models?|system) (?:learned|shows?|predicts?|indicates?)")


def verify(draft: str, decision: str | None, question: str, evidence_block: str, citable: set[str],
           template_decision: str, model_evidence: bool = True) -> list[str]:
    problems = []
    if not model_evidence and MODEL_CLAIM.search(normalize(draft)):
        # DT-029: report passages or observed NASA data presented as something the ML models learned
        problems.append('attributes to the ML models something that comes from the reports or the observed data')
    if decision is None:
        problems.append('missing [[DECISION:...]] mark')
    elif template_decision == 'abstain' and decision == 'answer':
        problems.append('says ANSWER but the tools gave no answer to the central question')
    elif template_decision == 'answer' and decision == 'abstain':
        problems.append('abstains although the tools answered the question')
    invented = sorted(numbers(draft) - allowed_numbers(question, evidence_block))
    invented = [n for n in invented if not derived(n, question + ' ' + evidence_block)]
    if invented:
        problems.append(f'numbers not found in the tool outputs: {invented[:6]}')
    bad_cites = sorted({f'{a}{b}' for a, b in CITE.findall(draft)} - citable)
    if bad_cites:
        problems.append(f'citations that do not exist: {bad_cites}')
    src = normalize(question + ' ' + evidence_block)
    named = {w for w in SUBSTANCES if re.search(rf'(?<![\w-]){re.escape(w)}\b', normalize(draft))}
    foreign = sorted(w for w in named if not re.search(rf'(?<![\w-]){re.escape(w)}\b', src)
                     and not any(re.search(rf'\b{re.escape(alias)}\b', src) for alias, sub in SUBSTANCES.items() if sub == SUBSTANCES[w]))
    if foreign:
        problems.append(f'names substances not in the question or tools: {foreign}')
    if not draft.strip():
        problems.append('empty text')
    return problems


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
