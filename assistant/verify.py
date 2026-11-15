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


def derived(n: str, blob: str) -> bool:
    """DT-028: a number the LLM computed from two COUNTS of the tools (sum, difference or percentage), e.g. "272 lit"
    from 333 tests and 61 that did not light, or "80.25 %" from 107 of 133. The verifier recomputes it; anything else
    is still rejected. Only whole numbers from the evidence are combined (counts), to keep coincidences unlikely."""
    try:
        x = float(n)
    except ValueError:
        return False
    counts = sorted({int(v) for v in re.findall(r'(?<![\d.])\d+(?![\d.])', blob) if 0 < int(v) < 100000})[:300]
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


def verify(draft: str, decision: str | None, question: str, evidence_block: str, citable: set[str],
           template_decision: str) -> list[str]:
    problems = []
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
