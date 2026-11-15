"""Summary of the blind runs (DT-023): kind of question read right, decision right, drafted by the LLM, per run.

    .venv/Scripts/python -m assistant.bench.blind_summary
Prints a markdown table plus the questions that changed, for docs/MEDICION_FINAL_2026-11-15.md.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
RUNS = [('v1, sistema anterior (línea de base)', 'blind_report_before.json'),
        ('v1, pasada 2 (clasificador + buscar antes de "no hay datos")', 'blind_report.json'),
        ('**v2, preguntas nuevas, sistema final**', 'blind_report_v2.json')]


def load(name):
    p = HERE / name
    return json.load(open(p, encoding='utf-8')) if p.exists() else None


def main():
    print('| Corrida | n | Tipo de pregunta bien | Decisión bien | Redactada por el LLM | Errores |')
    print('|---|---|---|---|---|---|')
    for label, f in RUNS:
        rows = load(f)
        if not rows:
            continue
        n = len(rows)
        k, d, l, e = (sum(r.get(x, False) for r in rows) for x in ('kind_ok', 'decision_ok', 'llm_drafted', 'error'))
        e = sum('error' in r for r in rows)
        print(f'| {label} | {n} | {k}/{n} ({k / n:.0%}) | {d}/{n} ({d / n:.0%}) | {l}/{n} | {e} |')
    rows = load('blind_report_v2.json') or []
    bad = [r for r in rows if not r.get('decision_ok') or not r.get('kind_ok')]
    if bad:
        print('\nPreguntas v2 con algo mal (tipo o decisión):')
        for r in bad:
            print(f"- {r['id']} ({r.get('intent')} / {r.get('decision')}; esperado {r['kinds']} / {r['decisions']}): {r['q']}")
    rej = [r for r in rows if r.get('verification')]
    if rej:
        print('\nRespuestas v2 en las que el verificador rechazó el texto del LLM (se usó la plantilla):')
        for r in rej:
            print(f"- {r['id']}: {'; '.join(r['verification'])[:200]}")


if __name__ == '__main__':
    main()
