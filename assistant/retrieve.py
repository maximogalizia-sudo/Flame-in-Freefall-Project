"""Retrieval helpers for the NASA-report search (MVP, NOTAS 2026-11-14: small English embedding model, no reranker).

* translate_query: the index and the embedding model are English-only, so a Spanish question is turned into an
  English search query (LLM, one short call; glossary fallback without LLM). The user's question is unchanged.
* is_table_like / select_passages: table rows add noise (their numbers are answered by /tests/search), so they are
  dropped; then at most one passage per page, top 6.
"""
from __future__ import annotations

import re

from .understand import normalize

GLOSSARY = {   # Spanish -> English, used only when the LLM is not available
    'llama fria': 'cool flame', 'llamas frias': 'cool flames', 'gota': 'droplet', 'gotas': 'droplets',
    'llama': 'flame', 'llamas': 'flames', 'se apaga': 'extinction', 'apagan': 'extinction', 'apagar': 'extinguish',
    'extincion': 'extinction', 'se rompian': 'disruption', 'se rompe': 'disruption', 'rompian': 'disruption',
    'rotura': 'disruption', 'aguja': 'needle', 'agujas': 'needles', 'oxigeno': 'oxygen', 'presion': 'pressure',
    'helio': 'helium', 'dioxido de carbono': 'carbon dioxide', 'combustible': 'fuel', 'tela': 'fabric',
    'algodon': 'cotton', 'metanol': 'methanol', 'heptano': 'heptane', 'propano': 'propane', 'butano': 'butane',
    'etano': 'ethane', 'microgravedad': 'microgravity', 'quemador': 'burner', 'hollin': 'soot', 'radiacion': 'radiation',
    'propagacion': 'flame spread', 'ignicion': 'ignition', 'encender': 'ignite', 'flujo': 'flow', 'aire': 'air',
    'por que': 'why', 'que es': 'what is', 'como': 'how', 'resultados': 'results', 'prueba': 'test', 'pruebas': 'tests',
    'experimento': 'experiment', 'contaminado': 'contaminated', 'recubrimiento': 'coating', 'materiales': 'materials',
}
STOP_ES = r'\b(?:el|la|los|las|un|una|de|del|en|y|o|que|se|por|para|con|sin|al|lo|su|sus|es|son|segun|nasa)\b'


def glossary_translate(text: str) -> str:
    t = normalize(text)
    for es, en in sorted(GLOSSARY.items(), key=lambda kv: -len(kv[0])):
        t = re.sub(rf'\b{re.escape(es)}\b', en, t)
    t = re.sub(STOP_ES, ' ', t)
    return re.sub(r'[¿?¡!.,;:]+', ' ', re.sub(r'\s+', ' ', t)).strip()


def translate_query(text: str, lang: str, llm=None) -> tuple[str, str]:
    """Returns (english_query, method)."""
    if lang != 'es':
        return text, 'as_is'
    if llm is not None:
        out = llm.chat('You turn a Spanish question into a short English search query for NASA combustion reports. '
                       'Output ONLY the English query, max 15 words, no quotes.', text, max_tokens=40, temperature=0.0)
        if out:
            q = out.strip().strip('"').splitlines()[0][:200]
            if q and re.fullmatch(r"[A-Za-z0-9 ,.'()\-/%?₂+]+", q):
                # add the glossary's NASA terms the LLM may have paraphrased ('breakup' vs NASA's 'disruption')
                extra = [w for w in glossary_translate(text).split() if w.lower() not in q.lower() and w not in ('why', 'how', 'what')]
                return (q + ' ' + ' '.join(extra)).strip(), 'llm+glossary'
    return glossary_translate(text), 'glossary'


def is_table_like(content: str) -> bool:
    words = content.split()
    if not words:
        return True
    num_share = sum(bool(re.fullmatch(r'[~<>±–-]?\d+(?:[.,]\d+)?%?', w)) for w in words) / len(words)
    lines = [l for l in content.splitlines() if l.strip()]
    many_nums = sum(len(re.findall(r'\d+(?:[.,]\d+)?', l)) >= 4 for l in lines) / max(len(lines), 1)
    return num_share > 0.35 or (len(lines) >= 4 and many_nums >= 0.5)


CAPTION = re.compile(r'Figure\s+\d+\.?\s*[—−–-]+\s*Tests?\s+[A-Z][A-Za-z-]*\s*[−–-]?\s*\d+', re.I)


def is_caption(content: str) -> bool:
    """Per-test figure caption (e.g. 'Figure 61.—Test FLEX-057. Fiber-supported ...'): describes ONE test."""
    return bool(CAPTION.search(content[:250]))


STOP_EN = set('the a an of in on at to for and or is are was were be been by with from that this these those it its as '
              'what why how when which who does did do can could would should about into than then there their they'
              ' flame flames nasa test tests experiment experiments'.split())


def _stems(text: str) -> set[str]:
    return {w[:6] for w in re.findall(r'[a-z]{3,}', text.lower()) if w not in STOP_EN}


def keyword_rerank(query: str, candidates: list[dict]) -> list[dict]:
    """Light reranker (no model, MVP): share of the query's content words (6-letter stems, so 'disrupt' matches
    'disruptive') found in each passage, then the original fusion order. Replaces the cross-encoder we dropped."""
    q = _stems(query)
    if not q:
        return candidates
    def score(item):
        idx, c = item
        return (-len(q & _stems(c.get('content', ''))) / len(q), idx)
    return [c for _, c in sorted(enumerate(candidates), key=score)]


def select_passages(candidates: list[dict], k: int = 6, prefer_narrative: bool = True, query: str | None = None) -> list[dict]:
    """Drop table rows; rerank by query keywords; for explanatory questions put narrative text (sections,
    conclusions) before per-test figure captions. Captions still come after, as examples."""
    if query:
        candidates = keyword_rerank(query, candidates)
    if prefer_narrative:
        candidates = ([c for c in candidates if not is_caption(c.get('content', ''))]
                      + [c for c in candidates if is_caption(c.get('content', ''))])
    out, seen = [], set()
    for c in candidates:
        if is_table_like(c.get('content', '')):
            continue
        key = (c.get('file_name'), c.get('page_from'))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) == k:
            break
    return out
