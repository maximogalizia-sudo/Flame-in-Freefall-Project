"""Which reading of the question should win: keyword rules, the LLM, or the LLM only when the rules are unsure?

Measured on every question that already has a hand label for its kind (intent):
  bench/questions.json (50), analytic_heldout.json (20), analytic_heldout_v2.json (12), Lautaro's 6.
The LLM is called once per question (spaced, to respect the provider's tokens-per-minute limit) and the three
policies of assistant.intent_llm.choose are scored. Output: assistant/bench/intent_report.json + a printed table.

    .venv/Scripts/python -m assistant.bench.intent_eval
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from assistant.bench.analytic_eval import LC6
from assistant.envfile import load_env
from assistant.intent_llm import choose, classify_llm
from assistant.llm import get_llm
from assistant.understand import matched_intents, normalize, understand

HERE = Path(__file__).parent
POLICIES = ('rules', 'llm', 'unsure', 'guarded')


def labeled() -> list[dict]:
    out = []
    for x in json.load(open(HERE / 'questions.json', encoding='utf-8')):
        out.append({'set': 'bench50', 'id': x['id'], 'q': x['q'], 'gold': x['intent']})
    for f, name in (('analytic_heldout.json', 'heldout_v1'), ('analytic_heldout_v2.json', 'heldout_v2')):
        d = json.load(open(HERE / f, encoding='utf-8'))
        for x in (d['items'] if isinstance(d, dict) else d):
            out.append({'set': name, 'id': x['id'], 'q': x['q'], 'gold': x['kind']})
    for i, q, k in LC6:
        out.append({'set': 'lc6', 'id': i, 'q': q, 'gold': k})
    return out


def main(pause_s: float = 2.5):
    load_env()
    llm = get_llm()
    rows = []
    for it in labeled():
        u = understand(it['q'])
        m = matched_intents(normalize(it['q']))
        r = classify_llm(it['q'], llm)
        rows.append({**it, 'rules': u.intent, 'matched': m, 'llm': r['intent'] if r else None,
                     **{f'p_{p}': choose(u.intent, m, r, p, u) for p in POLICIES}})
        time.sleep(pause_s)
    sets = sorted({r['set'] for r in rows}) + ['ALL']
    print(f"{'set':12} {'n':>3}  " + '  '.join(f'{p:>7}' for p in POLICIES))
    summary = {}
    for s in sets:
        rs = [r for r in rows if s == 'ALL' or r['set'] == s]
        acc = {p: sum(r[f'p_{p}'] == r['gold'] for r in rs) for p in POLICIES}
        summary[s] = {'n': len(rs), **acc}
        print(f"{s:12} {len(rs):>3}  " + '  '.join(f'{acc[p]:>7}' for p in POLICIES))
    print('\nGuarded policy wrong:')
    for r in rows:
        if r['p_guarded'] != r['gold']:
            print(f"  {r['id']:6} gold={r['gold']:16} guarded={r['p_guarded']:16} llm={r['llm']} rules={r['rules']} | {r['q'][:70]}")
    print('Rules wrong, LLM right:')
    for r in rows:
        if r['rules'] != r['gold'] and r['llm'] == r['gold']:
            print(f"  {r['id']:6} gold={r['gold']:16} rules={r['rules']:16} matched={r['matched']} | {r['q'][:70]}")
    (HERE / 'intent_report.json').write_text(json.dumps({'summary': summary, 'rows': rows}, ensure_ascii=False, indent=1),
                                             encoding='utf-8')


if __name__ == '__main__':
    main()
