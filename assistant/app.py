"""HTTP service: POST /answer (AskRequest -> Answer v2) and GET /health. Called by MG's API /ask (M4).

Auth: `Authorization: Bearer LLM_SERVICE_TOKEN` (if the variable is set). Run: uvicorn assistant.app:app --port 8100
"""
from __future__ import annotations

import hmac

from fastapi import Depends, FastAPI, Header, HTTPException

from flame.contracts import Answer, AskRequest

from . import VERSION
from .config import get_settings
from .pipeline import answer
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


def get_drafter():
    """Step 3 plugs the LLM drafter here; None = template answers."""
    try:
        from .generate import make_drafter
    except ImportError:
        return None
    return make_drafter()


@app.get('/health')
def health() -> dict:
    s = get_settings()
    return {'status': 'ok', 'version': VERSION, 'tools_mode': get_tools().mode,
            'llm': 'configured' if s.llm_base_url else 'not_configured', 'llm_model': s.llm_model if s.llm_base_url else None,
            'reranker': s.reranker_model or 'off'}


@app.post('/answer', response_model=Answer, dependencies=[Depends(require_token)])
def answer_endpoint(req: AskRequest) -> Answer:
    return answer(req, drafter=get_drafter())
