"""Measure analytic questions through MG's live /ask (full deployed stack): LC's 6 questions + held-out v1 and v2.

For each question: intent (vs expected kind), decision (answerable -> 'answer'; not answerable -> not 'answer'),
whether the LLM drafted the text, and the text. Usage: python -m assistant.bench.analytic_eval
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
API = 'https://api-production-9c5980b0.up.railway.app/ask'
LC6 = [('LC1', '¿En qué rangos de oxígeno no se prende fuego bajo ninguna condición?', 'profile'),
       ('LC2', '¿Qué pasa con una llama de propano con 40 % de oxígeno a 2 bar y un caudal de 1,5 mg/s? ¿Se prende y cuánto tarda en apagarse?', 'predict'),
       ('LC3', '¿Cuál es el peligro de la alta presión respecto a este fenómeno?', 'explain_variable'),
       ('LC4', 'Mencioname 3 experimentos que se deberían realizar en el futuro para enriquecer los datos y los análisis', 'gaps'),
       ('LC5', 'Dame las 5 configuraciones de variables que más tienden a incendiarse', 'rank'),
       ('LC6', '¿Cuáles son las configuraciones de variables que más tardan en apagarse?', 'rank')]


def main() -> None:
    sets = {'lc6': [{'id': i, 'q': q, 'kind': k, 'answerable': True} for i, q, k in LC6]}
    for name in ('analytic_heldout', 'analytic_heldout_v2'):
        sets[name] = json.loads((HERE / f'{name}.json').read_text(encoding='utf-8'))['items']
    report = {}
    for name, items in sets.items():
        rows = []
        for it in items:
            d = httpx.post(API, json={'text': it['q']}, timeout=180).json()
            ok_dec = (d.get('decision') in ('answer', 'partial')) if it['answerable'] else (d.get('decision') != 'answer')
            if it['answerable'] and it['kind'] != 'predict':
                ok_dec = d.get('decision') == 'answer'
            rows.append({'id': it['id'], 'kind': it['kind'], 'intent': d.get('intent'), 'intent_ok': d.get('intent') == it['kind'],
                         'decision': d.get('decision'), 'decision_ok': ok_dec, 'llm': str(d.get('assistant_version')).endswith(':llm'),
                         'tools': d.get('tools_used'), 'text': d.get('text')})
            time.sleep(20)
        n = len(rows)
        report[name] = {'summary': {'n': n, 'intent_ok': round(sum(r['intent_ok'] for r in rows) / n, 2),
                                    'decision_ok': round(sum(r['decision_ok'] for r in rows) / n, 2),
                                    'llm_drafted': round(sum(r['llm'] for r in rows) / n, 2)}, 'rows': rows}
        print(name, report[name]['summary'])
        for r in rows:
            if not (r['intent_ok'] and r['decision_ok']):
                print('  FAIL', r['id'], r['kind'], '->', r['intent'], r['decision'], r['tools'])
    (HERE / 'analytic_report_api.json').write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
