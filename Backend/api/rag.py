"""Hybrid search over the NASA reports for the assistant: POST /rag/search (docs/PLAN_ASISTENTE.md, M3).

  dense   : pgvector cosine distance between the query embedding and each chunk (top k)
  lexical : Postgres full-text search, any query word may match (OR), ranked by ts_rank_cd (top k)
  fusion  : reciprocal-rank fusion, score = sum over channels of 1 / (60 + rank); up to 30 candidates

No reranker here: the assistant service (LC) reranks the candidates.

The query is embedded with the SAME model that built the index (RAG/build_index.py): Qwen3-Embedding-0.6B,
normalized, with QUERY_INSTRUCTION in front (documents were embedded without it). The model is loaded once,
in a background thread at startup, so the first search does not pay for it.
"""
from __future__ import annotations

import logging
import os
import threading

from . import db
from .contracts import Citation, RagCandidate, RagSearchRequest, RagSearchResponse

log = logging.getLogger("flame.rag")

EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
QUERY_INSTRUCTION = ("Instruct: Given a question about NASA microgravity combustion experiments, "
                     "retrieve passages from NASA reports that answer it.\nQuery: ")
RRF_K = 60
MAX_CANDIDATES = 30

_model = None
_model_error: str | None = None
_lock = threading.Lock()


def _load_model():
    global _model, _model_error
    with _lock:
        if _model is not None:
            return _model
        try:
            import torch
            from sentence_transformers import SentenceTransformer

            torch.set_num_threads(max(1, int(os.environ.get("RAG_TORCH_THREADS", "4"))))
            _model = SentenceTransformer(EMBEDDING_MODEL, device="cpu", model_kwargs={"dtype": "float32"})
            _model_error = None
        except Exception as e:  # noqa: BLE001 - reported by /health and /rag/search
            _model_error = f"{type(e).__name__}: {e}"
            log.exception("could not load the embedding model")
        return _model


def warm_up() -> None:
    """Load the embedding model in the background (called at API startup)."""
    threading.Thread(target=_load_model, name="rag-warm-up", daemon=True).start()


def status() -> str:
    if _model is not None:
        return "ok"
    return f"error: {_model_error}" if _model_error else "loading"


def embed_query(text: str) -> list[float]:
    model = _load_model()
    if model is None:
        raise RuntimeError(f"embedding model unavailable ({_model_error})")
    v = model.encode([QUERY_INSTRUCTION + text], normalize_embeddings=True, convert_to_numpy=True)[0]
    return v.astype(float).tolist()


def _vector_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


DOC_FILTER = "(cardinality(%(acc)s::text[]) = 0 OR d.accession = ANY(%(acc)s::text[]))"
DENSE_SQL = f"""
    SELECT c.chunk_id FROM rag.chunk c JOIN rag.document d USING (document_id)
    WHERE {DOC_FILTER}
    ORDER BY c.embedding <=> %(vec)s::vector LIMIT %(k)s"""
LEXICAL_SQL = f"""
    WITH q AS (SELECT to_tsquery('english', replace(plainto_tsquery('english', %(text)s)::text, ' & ', ' | ')) AS tq)
    SELECT c.chunk_id FROM rag.chunk c JOIN rag.document d USING (document_id), q
    WHERE {DOC_FILTER} AND numnode(q.tq) > 0 AND c.tsv @@ q.tq
    ORDER BY ts_rank_cd(c.tsv, q.tq) DESC, c.chunk_id LIMIT %(k)s"""
DETAIL_SQL = """
    SELECT c.chunk_id, d.accession, d.title AS doc_title, d.file_name, c.page_from, c.page_to, c.content, d.source_url
    FROM rag.chunk c JOIN rag.document d USING (document_id) WHERE c.chunk_id = ANY(%s)"""


def _ranked_ids(sql: str, params: dict) -> list[int]:
    with db.connection() as conn, conn.cursor() as cur:
        # with an accession filter, keep scanning the HNSW index until k rows pass it (pgvector >= 0.8)
        cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        cur.execute(sql, params)
        return [r[0] for r in cur.fetchall()]


def search(req: RagSearchRequest) -> RagSearchResponse:
    acc = [a.strip().upper() for a in req.accessions if a.strip()]
    vec = _vector_literal(embed_query(req.query))
    dense = _ranked_ids(DENSE_SQL, {"acc": acc, "vec": vec, "k": req.k})
    lexical = _ranked_ids(LEXICAL_SQL, {"acc": acc, "text": req.query, "k": req.k})

    rank_dense = {cid: i + 1 for i, cid in enumerate(dense)}
    rank_lexical = {cid: i + 1 for i, cid in enumerate(lexical)}
    scores = {cid: sum(1 / (RRF_K + r[cid]) for r in (rank_dense, rank_lexical) if cid in r)
              for cid in rank_dense.keys() | rank_lexical.keys()}
    top = sorted(scores, key=lambda cid: (-scores[cid], rank_dense.get(cid, 10**6), cid))[:MAX_CANDIDATES]

    details = {r["chunk_id"]: r for r in db.query(DETAIL_SQL, (top,))} if top else {}
    candidates = [RagCandidate(**details[cid], rank_dense=rank_dense.get(cid), rank_lexical=rank_lexical.get(cid),
                               score_rrf=round(scores[cid], 6)) for cid in top]
    return RagSearchResponse(query=req.query, embedding_model=EMBEDDING_MODEL, candidates=candidates)


def citations(query: str, accessions: list[str] | None = None, k: int = 5) -> list[Citation]:
    """Top fused candidates as contract Citations (used by /documents/search and the template answer)."""
    res = search(RagSearchRequest(query=query, accessions=accessions or [], k=30))
    out = []
    for c in res.candidates[:k]:
        pages = None if c.page_from is None else (
            f"p. {c.page_from}" if c.page_from == c.page_to else f"pp. {c.page_from}-{c.page_to}")
        out.append(Citation(doc_id=c.file_name, psi_id=c.accession, title=c.doc_title, page_or_section=pages,
                            snippet=c.content[:400], url=c.source_url))
    return out
