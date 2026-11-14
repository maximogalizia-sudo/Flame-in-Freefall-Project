"""The assistant (LLM) service client and /ask (PLAN_ASISTENTE M4): HTTPS only, token, 90 s timeout, validated
response, and the template answer when the service is off or fails."""
import httpx
import pytest
from fastapi.testclient import TestClient

from api import main, services
from api.contracts import Answer, AskRequest, Conditions
from api.main import app
from api.services import LlmClient, ServiceError

client = TestClient(app)
GOOD = Answer(understood=Conditions(family="flex", fuel="methanol"), intent="will_it_burn", unknown_terms=[],
              text="From the models: 98 %.", predictions=[], evidence=[], citations=[], chart=None,
              decision="answer", tools_used=["predict", "rag_search"], trace_id="t-1", assistant_version="llm-v1")


def transport(handler):
    return httpx.MockTransport(handler)


def llm(handler, token="secret", timeout=2):
    return LlmClient("https://llm.example.com", token, timeout, transport=transport(handler))


def test_rejects_plain_http_outside_localhost():
    with pytest.raises(ServiceError):
        LlmClient("http://llm.example.com", None, 2)
    LlmClient("http://localhost:9000", None, 2)          # allowed for development


def test_not_configured_returns_none():
    assert LlmClient(None, None, 2).answer(AskRequest(text="hello there")) is None


def test_answer_forwards_body_and_token_and_validates():
    seen = {}

    def handler(request: httpx.Request):
        seen.update(auth=request.headers.get("authorization"), url=str(request.url), body=request.read())
        return httpx.Response(200, json=GOOD.model_dump(mode="json"))

    req = AskRequest(text="Does methanol go out at 18 % O2?", history=[{"role": "user", "text": "hi"}], lang="en")
    out = llm(handler).answer(req)
    assert seen["auth"] == "Bearer secret" and seen["url"] == "https://llm.example.com/answer"
    assert b"Does methanol go out" in seen["body"] and b'"history"' in seen["body"] and b'"lang":"en"' in seen["body"]
    assert out.decision == "answer" and out.tools_used == ["predict", "rag_search"] and out.trace_id == "t-1"


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(500),
    lambda r: httpx.Response(200, text="not json"),
    lambda r: httpx.Response(200, json={"text": "missing the required fields"}),
    lambda r: httpx.Response(200, json={**GOOD.model_dump(mode="json"), "decision": "maybe"}),
    lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)),
], ids=["http-500", "not-json", "invalid-answer", "invalid-decision", "timeout"])
def test_failures_degrade_to_none(handler):
    assert llm(handler).answer(AskRequest(text="hello there")) is None


def test_default_timeout_is_90_seconds(monkeypatch):
    monkeypatch.delenv("LLM_TIMEOUT_S", raising=False)
    monkeypatch.setenv("LLM_SERVICE_URL", "https://llm.example.com")
    services.get_settings.cache_clear()
    try:
        assert services.get_llm().timeout_s == 90
    finally:
        services.get_settings.cache_clear()


def test_ask_uses_the_service_when_it_answers(monkeypatch):
    monkeypatch.setattr(main, "get_llm", lambda: llm(lambda r: httpx.Response(200, json=GOOD.model_dump(mode="json"))))
    a = client.post("/ask", json={"text": "Does methanol go out at 18 % O2?"}).json()
    assert a["decision"] == "answer" and a["tools_used"] == ["predict", "rag_search"] and a["trace_id"] == "t-1"


@pytest.mark.parametrize("service", [
    LlmClient(None, None, 2),                                              # service not configured
    LlmClient("https://llm.example.com", "x", 2, transport=transport(lambda r: httpx.Response(503))),
])
def test_ask_falls_back_to_the_template_answer(monkeypatch, service):
    monkeypatch.setattr(main, "get_llm", lambda: service)
    a = client.post("/ask", json={"text": "Would cotton fabric burn at 34% oxygen in a lunar habitat?"}).json()
    assert a["decision"] == "partial" and a["verification"] == ["assistant unavailable: template answer"]
    assert a["understood"]["material"] == "sibal" and a["predictions"][0]["in_domain"] is False
    assert a["assistant_version"] == "template-v1"
