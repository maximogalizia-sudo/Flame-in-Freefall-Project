# Assistant, tools and services contract

Source of truth for every payload: [`flame/contracts.py`](../flame/contracts.py) (contract v2). The backend API
re-exports it (`Backend/api/contracts.py`); never redefine a model on either side.

```
Frontend (Cloudflare Pages) ──HTTPS──► Backend API (FastAPI + Postgres/pgvector) ──HTTPS──► Assistant (LLM) service
                                                  ▲                                            │
                                                  └──────── tool calls (Bearer token) ─────────┘
                                                    /predict /coverage /explain /tests/search /rag/search
```

- The **frontend** only talks to the backend API. Service URLs and tokens never reach the browser.
- The **backend API** owns the data: LC's interpretable models (`flame/`), the curated tables and the document index.
- The **assistant service** (separate process, operated by LC) plans which tools to call, calls them over HTTPS,
  reranks the document candidates, writes the answer **only from the tool outputs**, marks it
  `answer` / `partial` / `abstain` and verifies that every number it wrote appears in a tool output.
  It never receives database credentials and never writes SQL: it can only call the typed endpoints below.

## Configuration (backend environment)

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | Postgres with the `vector` extension (pgvector) |
| `CORS_ORIGINS` | Comma-separated browser origins allowed to call the API (the Cloudflare Pages URLs) |
| `ASSISTANT_API_TOKEN` | Secret the assistant sends as `Authorization: Bearer …` to `/tests/search` and `/rag/search`. If unset, those two endpoints are open (local development only) |
| `LLM_SERVICE_URL` | Base URL of the assistant service. `https://` only (plain `http://` is accepted for `localhost`). If unset, `/ask` answers with the template fallback |
| `LLM_SERVICE_TOKEN` | Secret the API sends as `Authorization: Bearer …` to the assistant service |
| `LLM_TIMEOUT_S` | How long `/ask` waits for the assistant, default 90 s |
| `RAG_QUERY_EMBEDDINGS` | `on` (default, set in the Docker image) / `off`. With `off`, `/rag/search` runs on the text channel only (`rank_dense = null`, `embedding_model = "lexical-only (query embeddings off)"`, `/health` shows `rag: lexical_only`). The same happens automatically if the model cannot be loaded |
| `RAG_EMBED_THREADS` | CPU threads used to embed a search query, default 2 |

## Tool endpoints (called by the assistant)

| Endpoint | Tool | Notes |
|---|---|---|
| `POST /predict`, `POST /coverage`, `POST /explain` (also `GET /explain/{family}`) | ML models | `Conditions` in → `Prediction` / `EvidenceItem`. "We don't know" rule by combination: no number outside the tested combinations |
| `POST /tests/search` | Tabular query | `TestsSearchRequest` → `TestsSearchResponse` over `mart.flex_tests`, `mart.cfig_tests`, `mart.bass_tests`. Whitelisted filters and `group_by` columns per family, values always bound parameters; a filter that does not exist for a family is rejected with `422` |
| `POST /rag/search` | Document search | `RagSearchRequest` → `RagSearchResponse`: top-k by vector (pgvector, cosine) + top-k by full text, fused with reciprocal-rank fusion (`1/(60+rank)`), up to 30 candidates with both ranks. Table chunks are left out. No reranker here |
| `POST /risk` | Early-warning indicator (v3) | `RiskRequest` (an atmosphere) → `RiskResponse`: overall `level` and one traffic light per scenario; `gray` = NASA never tested it (unknown, not safe); `disclaimer` always present. Public, no database |
| `POST /sweep` | Scenario explorer (v3) | `SweepRequest` → `SweepResponse`: vary one input; untested points come back with `probability = null`. Public, no database |
| `GET /effects/{variable}` | Per-variable explanation (v3) | `EffectsResponse` for `o2_frac`, `co2_frac`, `he_frac`, `pressure_kpa` or `droplet_d0_mm`: direction and size with interval, where it was tested, what was never tested. Public, no database |

`/tests/search` and `/rag/search` require the bearer token when `ASSISTANT_API_TOKEN` is set.

### Document index

`RAG/build_index.py` (offline) turns the NASA reports into `RAG/index/rag_index.parquet`: page-aware chunks of about
1,400 characters with 220 of overlap, embedded with `BAAI/bge-small-en-v1.5` through fastembed (ONNX, no torch;
normalized, 384 dimensions; documents without prefix, queries with `Represent this sentence for searching relevant
passages: `). English only, like the reports: the assistant translates Spanish questions before searching. Each chunk
has `chunk_kind` = `text` or `table` (more than 35 % of the words are numbers, or half or more of its lines carry 4
numbers or more); `/rag/search` only returns `text`. `Backend/pipeline/load_rag.py` loads the index into
`rag.document` / `rag.chunk` (schema `RAG/sql/001_rag.sql`) on every container start, skipping the reload when the
same index is already there, and recreating `rag.chunk` when the vector size changes. The API embeds each query with
the same model on CPU (about 10 ms; the whole API stays around 300 MB of RAM); the text channel ignores query words
found in more than 20 % of the chunks and ranks by `ts_rank_cd` normalized by length.

## Assistant service contract

`GET /health` → `200` when ready.

`POST /answer`, body = `AskRequest`:

```json
{ "text": "Why did FLEX droplets break up?", "history": [{ "role": "user", "text": "…" }], "lang": "en" }
```

Response = `Answer` (v2): the written `text`, `decision`, `predictions`, `evidence`, `citations`, `tools_used`,
`verification` (problems found by the verifier, empty = passed), `trace_id`, `assistant_version`.

`POST /ask` on the backend forwards the body as is, with a 90 s timeout and the bearer token, and validates the
response against `Answer`. If the service is not configured, does not answer in time, returns an HTTP error or an
invalid body, `/ask` does not fail: it returns the template answer (conditions read with simple rules, then
`predict` + `coverage`) with `decision = "partial"` and
`verification = ["assistant unavailable: template answer"]`.
