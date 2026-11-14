"""Hybrid search over the NASA reports for the assistant: POST /rag/search (docs/PLAN_ASISTENTE.md, M3).

  dense   : pgvector cosine distance between the query embedding and each chunk (top k), exact scan (the corpus
            has a few thousand chunks, so an exact scan costs milliseconds and the report filter is always exact)
  lexical : Postgres full-text search, any query word may match (OR), ranked by ts_rank_cd divided by the chunk
            length (top k). Words that appear in more than COMMON_TERM_SHARE of the chunks ("FLEX", "droplet",
            "flame"...) say nothing about which passage answers the question, so they are ignored unless every
            query word is that common.
  fusion  : reciprocal-rank fusion, score = sum over channels of 1 / (60 + rank); up to 30 candidates

No reranker here: the assistant service (LC) reranks the candidates.

Both channels leave out the chunks marked chunk_kind = 'table' (mostly numbers, see RAG/build_index.py): those
numbers are answered by /tests/search, and in the search they only added noise.

The query is embedded with the SAME model that built the index (RAG/build_index.py): BAAI/bge-small-en-v1.5 through
fastembed (ONNX, no torch, ~70 MB on disk), normalized, 384 dims, with QUERY_INSTRUCTION in front (documents were
embedded without it). English only, like the reports: the assistant translates Spanish questions before searching.
The model is loaded once, in a background thread at startup, so the first search does not pay for it.

RAG_QUERY_EMBEDDINGS=off turns the dense channel off (every candidate then has rank_dense = None); the same fallback
applies if the model cannot be loaded, so the API keeps answering from the text channel instead of failing.
"""
from __future__ import annotations

import logging
import os
import threading

import numpy as np

from . import db
from .contracts import Citation, RagCandidate, RagSearchRequest, RagSearchResponse

log = logging.getLogger("flame.rag")

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
RRF_K = 60
MAX_CANDIDATES = 30
COMMON_TERM_SHARE = 0.2

EMBEDDINGS_ENABLED = os.environ.get("RAG_QUERY_EMBEDDINGS", "on").strip().lower() not in {"off", "0", "false", "no"}
LEXICAL_ONLY = "lexical-only (query embeddings off)"

_model = None
_model_error: str | None = None
_lock = threading.Lock()


def _load_model():
    global _model, _model_error
    with _lock:
        if _model is not None:
            return _model
        try:
            from fastembed import TextEmbedding

            threads = max(1, int(os.environ.get("RAG_EMBED_THREADS", "2")))
            _model = TextEmbedding(EMBEDDING_MODEL, cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"), threads=threads)
            _model_error = None
        except Exception as e:  # noqa: BLE001 - reported by /health and /rag/search
            _model_error = f"{type(e).__name__}: {e}"
            log.exception("could not load the embedding model")
        return _model


def warm_up() -> None:
    """Load the embedding model in the background (called at API startup), unless query embeddings are off."""
    if not EMBEDDINGS_ENABLED:
        return
    threading.Thread(target=_load_model, name="rag-warm-up", daemon=True).start()


def status() -> str:
    if not EMBEDDINGS_ENABLED:
        return "lexical_only"
    if _model is not None:
        return "ok"
    return f"error: {_model_error}" if _model_error else "loading"


def embed_query(text: str) -> list[float]:
    model = _load_model()
    if model is None:
        raise RuntimeError(f"embedding model unavailable ({_model_error})")
    v = next(iter(model.embed([QUERY_INSTRUCTION + text])))
    return (v / max(float(np.linalg.norm(v)), 1e-12)).astype(float).tolist()


def _vector_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


DOC_FILTER = ("c.chunk_kind = 'text' AND "
              "(cardinality(%(acc)s::text[]) = 0 OR d.accession = ANY(%(acc)s::text[]))")
DENSE_SQL = f"""
    SELECT c.chunk_id FROM rag.chunk c JOIN rag.document d USING (document_id)
    WHERE {DOC_FILTER}
    ORDER BY c.embedding <=> %(vec)s::vector LIMIT %(k)s"""
# The query words as stems (the same 'english' configuration that built rag.chunk.tsv), with their share of chunks.
LEXICAL_SQL = f"""
    WITH terms AS (SELECT unnest(tsvector_to_array(to_tsvector('english', %(text)s))) AS t),
         stat  AS (SELECT word, ndoc FROM ts_stat('SELECT tsv FROM rag.chunk WHERE chunk_kind = ''text''')),
         total AS (SELECT greatest(count(*), 1)::float AS n FROM rag.chunk WHERE chunk_kind = 'text'),
         share AS (SELECT t, coalesce(s.ndoc, 0) / total.n AS frac FROM terms LEFT JOIN stat s ON s.word = terms.t, total),
         kept  AS (SELECT t FROM share WHERE frac <= %(common)s
                   UNION ALL
                   SELECT t FROM share WHERE NOT EXISTS (SELECT 1 FROM share WHERE frac <= %(common)s)),
         q     AS (SELECT to_tsquery('simple', string_agg(quote_literal(t), ' | ')) AS tq FROM kept)
    SELECT c.chunk_id FROM rag.chunk c JOIN rag.document d USING (document_id), q
    WHERE {DOC_FILTER} AND q.tq IS NOT NULL AND numnode(q.tq) > 0 AND c.tsv @@ q.tq
    ORDER BY ts_rank_cd(c.tsv, q.tq, 2) DESC, c.chunk_id LIMIT %(k)s"""
DETAIL_SQL = """
    SELECT c.chunk_id, d.accession, d.title AS doc_title, d.file_name, c.page_from, c.page_to, c.content, d.source_url
    FROM rag.chunk c JOIN rag.document d USING (document_id) WHERE c.chunk_id = ANY(%s)"""


def _ranked_ids(sql: str, params: dict, exact: bool = False) -> list[int]:
    with db.connection() as conn, conn.cursor() as cur:
        if exact:      # exact vector scan instead of the approximate HNSW index
            cur.execute("SET LOCAL enable_indexscan = off")
            cur.execute("SET LOCAL enable_bitmapscan = off")
        cur.execute(sql, params)
        return [r[0] for r in cur.fetchall()]


def search(req: RagSearchRequest) -> RagSearchResponse:
    acc = [a.strip().upper() for a in req.accessions if a.strip()]
    dense, model_name = [], LEXICAL_ONLY
    if EMBEDDINGS_ENABLED:
        try:
            vec = _vector_literal(embed_query(req.query))
            dense = _ranked_ids(DENSE_SQL, {"acc": acc, "vec": vec, "k": req.k}, exact=True)
            model_name = EMBEDDING_MODEL
        except RuntimeError as e:          # the model could not be loaded: keep answering from the text channel
            log.warning("dense search unavailable, using the text channel only: %s", e)
    lexical = _ranked_ids(LEXICAL_SQL, {"acc": acc, "text": req.query, "k": req.k, "common": COMMON_TERM_SHARE})

    rank_dense = {cid: i + 1 for i, cid in enumerate(dense)}
    rank_lexical = {cid: i + 1 for i, cid in enumerate(lexical)}
    scores = {cid: sum(1 / (RRF_K + r[cid]) for r in (rank_dense, rank_lexical) if cid in r)
              for cid in rank_dense.keys() | rank_lexical.keys()}
    top = sorted(scores, key=lambda cid: (-scores[cid], rank_dense.get(cid, 10**6), cid))[:MAX_CANDIDATES]

    details = {r["chunk_id"]: r for r in db.query(DETAIL_SQL, (top,))} if top else {}
    candidates = [RagCandidate(**details[cid], rank_dense=rank_dense.get(cid), rank_lexical=rank_lexical.get(cid),
                               score_rrf=round(scores[cid], 6)) for cid in top]
    return RagSearchResponse(query=req.query, embedding_model=model_name, candidates=candidates)


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
