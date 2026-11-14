"""Load the document index (RAG/index/rag_index.parquet, built by RAG/build_index.py) into pgvector (M3).

Creates the `vector` extension and the rag schema (RAG/sql/001_rag.sql), then replaces rag.document / rag.chunk
with the index shipped in the image. Skips the reload when the same index (same sha256) is already loaded, so
container restarts are fast. If rag.chunk was created for another embedding size (or without chunk_kind), it is
dropped and created again from 001_rag.sql.

Run (from Backend/, uses DATABASE_URL):  .venv/Scripts/python -m pipeline.load_rag
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras

_HERE = Path(__file__).resolve()
sys.path[:0] = [str(_HERE.parents[1]), str(_HERE.parents[2])]
from api.settings import get_settings  # noqa: E402


def rag_dir() -> Path:
    """RAG/ next to Backend/ locally; /app/rag in the Docker image."""
    for candidate in (_HERE.parents[2] / "RAG", _HERE.parents[1] / "rag"):
        if (candidate / "index" / "manifest.json").exists():
            return candidate
    raise SystemExit("RAG index not found (RAG/index/manifest.json)")


DOC_COLS = ["accession", "doi", "s3_key", "sha256", "file_name", "title", "category", "n_pages", "source_url"]


def _py(v):
    if v is None or (not isinstance(v, (str, bytes, list)) and pd.isna(v)):
        return None
    return v.item() if hasattr(v, "item") else v


def _drop_stale_chunk_table(cur, dim: int) -> None:
    """rag.chunk from an older index (other vector size or no chunk_kind column) cannot take the new rows."""
    cur.execute("SELECT to_regclass('rag.chunk') IS NOT NULL")
    if not cur.fetchone()[0]:
        return
    cur.execute("SELECT attname, atttypmod FROM pg_attribute WHERE attrelid = 'rag.chunk'::regclass "
                "AND attname IN ('embedding', 'chunk_kind') AND NOT attisdropped")
    cols = dict(cur.fetchall())
    if cols.get("embedding") != dim or "chunk_kind" not in cols:
        print(f"rag: rag.chunk has embedding size {cols.get('embedding')} / chunk_kind={'chunk_kind' in cols}; "
              f"recreating it for size {dim}")
        cur.execute("DROP TABLE rag.chunk")
        cur.execute("SELECT to_regclass('rag.index_meta') IS NOT NULL")
        if cur.fetchone()[0]:
            cur.execute("DELETE FROM rag.index_meta")      # force the full reload below


def main() -> None:
    url = get_settings().database_url
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    base = rag_dir()
    manifest = json.loads((base / "index" / "manifest.json").read_text(encoding="utf-8"))
    with psycopg2.connect(url) as conn, conn.cursor() as cur:
        _drop_stale_chunk_table(cur, manifest["embedding_dim"])
        cur.execute((base / "sql" / "001_rag.sql").read_text(encoding="utf-8"))
        cur.execute("CREATE TABLE IF NOT EXISTS rag.index_meta (id int PRIMARY KEY DEFAULT 1, index_sha256 text, "
                    "embedding_model text, n_chunks int, loaded_at timestamptz DEFAULT now())")
        cur.execute("SELECT index_sha256 FROM rag.index_meta WHERE id = 1")
        row = cur.fetchone()
        if row and row[0] == manifest["index_sha256"]:
            print(f"rag: index {manifest['index_sha256'][:12]} already loaded ({manifest['n_chunks']} chunks)")
            return

        df = pd.read_parquet(base / "index" / "rag_index.parquet")
        cur.execute("TRUNCATE rag.chunk, rag.document RESTART IDENTITY CASCADE")
        docs = df.drop_duplicates("file_name")[DOC_COLS]
        doc_ids = {}
        for r in docs.itertuples(index=False):
            cur.execute(f"INSERT INTO rag.document ({', '.join(DOC_COLS)}) VALUES ({', '.join(['%s'] * len(DOC_COLS))}) "
                        "RETURNING document_id", tuple(_py(v) for v in r))
            doc_ids[r.file_name] = cur.fetchone()[0]
        rows = [(doc_ids[r.file_name], int(r.chunk_index), _py(r.page_from), _py(r.page_to), r.content, int(r.tokens),
                 r.chunk_kind, "[" + ",".join(f"{x:.7g}" for x in r.embedding) + "]")
                for r in df.itertuples(index=False)]
        psycopg2.extras.execute_values(
            cur, "INSERT INTO rag.chunk (document_id, chunk_index, page_from, page_to, content, tokens, chunk_kind, "
                 "embedding) VALUES %s", rows, template="(%s, %s, %s, %s, %s, %s, %s, %s::vector)", page_size=200)
        cur.execute("INSERT INTO rag.index_meta (id, index_sha256, embedding_model, n_chunks) VALUES (1, %s, %s, %s) "
                    "ON CONFLICT (id) DO UPDATE SET index_sha256 = EXCLUDED.index_sha256, "
                    "embedding_model = EXCLUDED.embedding_model, n_chunks = EXCLUDED.n_chunks, loaded_at = now()",
                    (manifest["index_sha256"], manifest["embedding_model"], len(df)))
        cur.execute("ANALYZE rag.chunk")
        print(f"rag: loaded {len(docs)} documents, {len(df)} chunks ({manifest['embedding_model']})")


if __name__ == "__main__":
    main()
