"""POST /rag/search fusion logic (PLAN_ASISTENTE M3). The two retrieval channels and the embedding model are faked,
so this runs without pgvector or torch; the real end-to-end check runs against the deployed API."""
import pytest
from fastapi.testclient import TestClient

from api import rag
from api.contracts import RagSearchRequest
from api.main import app

client = TestClient(app)


@pytest.fixture
def fake_channels(monkeypatch):
    calls = {}

    def ranked(sql, params):
        calls.setdefault("params", []).append(params)
        return [11, 12, 13] if sql is rag.DENSE_SQL else [13, 14]

    def details(sql, params):
        return [{"chunk_id": cid, "accession": "PSI-69", "doc_title": "FLEX report", "file_name": "flex.pdf",
                 "page_from": 5, "page_to": 6, "content": f"chunk {cid}", "source_url": None} for cid in params[0]]

    monkeypatch.setattr(rag, "embed_query", lambda text: [0.0] * 1024)
    monkeypatch.setattr(rag, "_ranked_ids", ranked)
    monkeypatch.setattr(rag.db, "query", details)
    return calls


def test_rrf_fusion_and_ranks(fake_channels):
    res = rag.search(RagSearchRequest(query="why did FLEX droplets disrupt?", accessions=["psi-69"], k=5))
    ids = [c.chunk_id for c in res.candidates]
    assert ids[0] == 13                                    # found by both channels -> highest fused score
    assert set(ids) == {11, 12, 13, 14}
    top = res.candidates[0]
    assert (top.rank_dense, top.rank_lexical) == (3, 1)
    assert top.score_rrf == pytest.approx(1 / 63 + 1 / 61, rel=1e-4)
    only_text = next(c for c in res.candidates if c.chunk_id == 14)
    assert only_text.rank_dense is None and only_text.rank_lexical == 2
    assert res.embedding_model == "Qwen/Qwen3-Embedding-0.6B"
    assert all(p["acc"] == ["PSI-69"] and p["k"] == 5 for p in fake_channels["params"])


def test_endpoint_and_citations(fake_channels):
    r = client.post("/rag/search", json={"query": "what is a cool flame"})
    assert r.status_code == 200 and len(r.json()["candidates"]) == 4
    cites = client.get("/documents/search", params={"q": "cool flame", "k": 2}).json()
    assert len(cites) == 2 and cites[0]["page_or_section"] == "pp. 5-6" and cites[0]["psi_id"] == "PSI-69"


def test_validation():
    assert client.post("/rag/search", json={"query": "x"}).status_code == 422
    assert client.post("/rag/search", json={"query": "cool flame", "k": 61}).status_code == 422


def test_model_unavailable_is_503(monkeypatch):
    def broken(text):
        raise RuntimeError("embedding model unavailable (test)")
    monkeypatch.setattr(rag, "embed_query", broken)
    assert client.post("/rag/search", json={"query": "cool flame"}).status_code == 503
