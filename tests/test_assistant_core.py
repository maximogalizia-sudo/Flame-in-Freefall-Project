"""Assistant step 1-2: understanding, planning, tools (local + HTTP mock), template answers, HTTP service."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from flame.contracts import AskRequest, ChatTurn, Conditions, TestsSearchRequest
from assistant.pipeline import answer
from assistant.plan import make_plan
from assistant.tools import HttpTools, LocalTools
from assistant.understand import understand


@pytest.fixture(autouse=True)
def _traces(tmp_path, monkeypatch):
    monkeypatch.setenv('ASSISTANT_TRACE_PATH', str(tmp_path / 't.jsonl'))
    from assistant.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize('q, intent, cond', [
    ('¿Se apaga una gota de heptano con 30 % de oxígeno a 0,7 atm?', 'predict', {'fuel': 'heptane', 'o2_frac': 0.3}),
    ('Does a methanol droplet go out at 18% oxygen and 15% CO2?', 'predict', {'fuel': 'methanol', 'co2_frac': 0.15}),
    ('¿Cuántas pruebas de Nomex no prendieron?', 'count', {'material': 'nomex'}),
    ('¿Por qué se rompían las gotas en FLEX?', 'explain_doc', {}),
    ('¿Hay datos de algodón con 34 % de oxígeno?', 'coverage', {'material': 'sibal', 'o2_frac': 0.34}),
    ('Compará metanol y heptano con 21 % de O2', 'compare', {'fuel': 'methanol'}),
    ('¿Y en gravedad lunar con humedad?', 'coverage', {'gravity_g': 0.165}),
    ('hola como estas', 'off_topic', {}),
    ('oxygen 25%, helium 20%, heptane droplet 2.5 mm on a fiber', 'predict', {'he_frac': 0.2, 'droplet_d0_mm': 2.5, 'support': 'fiber'}),
])
def test_understand(q, intent, cond):
    u = understand(q)
    assert u.intent == intent
    got = u.conditions.model_dump(exclude_none=True)
    for k, v in cond.items():
        assert got.get(k) == pytest.approx(v) if isinstance(v, float) else got.get(k) == v


def test_language():
    assert understand('¿Se apaga una gota?').lang == 'es'
    assert understand('Does a droplet go out?').lang == 'en'


def test_plan_tools():
    tools = lambda q: [c.tool for c in make_plan(understand(q)).calls]   # noqa: E731
    assert tools('¿Se apaga una gota de metanol con 18 % de oxígeno?') == ['predict', 'coverage']
    assert tools('¿Cuántas pruebas de Nomex hubo?') == ['tests_search']
    assert tools('¿Qué es una llama fría?') == ['rag_search']
    assert tools('hola') == []


def test_in_domain_prediction_is_answer():
    a = answer(AskRequest(text='¿Se apaga una gota de metanol con 18 % de oxígeno y 15 % de CO2?'), tools=LocalTools())
    assert a.decision == 'answer' and a.predictions[0].probability is not None and 'predict' in a.tools_used


def test_untested_combination_abstains_with_evidence():
    a = answer(AskRequest(text='¿Se apaga una gota de heptano con 34 % de oxígeno y 20 % de CO2 a 0,7 atm?'), tools=LocalTools())
    assert a.decision == 'abstain' and a.predictions[0].probability is None and a.evidence


def test_count_and_off_topic_and_docs_unavailable():
    assert answer(AskRequest(text='¿Cuántas pruebas de Nomex no prendieron?'), tools=LocalTools()).decision == 'answer'
    assert answer(AskRequest(text='hola como estas'), tools=LocalTools()).decision == 'abstain'
    a = answer(AskRequest(text='¿Por qué se rompían las gotas en FLEX?'), tools=LocalTools())
    assert a.decision == 'abstain' and 'no está disponible' in a.text


def test_follow_up_inherits_conditions():
    a = answer(AskRequest(text='¿y con 25 % de CO2?', history=[ChatTurn(role='user', text='¿Se apaga una gota de metanol con 18 % de oxígeno?')]),
               tools=LocalTools())
    assert a.understood.fuel == 'methanol' and a.understood.co2_frac == pytest.approx(0.25)


def test_local_tests_search_matches_known_counts():
    r = LocalTools().tests_search(TestsSearchRequest(family='solids', material='Nomex', group_by=['outcome_group']))
    assert r.ok and r.data.n_total == 9 and {'outcome_group': 'no_flame', 'n': 7} in r.data.counts


def test_http_tools_with_mock_api():
    preds = LocalTools().predict(Conditions(fuel='methanol', o2_frac=0.18)).data
    def handler(request: httpx.Request):
        assert request.headers['Authorization'] == 'Bearer tok'
        if request.url.path == '/predict':
            return httpx.Response(200, json=[p.model_dump() for p in preds])
        if request.url.path == '/rag/search':
            return httpx.Response(503, json={'detail': 'not ready'})
        return httpx.Response(404)
    ht = HttpTools('https://api.example', 'tok', transport=httpx.MockTransport(handler))
    assert ht.predict(Conditions(fuel='methanol', o2_frac=0.18)).data[0].probability == preds[0].probability
    from flame.contracts import RagSearchRequest
    r = ht.rag_search(RagSearchRequest(query='why'))
    assert not r.ok and '503' in r.error


def test_service_endpoint_and_token(monkeypatch):
    monkeypatch.setenv('LLM_SERVICE_TOKEN', 'secret')
    from assistant.config import get_settings
    get_settings.cache_clear()
    from assistant.app import app
    c = TestClient(app)
    assert c.post('/answer', json={'text': 'hola como estas'}).status_code == 401
    r = c.post('/answer', json={'text': '¿Se apaga una gota de metanol con 18 % de oxígeno?'}, headers={'Authorization': 'Bearer secret'})
    assert r.status_code == 200 and r.json()['decision'] == 'answer'
    assert c.get('/health').json()['status'] == 'ok'
