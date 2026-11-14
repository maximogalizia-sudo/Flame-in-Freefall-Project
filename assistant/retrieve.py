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
                return q, 'llm'
    return glossary_translate(text), 'glossary'


def is_table_like(content: str) -> bool:
    words = content.split()
    if not words:
        return True
    num_share = sum(bool(re.fullmatch(r'[~<>±–-]?\d+(?:[.,]\d+)?%?', w)) for w in words) / len(words)
    lines = [l for l in content.splitlines() if l.strip()]
    many_nums = sum(len(re.findall(r'\d+(?:[.,]\d+)?', l)) >= 4 for l in lines) / max(len(lines), 1)
    return num_share > 0.35 or (len(lines) >= 4 and many_nums >= 0.5)


def select_passages(candidates: list[dict], k: int = 6) -> list[dict]:
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
