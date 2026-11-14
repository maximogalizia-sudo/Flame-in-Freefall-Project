"""MVP retrieval helpers and LLM durability (NOTAS 2026-11-14)."""
import httpx

from assistant.llm import LLM
from assistant.retrieve import glossary_translate, is_table_like, select_passages, translate_query


def test_retired_model_falls_back_to_next():
    calls = []
    def handler(request: httpx.Request):
        import json
        m = json.loads(request.content)['model']
        calls.append(m)
        if m == 'old-model':
            return httpx.Response(404, json={'error': {'message': 'The model `old-model` does not exist'}})
        return httpx.Response(200, json={'choices': [{'message': {'content': '<think>x</think>ok'}}]})
    llm = LLM('https://x/v1', 'k', 'old-model,new-model', transport=httpx.MockTransport(handler))
    assert llm.chat('s', 'u') == 'ok' and llm.active_model == 'new-model'
    assert llm.chat('s', 'u') == 'ok' and calls == ['old-model', 'new-model', 'new-model']


def test_all_models_gone_returns_none():
    llm = LLM('https://x/v1', 'k', 'a,b', transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    assert llm.chat('s', 'u') is None


def test_translate_query():
    assert translate_query('Why did droplets disrupt?', 'en') == ('Why did droplets disrupt?', 'as_is')
    q, how = translate_query('¿Por qué se rompían las gotas en FLEX?', 'es', None)
    assert how == 'glossary' and 'droplets' in q and 'disruption' in q
    fake = LLM('https://x/v1', 'k', 'm', transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={'choices': [{'message': {'content': 'why FLEX droplets disrupted'}}]})))
    q2, how2 = translate_query('¿Por qué se rompían las gotas en FLEX?', 'es', fake)
    assert how2 == 'llm+glossary' and q2.startswith('why FLEX droplets disrupted') and 'disruption' in q2


def test_glossary_compound_terms():
    assert 'cool flame' in glossary_translate('¿Qué es una llama fría?')


def test_table_filter():
    table = '193F001 0.15 0.85 0 0 4.66 4.59 0.41 1.6 Extinction\n193F002 0.15 0.85 0 0 3.10 2.20 0.55 2.1 Extinction\n' * 3
    text = 'The disruption was attributed to a coating on the needles that dissolved in the fuel and nucleated bubbles.'
    assert is_table_like(table) and not is_table_like(text)
    cands = [{'content': table, 'file_name': 'a', 'page_from': 1}, {'content': text, 'file_name': 'a', 'page_from': 2},
             {'content': text + ' again', 'file_name': 'a', 'page_from': 2}]
    assert [c['page_from'] for c in select_passages(cands)] == [2]
