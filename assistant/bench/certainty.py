"""Over-hedging check (LC, 2026-11-14: "it always says partial even when something can be said with certainty").

For questions whose answer is certain from the data/models/reports, measures:
  * label hedging: decision is 'partial' or 'abstain' instead of 'answer';
  * text hedging: hedge phrases in the text of a certain answer (e.g. "no podemos afirmar", "sin embargo, no hay datos").
For questions that are NOT answerable, checks that the system does not claim a confident 'answer'.
Usage: python -m assistant.bench.certainty [--api]   (--api: through MG's live /ask, i.e. the full deployed stack)
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
HEDGES = [r'no (?:se )?pued\w+ (?:afirmar|asegurar|concluir|determinar)', r'no podemos', r'no es posible (?:afirmar|saber|determinar)',
          r'sin embargo,? (?:la evidencia|los datos|no)', r'no hay (?:datos|evidencia|informaci[oó]n) (?:suficiente|directa|espec)',
          r'no (?:proporciona|ofrece|detalla|especifica)', r'cannot (?:say|confirm|conclude|determine)', r'not enough (?:data|evidence)',
          r'however,? (?:the evidence|the data|no)', r'no (?:clear|direct) evidence', r'limitaci[oó]n', r'con cautela|with caution']


def hedges(text: str) -> list[str]:
    t = text.lower()
    return [h for h in HEDGES if re.search(h, t)]


def ask_api(q: str) -> dict:
    import httpx
    return httpx.post('https://api-production-9c5980b0.up.railway.app/ask', json={'text': q}, timeout=150).json()


def ask_local(q: str) -> dict:
    from flame.contracts import AskRequest
    from assistant.generate import make_drafter
    from assistant.llm import get_llm
    from assistant.pipeline import answer
    from assistant.tools import get_tools
    global _LLM
    if '_LLM' not in globals():
        _LLM = get_llm()
    return answer(AskRequest(text=q), tools=get_tools(), drafter=make_drafter(_LLM) if _LLM else None, llm=_LLM).model_dump()


def main(api: bool) -> None:
    from assistant.envfile import load_env
    load_env()
    items = json.loads((HERE / 'certainty.json').read_text(encoding='utf-8'))
    rows = []
    for it in items:
        d = ask_api(it['q']) if api else ask_local(it['q'])
        h = hedges(d.get('text') or '')
        rows.append({'id': it['id'], 'certain': it['certain'], 'decision': d.get('decision'), 'intent': d.get('intent'),
                     'version': d.get('assistant_version'), 'hedges': h, 'text': (d.get('text') or '')[:500]})
        time.sleep(20)    # free-tier LLM budget
    cert = [r for r in rows if r['certain']]
    unc = [r for r in rows if not r['certain']]
    summary = {
        'certain_n': len(cert),
        'certain_labelled_answer': round(sum(r['decision'] == 'answer' for r in cert) / len(cert), 2),
        'certain_labelled_partial_or_abstain': round(sum(r['decision'] != 'answer' for r in cert) / len(cert), 2),
        'certain_text_with_hedges': round(sum(bool(r['hedges']) for r in cert) / len(cert), 2),
        'llm_drafted': round(sum(str(r['version']).endswith(':llm') for r in rows) / len(rows), 2),   # if low, hedges are not measured
        'uncertain_n': len(unc),
        'uncertain_claimed_answer': round(sum(r['decision'] == 'answer' for r in unc) / len(unc), 2),
    }
    out = {'summary': summary, 'rows': rows}
    (HERE / ('certainty_report_api.json' if api else 'certainty_report.json')).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(summary, indent=1))
    for r in rows:
        flag = ('OVER-HEDGED' if r['certain'] and (r['decision'] != 'answer' or r['hedges']) else
                'OVER-CLAIMED' if not r['certain'] and r['decision'] == 'answer' else 'ok')
        print(f"{r['id']} {flag:12s} {r['decision']:8s} {r['intent']} hedges={len(r['hedges'])}")


if __name__ == '__main__':
    main(api='--api' in sys.argv)
