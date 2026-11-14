"""Evaluation bank (L6). Usage: python -m assistant.bench.run [--http]

Metrics per question type: intent correct, expected tools called, decision correct (incl. abstention),
expected values understood, expected text present, invented numbers (must be 0: every number in the answer text
must appear in a tool output or in the question), latency. Writes assistant/bench/report.json.
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

from flame.contracts import AskRequest

from assistant.pipeline import answer
from assistant.tools import LocalTools, get_tools

HERE = Path(__file__).parent
NUMRE = re.compile(r'(?<![\w.])\d+(?:[.,]\d+)?')


def numbers(s: str) -> set[str]:
    return {n.replace(',', '.').rstrip('0').rstrip('.') if '.' in n.replace(',', '.') else n for n in NUMRE.findall(s)}


def allowed_numbers(q: str, a) -> set[str]:
    """Numbers the answer may contain: from the question and from every tool output (also as %)."""
    blob = q + ' ' + json.dumps(a.model_dump(), ensure_ascii=False)
    out = numbers(blob)
    for v in re.findall(r'\d+\.\d+', blob):
        x = float(v)
        for y in (x, x * 100):
            out |= {f'{y:.0f}', f'{y:.1f}'.rstrip('0').rstrip('.'), f'{y:.2f}'.rstrip('0').rstrip('.'), f'{y:g}'}
    return out


def run(http: bool = False) -> dict:
    tools = get_tools() if http else LocalTools()
    items = json.loads((HERE / 'questions.json').read_text(encoding='utf-8'))
    rows = []
    for it in items:
        t0 = time.perf_counter()
        a = answer(AskRequest(text=it['q']), tools=tools)
        ms = int((time.perf_counter() - t0) * 1000)
        got = a.understood.model_dump(exclude_none=True)
        invented = sorted(numbers(a.text) - allowed_numbers(it['q'], a))
        r = {'id': it['id'], 'type': it['type'], 'intent_ok': a.intent == it['intent'],
             'tools_ok': all(t in a.tools_used for t in it['tools']) and (bool(it['tools']) or not a.tools_used),
             'decision_ok': a.decision == it['decision'], 'decision': a.decision,
             'expect_ok': all((abs(got.get(k, -1) - v) < 1e-6) if isinstance(v, float) else got.get(k) == v for k, v in it.get('expect', {}).items()),
             'text_ok': all(x in a.text for x in it.get('expect_text', [])),
             'invented_numbers': invented, 'ms': ms, 'version': a.assistant_version}
        rows.append(r)
    by = defaultdict(list)
    for r in rows:
        by[r['type']].append(r)
    summary = {}
    for k, rs in list(by.items()) + [('ALL', rows)]:
        summary[k] = {m: round(sum(r[m] for r in rs) / len(rs), 2) for m in ('intent_ok', 'tools_ok', 'decision_ok', 'expect_ok', 'text_ok')}
        summary[k].update(n=len(rs), invented_numbers=sum(len(r['invented_numbers']) for r in rs), median_ms=sorted(r['ms'] for r in rs)[len(rs) // 2])
    report = {'summary': summary, 'failures': [r for r in rows if not all(r[m] for m in ('intent_ok', 'tools_ok', 'decision_ok', 'expect_ok', 'text_ok')) or r['invented_numbers']]}
    (HERE / 'report.json').write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding='utf-8')
    return report


if __name__ == '__main__':
    rep = run(http='--http' in sys.argv)
    for k, v in rep['summary'].items():
        print(f'{k:12s} {v}')
    for f in rep['failures']:
        print('FAIL', f)
