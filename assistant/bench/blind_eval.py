"""Blind test against the LIVE system (web -> MG's API /ask -> assistant -> Groq).

Questions: bench/blind_agent_v1.json, written by a subagent with no context of the project; accepted kinds and
decisions labeled before running (DT-020). Questions are sent spaced out (default 35 s) so the provider's
tokens-per-minute limit is not hit. Full answers are saved for reading by hand: bench/blind_report.json.

    .venv/Scripts/python -m assistant.bench.blind_eval [--api URL] [--pause 35] [--only B01,B02]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
API = 'https://api-production-9c5980b0.up.railway.app'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--api', default=API)
    ap.add_argument('--pause', type=float, default=35.0)
    ap.add_argument('--only', default='')
    ap.add_argument('--out', default='blind_report.json')
    a = ap.parse_args()
    items = json.load(open(HERE / 'blind_agent_v1.json', encoding='utf-8'))['items']
    if a.only:
        keep = set(a.only.split(','))
        items = [x for x in items if x['id'] in keep]
    rows = []
    for k, it in enumerate(items):
        t0 = time.perf_counter()
        try:
            r = httpx.post(f'{a.api}/ask', json={'text': it['q']}, timeout=110).json()
            row = {**it, 'intent': r.get('intent'), 'decision': r.get('decision'), 'tools': r.get('tools_used'),
                   'verification': r.get('verification'), 'version': r.get('assistant_version'),
                   'n_citations': len(r.get('citations') or []), 'n_predictions': len(r.get('predictions') or []),
                   'text': r.get('text'), 'trace_id': r.get('trace_id')}
        except Exception as e:  # noqa: BLE001
            row = {**it, 'error': f'{type(e).__name__}: {e}'}
        row['s'] = round(time.perf_counter() - t0, 1)
        row['kind_ok'] = row.get('intent') in it['kinds']
        row['decision_ok'] = row.get('decision') in it['decisions']
        row['llm_drafted'] = 'template' not in str(row.get('version'))
        rows.append(row)
        print(f"{it['id']} {row.get('intent')!s:16} {row.get('decision')!s:8} kind={'ok' if row['kind_ok'] else 'NO'} "
              f"dec={'ok' if row['decision_ok'] else 'NO'} llm={'y' if row['llm_drafted'] else 'n'} {row['s']}s", flush=True)
        (HERE / a.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
        if k < len(items) - 1:
            time.sleep(a.pause)
    n = len(rows)
    print(f"\nkind {sum(r['kind_ok'] for r in rows)}/{n} · decision {sum(r['decision_ok'] for r in rows)}/{n} · "
          f"drafted by LLM {sum(r['llm_drafted'] for r in rows)}/{n} · errors {sum('error' in r for r in rows)}")


if __name__ == '__main__':
    main()
