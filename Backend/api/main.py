"""Flame in Freefall API (docs/DOCUMENTO_TECNICO.md §7.3).

The backend does all the work: it serves the curated database and orchestrates the
model functions. The RAG and LLM services are separate and called over HTTPS only
when needed (see services.py).

Run locally (from the repo root, so that `flame` is importable):
    Backend/.venv/Scripts/python -m uvicorn --app-dir Backend api.main:app --reload --port 8000
"""
from __future__ import annotations

from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import db, modeling, tabular
from .auth import require_assistant_token
from .contracts import (Answer, AskRequest, Citation, Conditions, EvidenceItem, Prediction, TestsSearchRequest,
                        TestsSearchResponse)
from .services import get_llm, get_rag
from .settings import get_settings

app = FastAPI(
    title="Flame in Freefall API",
    version="0.2.0",
    description="NASA PSI microgravity combustion data, curated for fire-safety insights.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

Family = Literal["flex", "solids", "cfig"]
FamilyGroup = Literal["droplet", "gas", "solid", "aerosol", "ground"]


# ---------------------------------------------------------------- health
@app.get("/health")
def health() -> dict:
    database = db.ping()
    return {"status": "ok" if database else "degraded", "database": "ok" if database else "down",
            "services": {"rag": get_rag().health(), "llm": get_llm().health()}}


# ---------------------------------------------------------------- data
@app.get("/stats")
def stats() -> dict:
    return db.query_one("SELECT * FROM mart.v_stats")


@app.get("/investigations")
def investigations() -> list[dict]:
    return db.query("SELECT * FROM mart.v_investigations ORDER BY investigation_id")


@app.get("/investigations/{psi_id}")
def investigation(psi_id: str) -> dict:
    row = db.query_one("SELECT * FROM mart.v_investigations WHERE psi_id = %s", (psi_id.upper(),))
    if not row:
        raise HTTPException(404, f"Unknown investigation {psi_id}")
    return row


@app.get("/tests")
def tests(psi_id: str | None = None, family_group: FamilyGroup | None = None, material: str | None = None,
          outcome: str | None = None, valid_only: bool = False,
          limit: int = Query(5000, ge=1, le=5000), offset: int = Query(0, ge=0)) -> list[dict]:
    where, params = ["row_type = 'test'"], []
    for col, val in (("psi_id", psi_id and psi_id.upper()), ("family_group", family_group),
                     ("material_code", material), ("outcome", outcome)):
        if val:
            where.append(f"{col} = %s")
            params.append(val)
    if valid_only:
        where.append("is_valid")
    params += [limit, offset]
    return db.query(f"SELECT * FROM mart.v_tests WHERE {' AND '.join(where)} ORDER BY test_id LIMIT %s OFFSET %s",
                    tuple(params))


# ---------------------------------------------------------------- assistant tool: tabular search (M2)
@app.post("/tests/search", response_model=TestsSearchResponse, dependencies=[Depends(require_assistant_token)])
def tests_search(req: TestsSearchRequest) -> TestsSearchResponse:
    """Whitelisted, parameterized search over LC's curated tables (mart.flex_tests / cfig_tests / bass_tests)."""
    try:
        return tabular.search(req)
    except tabular.SearchError as e:
        raise HTTPException(422, str(e)) from e


def _test_or_404(test_id: str) -> dict:
    row = db.query_one("SELECT * FROM mart.v_tests WHERE test_id = %s", (test_id,))
    if not row:
        raise HTTPException(404, f"Unknown test {test_id}")
    return row


@app.get("/tests/{test_id}")
def test(test_id: str) -> dict:
    return _test_or_404(test_id)


@app.get("/tests/{test_id}/measurements")
def measurements(test_id: str) -> list[dict]:
    _test_or_404(test_id)
    return db.query("SELECT * FROM mart.v_measurements WHERE test_id = %s ORDER BY variable", (test_id,))


@app.get("/tests/{test_id}/steps")
def steps(test_id: str) -> list[dict]:
    _test_or_404(test_id)
    return db.query("SELECT * FROM mart.v_test_steps WHERE test_id = %s ORDER BY step_order", (test_id,))


@app.get("/log-entries")
def log_entries(psi_id: str | None = None) -> list[dict]:
    if psi_id:
        return db.query("SELECT * FROM mart.v_log_entries WHERE psi_id = %s ORDER BY log_entry_id", (psi_id.upper(),))
    return db.query("SELECT * FROM mart.v_log_entries ORDER BY log_entry_id")


@app.get("/envelope")
def envelope() -> list[dict]:
    return db.query("SELECT * FROM mart.v_tested_envelope ORDER BY family_group")


@app.get("/data-dictionary")
def data_dictionary() -> list[dict]:
    return db.query("SELECT * FROM mart.v_data_dictionary ORDER BY variable")


# ---------------------------------------------------------------- model functions (§7.2)
@app.post("/predict", response_model=list[Prediction])
def predict(c: Conditions) -> list[Prediction]:
    return modeling.predict(c)


class ExplainRequest(BaseModel):
    family: Family


@app.get("/explain/{family}")
def explain(family: Family) -> dict:
    return modeling.explain(family)


@app.post("/explain")
def explain_post(req: ExplainRequest) -> dict:
    return modeling.explain(req.family)


@app.post("/coverage", response_model=list[EvidenceItem])
def coverage(c: Conditions) -> list[EvidenceItem]:
    return modeling.coverage(c)


@app.post("/ask", response_model=Answer)
def ask(req: AskRequest) -> Answer:
    return modeling.template_answer(req.text)


# ---------------------------------------------------------------- documents (RAG service proxy)
@app.get("/documents/search", response_model=list[Citation])
def documents_search(q: str = Query(..., min_length=3, max_length=300), psi_id: str | None = None,
                     k: int = Query(5, ge=1, le=10)) -> list[Citation]:
    rag = get_rag()
    if not rag.configured:
        raise HTTPException(503, "The document search service is not configured")
    results = rag.search(q, psi_ids=[psi_id.upper()] if psi_id else None, k=k)
    if results is None:
        raise HTTPException(502, "The document search service did not answer")
    return results
