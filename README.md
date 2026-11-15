# Flame in Freefall

NASA Space Apps Challenge 2026 · Rosario · *Flame in Freefall: SI-Powered Fire Safety
Insights from Microgravity Combustion Data*.

An **early-warning fire-risk indicator, scenario explorer and phenomenon explainer** built on
NASA's microgravity combustion experiments (NASA PSI). For given cabin or habitat conditions
it answers **what the evidence says, which NASA tests back it, and where NASA has no data yet** —
through interpretable models and an assistant whose numbers come only from NASA data and our models, and are checked
automatically before they are shown (the text is written by a language model; see "Use of AI").

- **Early warning**: for an atmosphere (O₂, pressure, CO₂, helium) a traffic light per scenario
  (fuel droplets, gas flames, cabin materials); **gray = never tested by NASA (unknown, not safe)**.
  It is an indicator from laboratory experiments, not a certified alarm.
- **Scenarios**: "what if" curves for one variable, with the untested part left empty.
- **Explanations**: how a variable acts (direction, size with interval, interactions), where it
  was tested and what cannot be separated, with NASA report passages.

## Architecture

```
Frontend (Cloudflare Pages) ──HTTPS──► Backend API (FastAPI + PostgreSQL/pgvector) ──HTTPS──► Assistant (LLM) service
                                                  ▲                                               │
                                                  └─────── tools: models · tabular search · report search
```

- **Backend** (`Backend/`): ETL of the 22 PSI experimental tables into PostgreSQL, curated
  views, and the API. It serves the three tools of the assistant: the interpretable models
  (`flame/`, `models/`), a whitelisted search over the curated tables (`data/processed/`)
  and a hybrid search (pgvector + full text + rank fusion) over NASA reports (`RAG/`).
- **Assistant service** (`assistant/`, separate process): understands the question with rules
  (Spanish or English), calls the tools, and has an open-weight language model (hosted by Groq;
  swappable for a local one) write the answer **only from what the tools return**. It abstains
  when the data cannot answer, cites its sources and has every number checked against the
  tool outputs; without the language model it answers from templates. Contract in
  `flame/contracts.py` and [`docs/SERVICES.md`](docs/SERVICES.md).
- **Models** (`flame/`, `models/`): interpretable models trained by `python -m flame.train` from
  the curated tables (`data/processed/`, produced by the EDA notebooks in `notebooks/`). Model
  card: [`models/MODEL_CARD.md`](models/MODEL_CARD.md).
- **Frontend** (`Frontend/`): static site with the extinction map, the outcome chart and the
  chat. It talks only to the backend API; without one it runs offline on JSON exported
  from the same views (the chat needs the API).

## Data model

Fact constellation with snowflaked dimensions:

- `fact_test_run`: 1,871 rows, one per test or table row.
- `fact_measurement`: one row per variable per test, in canonical units.
- `test_step`: flow sequences within a test.
- `fact_log_entry`: per-day logs, design matrices and sessions.
- `fact_file`: the PSI file index.

Conventions follow the team's ETL requirements: nothing is deleted or imputed,
original values are kept, known errors are flagged `is_valid = false`, special values
carry flags (censored, ramped, excel_error), units are canonical (`o2_frac`,
`pressure_kpa`, `flow_cm_s`), test IDs are stable (`PSI-69:114`), and every
correction is a decision in `Backend/pipeline/mappings/decisions.csv`, applied only
once approved.

## Run it locally

```bash
cd Backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # Windows paths
.venv/Scripts/python -m pipeline.run              # raw CSV -> data/staging (L1)
.venv/Scripts/python -m pipeline.load --apply     # -> data/processed (L2) + PostgreSQL (DATABASE_URL in ../.env)
.venv/Scripts/python -m pytest                    # tests
.venv/Scripts/python -m pipeline.load_curated     # LC's curated tables -> PostgreSQL (mart.*)
.venv/Scripts/python -m pipeline.load_rag         # document index -> pgvector (needs RAG/index/ and the vector extension)
cd .. && Backend/.venv/Scripts/python -m uvicorn --app-dir Backend api.main:app --port 8000   # from the repo root: `flame` must be importable

cd Frontend && python -m http.server 8787      # open http://127.0.0.1:8787/?api=http://127.0.0.1:8000
```

Assistant service and models (from the repo root):

```bash
python -m venv .venv && .venv/Scripts/pip install -r assistant/requirements.txt pytest
.venv/Scripts/python -m pytest tests/                       # models + assistant tests
.venv/Scripts/python -m assistant.bench.run                  # evaluation bank (add --llm to use the language model)
.venv/Scripts/python -m uvicorn assistant.app:app --port 8100   # POST /answer ; LLM_BASE_URL/LLM_API_KEY/LLM_MODEL in .env
```

Validate the SQL without a server (PGlite) and refresh the offline JSON:
`cd Backend/tools && npm install && npm run check`.

## API

`GET /health` · `/stats` · `/investigations` · `/tests` · `/tests/{test_id}`
(`/measurements`, `/steps`) · `/log-entries` · `/envelope` · `/data-dictionary` ·
`POST /predict` · `POST /coverage` · `POST /explain` (and `GET /explain/{family}`) ·
`POST /risk` · `POST /sweep` · `GET /effects/{variable}` ·
`POST /tests/search` · `POST /rag/search` · `GET /documents/search` · `POST /ask`.
Interactive docs at `/docs`.

`/predict`, `/coverage` and `/explain` run the interpretable models of `flame/`: a number is
given only where NASA ran clean tests with the same combination of conditions; elsewhere the
answer is "no data" with the reasons and the closest real tests. `/ask` forwards the
question to the assistant service and, if it is off or fails, answers with a template
built from the same models (`decision = "partial"`).

`/risk` (early-warning indicator for one atmosphere), `/sweep` (vary one input) and
`/effects/{variable}` (how one variable acts) wrap LC's `flame/scenarios.py`. Gray means
"NASA never tested this": unknown, never safe. The indicator comes from microgravity
laboratory experiments; it is not a certified fire alarm.

## Deploy

| Piece | Where | How |
|---|---|---|
| API + Postgres (pgvector) | Railway, service `api` | `cd Backend && railway up .. --path-as-root --service api` (build context is the repo root; `RAILWAY_DOCKERFILE_PATH=Backend/Dockerfile`). The image carries `flame/`, `models/`, `data/processed/` and the document index, runs LC's model tests while building, and reloads the curated tables and the index at every start |
| Frontend | Cloudflare Pages, project `nasa-dashboard` | `git archive <commit> Frontend` then `wrangler pages deploy Frontend --project-name nasa-dashboard --branch production`. The API URL is the `flame-api` meta tag of `index.html` |

Backend variables (Railway): `DATABASE_URL`, `CORS_ORIGINS`, `ASSISTANT_API_TOKEN`,
`LLM_SERVICE_URL`, `LLM_SERVICE_TOKEN`, optional `LLM_TIMEOUT_S` and `RAG_QUERY_EMBEDDINGS` (`on` by default). See
[`docs/SERVICES.md`](docs/SERVICES.md). Tokens are never committed.

## Use of AI

Built with help from an AI coding assistant (Claude). All approach and data decisions
were made by the team. No data was generated by AI. Inside the product, an open-weight
language model hosted by Groq translates Spanish search queries and writes the chat answers,
but only from what the tools return (models, curated tables, NASA reports); numbers are
checked against those outputs, the assistant abstains when the data cannot answer, and
templates take over if the model is unavailable. Details: [`docs/AI_USAGE.md`](docs/AI_USAGE.md).

## References

Data: NASA PSI, CC0-1.0. Full list: [`docs/REFERENCES.md`](docs/REFERENCES.md).
