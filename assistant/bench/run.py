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

from assistant.generate import make_drafter
from assistant.llm import get_llm
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


def run(http: bool = False, llm: bool = False, pace: float = 15.0) -> dict:
    tools = get_tools() if http else LocalTools()
    llm_client = get_llm() if llm else None
    drafter = make_drafter(llm_client) if llm_client else None
    items = json.loads((HERE / 'questions.json').read_text(encoding='utf-8'))
    rows = []
    for it in items:
        t0 = time.perf_counter()
        a = answer(AskRequest(text=it['q']), tools=tools, drafter=drafter, llm=llm_client)
        ms = int((time.perf_counter() - t0) * 1000)
        got = a.understood.model_dump(exclude_none=True)
        invented = sorted(numbers(a.text) - allowed_numbers(it['q'], a))
        r = {'id': it['id'], 'type': it['type'], 'intent_ok': a.intent == it['intent'],
             'tools_ok': all(t in a.tools_used for t in it['tools']) and (bool(it['tools']) or not a.tools_used),
             'decision_ok': a.decision == it['decision'], 'decision': a.decision,
             'expect_ok': all((abs(got.get(k, -1) - v) < 1e-6) if isinstance(v, float) else got.get(k) == v for k, v in it.get('expect', {}).items()),
             'text_ok': all(x in a.text for x in it.get('expect_text', [])),
             'doc_ok': ('expect_accession' not in it) or any(c.psi_id == it['expect_accession'] for c in a.citations[:5]),
             'invented_numbers': invented, 'ms': ms, 'version': a.assistant_version, 'verification': a.verification, 'text': a.text}
        rows.append(r)
        if llm:
            time.sleep(pace)       # free tier: ~8000 tokens/min -> space the questions out
    by = defaultdict(list)
    for r in rows:
        by[r['type']].append(r)
    summary = {}
    for k, rs in list(by.items()) + [('ALL', rows)]:
        summary[k] = {m: round(sum(r[m] for r in rs) / len(rs), 2) for m in ('intent_ok', 'tools_ok', 'decision_ok', 'expect_ok', 'text_ok', 'doc_ok')}
        summary[k].update(llm_used=round(sum(r['version'].endswith(':llm') for r in rs) / len(rs), 2))
        summary[k].update(n=len(rs), invented_numbers=sum(len(r['invented_numbers']) for r in rs), median_ms=sorted(r['ms'] for r in rs)[len(rs) // 2])
    report = {'summary': summary, 'failures': [r for r in rows if not all(r[m] for m in ('intent_ok', 'tools_ok', 'decision_ok', 'expect_ok', 'text_ok', 'doc_ok')) or r['invented_numbers']]}
    (HERE / 'report.json').write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding='utf-8')
    return report


if __name__ == '__main__':
    from assistant.envfile import load_env
    load_env()      # API_BASE_URL / tokens / LLM settings from .env (local runs)
    rep = run(http='--http' in sys.argv, llm='--llm' in sys.argv)
    for k, v in rep['summary'].items():
        print(f'{k:12s} {v}')
    for f in rep['failures']:
        print('FAIL', {k: v for k, v in f.items() if k != 'text'})
