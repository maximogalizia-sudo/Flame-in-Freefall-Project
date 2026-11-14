"""HTTP service: POST /answer (AskRequest -> Answer v2) and GET /health. Called by MG's API /ask (M4).

Auth: `Authorization: Bearer LLM_SERVICE_TOKEN` (if the variable is set). Run: uvicorn assistant.app:app --port 8100
"""
from __future__ import annotations

import hmac

from fastapi import Depends, FastAPI, Header, HTTPException

from flame.contracts import Answer, AskRequest

from . import VERSION
from .config import get_settings
from .envfile import load_env
from .generate import make_drafter
from .llm import get_llm
from .pipeline import answer

load_env()   # local runs only: reads ./.env if present (Railway passes real environment variables)
from .tools import get_tools

app = FastAPI(title='Flame in Freefall assistant', version=VERSION,
              description='Grounded assistant over NASA microgravity combustion data (LC). Contract: flame/contracts.py v2.')


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = get_settings().service_token
    if not expected:
        return
    scheme, _, token = (authorization or '').partition(' ')
    if scheme.lower() != 'bearer' or not hmac.compare_digest(token.strip(), expected):
        raise HTTPException(401, 'Missing or invalid service token', headers={'WWW-Authenticate': 'Bearer'})


_LLM = None


def get_llm_instance():
    """One LLM client per process, so the model fallback (LLM_MODEL list) is remembered between requests."""
    global _LLM
    if _LLM is None:
        _LLM = get_llm()
    return _LLM


@app.get('/health')
def health() -> dict:
    s, llm = get_settings(), get_llm_instance()
    return {'status': 'ok', 'version': VERSION, 'tools_mode': get_tools().mode,
            'llm': 'configured' if llm else 'not_configured', 'llm_model_active': llm.active_model if llm else None,
            'llm_models': llm.models if llm else [], 'reranker': s.reranker_model or 'off'}


@app.post('/answer', response_model=Answer, dependencies=[Depends(require_token)])
def answer_endpoint(req: AskRequest) -> Answer:
    llm = get_llm_instance()
    return answer(req, drafter=make_drafter(llm) if llm else None, llm=llm)
