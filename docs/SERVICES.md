# External services contract

The backend does all the work. It owns the curated database and the API that the
frontend calls. Two capabilities run as **separate services** that the backend calls
over **HTTPS** only when it needs them:

```
Frontend (Cloudflare Pages) ──HTTPS──► Backend API (FastAPI + Postgres) ──HTTPS──► Document search service ("RAG")
                                                                     └───HTTPS──► LLM service (optional fallback)
```

The frontend never talks to these services, and their URLs and tokens never reach the
browser. When a service is not configured or does not answer, the backend keeps
working without it and reports the state in `GET /health`.

## Configuration (backend environment)

| Variable | Meaning |
|---|---|
| `RAG_SERVICE_URL` | Base URL of the document search service. Must be `https://` (plain `http://` only for `localhost`) |
| `RAG_SERVICE_TOKEN` | Sent as `Authorization: Bearer <token>` |
| `LLM_SERVICE_URL` | Base URL of the LLM service (optional) |
| `LLM_SERVICE_TOKEN` | Sent as `Authorization: Bearer <token>` |
| `SERVICE_TIMEOUT_S` | Per-request timeout, default 8 s |

## Document search service

Semantic search over NASA PSI documents (reports, presentations, papers). It only
**finds** the paragraph that supports an answer, so it can be cited. It does not draw
conclusions.

`GET /health` → `200` when ready.

`POST /search`

```json
{ "query": "PMMA rod extinction at low flow", "psi_ids": ["PSI-25"], "k": 3 }
```

Response:

```json
{
  "results": [
    {
      "doc_id": "PSI-25_Reports_BASS-II_Summary.pdf",
      "psi_id": "PSI-25",
      "title": "BASS-II Summary Report",
      "page_or_section": "p. 14",
      "snippet": "The flame extinguished when the flow was reduced below ...",
      "url": "https://psi.nasa.gov/..."
    }
  ]
}
```

`psi_id` must be the PSI accession (`PSI-25`), which is the shared key with the
backend database. Each result is validated: an invalid result discards the response.

## LLM service (optional, decision D2)

Only a **fallback to translate the question** into structured conditions when the
backend's own parser understands nothing. It never sees data, never computes and
never writes numbers into answers. The answer text is always built from templates
over the model output.

`GET /health` → `200` when ready.

`POST /parse`

```json
{ "text": "Would cotton burn at 34% oxygen in a lunar habitat?" }
```

Response (validated against `Conditions` in `Backend/api/contracts.py`; out-of-range
values such as `"o2_frac": 34` are rejected):

```json
{ "conditions": { "family": "solids", "material": "sibal", "o2_frac": 0.34, "gravity_g": 0.165 },
  "intent": "will_it_burn" }
```

Intents: `will_it_burn`, `which_variable`, `evidence`, `coverage`.
