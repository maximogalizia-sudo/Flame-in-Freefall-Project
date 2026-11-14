"""Bearer token for the assistant's tool endpoints (docs/PLAN_ASISTENTE.md §2).

The LLM service calls /tests/search and /rag/search with `Authorization: Bearer <ASSISTANT_API_TOKEN>`.
If ASSISTANT_API_TOKEN is not set (local development), the endpoints are open.
"""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from .settings import get_settings


def require_assistant_token(authorization: str | None = Header(default=None)) -> None:
    expected = get_settings().assistant_api_token
    if not expected:
        return
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip(), expected):
        raise HTTPException(401, "Missing or invalid assistant token", headers={"WWW-Authenticate": "Bearer"})
