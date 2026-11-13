"""The external-service clients: HTTPS only, validated responses, graceful degradation."""
import httpx
import pytest

from api.services import LlmClient, RagClient, ServiceError


def transport(handler):
    return httpx.MockTransport(handler)


def test_rejects_plain_http_outside_localhost():
    with pytest.raises(ServiceError):
        RagClient("http://rag.example.com", None, 2)
    RagClient("http://localhost:9000", None, 2)          # allowed for development


def test_not_configured_returns_none():
    assert RagClient(None, None, 2).search("pmma") is None
    assert LlmClient(None, None, 2).parse("x") is None


def test_rag_search_sends_token_and_validates():
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"results": [
            {"doc_id": "d1", "psi_id": "PSI-25", "snippet": "flame extinguished at 1 cm/s"}]})

    rag = RagClient("https://rag.example.com", "secret", 2, transport=transport(handler))
    out = rag.search("pmma extinction", psi_ids=["PSI-25"])
    assert seen["auth"] == "Bearer secret"
    assert out[0].psi_id == "PSI-25"


def test_rag_garbage_or_error_degrades_to_none():
    bad = RagClient("https://rag.example.com", None, 2,
                    transport=transport(lambda r: httpx.Response(200, json={"results": [{"nope": 1}]})))
    assert bad.search("x") is None
    down = RagClient("https://rag.example.com", None, 2, transport=transport(lambda r: httpx.Response(500)))
    assert down.search("x") is None


def test_llm_parse_is_validated_against_the_contract():
    ok = LlmClient("https://llm.example.com", None, 2, transport=transport(
        lambda r: httpx.Response(200, json={"conditions": {"o2_frac": 0.3, "material": "sibal"}, "intent": "will_it_burn"})))
    c, intent = ok.parse("cotton at 30% oxygen")
    assert c.o2_frac == 0.3 and intent == "will_it_burn"
    out_of_range = LlmClient("https://llm.example.com", None, 2, transport=transport(
        lambda r: httpx.Response(200, json={"conditions": {"o2_frac": 30}})))
    assert out_of_range.parse("x") is None              # 30 is not a fraction: rejected, never trusted
