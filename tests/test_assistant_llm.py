"""Assistant step 3: LLM drafter + verifier, with a simulated OpenAI-compatible LLM (no network, no key)."""
import httpx
import pytest

from flame.contracts import AskRequest
from assistant.generate import make_drafter
from assistant.llm import LLM
from assistant.pipeline import answer
from assistant.tools import LocalTools

Q_IN = '¿Se apaga una gota de metanol con 18 % de oxígeno y 15 % de CO2?'
Q_OUT = '¿Se apaga una gota de heptano con 34 % de oxígeno y 20 % de CO2 a 0,7 atm?'


@pytest.fixture(autouse=True)
def _traces(tmp_path, monkeypatch):
    monkeypatch.setenv('ASSISTANT_TRACE_PATH', str(tmp_path / 't.jsonl'))
    from assistant.config import get_settings
    get_settings.cache_clear()


def fake_llm(reply: str | None):
    def handler(request: httpx.Request):
        if reply is None:
            return httpx.Response(503)
        return httpx.Response(200, json={'choices': [{'message': {'content': reply}}]})
    return LLM('https://llm.example/v1', 'k', 'm', transport=httpx.MockTransport(handler))


def run(q, reply):
    return answer(AskRequest(text=q), tools=LocalTools(), drafter=make_drafter(fake_llm(reply)))


def test_good_draft_is_used():
    base = answer(AskRequest(text=Q_IN), tools=LocalTools())
    p = round(base.predictions[0].probability * 100)
    a = run(Q_IN, f'[[DECISION:ANSWER]]\nSí: según el modelo, la llama se apaga con una probabilidad de {p} % [T1].')
    assert a.assistant_version.endswith(':llm') and a.decision == 'answer' and a.verification == []


def test_invented_number_is_rejected():
    a = run(Q_IN, '[[DECISION:ANSWER]]\nSe apaga en el 73,4 % de los casos [T1].')
    assert a.assistant_version.endswith(':template') and any('numbers not found' in v for v in a.verification)


def test_fake_citation_is_rejected():
    a = run(Q_IN, '[[DECISION:ANSWER]]\nSe apaga casi siempre [T9].')
    assert any('citations that do not exist' in v for v in a.verification)


def test_answer_on_untested_combination_is_rejected():
    a = run(Q_OUT, '[[DECISION:ANSWER]]\nSí, se apaga [T1].')
    assert a.decision == 'abstain' and any('says ANSWER' in v for v in a.verification)


def test_foreign_substance_is_rejected():
    a = run(Q_IN, '[[DECISION:ANSWER]]\nCon propano pasaría lo mismo [T1].')
    assert any('names substances' in v for v in a.verification)


def test_missing_mark_and_llm_down_fall_back_to_template():
    assert any('missing' in v for v in run(Q_IN, 'Se apaga [T1].').verification)
    a = run(Q_IN, None)
    assert a.assistant_version.endswith(':template') and 'unavailable' in a.verification[0]
