"""HTTPS client for the assistant (LLM) service, built and operated by LC (docs/PLAN_ASISTENTE.md, M4).

  POST {LLM_SERVICE_URL}/answer   {"text", "history", "lang"}  ->  Answer (contract v2)
  GET  {LLM_SERVICE_URL}/health

The service plans, calls this API's tools (/predict /coverage /explain /tests/search /rag/search, with
ASSISTANT_API_TOKEN), writes the answer only from the tool outputs and verifies it. Here we only forward the
question with `Authorization: Bearer <LLM_SERVICE_TOKEN>` and validate what comes back against the contract.

The client degrades gracefully: if the service is not configured, unreachable, slow (> LLM_TIMEOUT_S, 90 s by
default) or answers something that is not a valid `Answer`, `answer()` returns None and /ask falls back to the
template answer.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from .contracts import Answer, AskRequest
from .settings import get_settings

log = logging.getLogger("flame.services")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ServiceError(RuntimeError):
    pass


class ExternalService:
    """Base client: HTTPS only (except localhost for development), bearer token, timeout."""

    name = "service"

    def __init__(self, base_url: str | None, token: str | None, timeout_s: float,
                 transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token = token
        self.timeout_s = timeout_s
        self.transport = transport
        if self.base_url:
            u = urlparse(self.base_url)
            if u.scheme != "https" and u.hostname not in LOCAL_HOSTS:
                raise ServiceError(f"{self.name}: {self.base_url} must use https")

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _post(self, path: str, payload: dict) -> dict:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        with httpx.Client(timeout=self.timeout_s, transport=self.transport) as client:
            r = client.post(f"{self.base_url}{path}", json=payload, headers=headers)
            r.raise_for_status()
            return r.json()

    def health(self) -> str:
        if not self.configured:
            return "not_configured"
        try:
            with httpx.Client(timeout=min(self.timeout_s, 3), transport=self.transport) as client:
                r = client.get(f"{self.base_url}/health")
            return "ok" if r.status_code == 200 else f"error_{r.status_code}"
        except httpx.HTTPError:
            return "unreachable"


class LlmClient(ExternalService):
    name = "llm"

    def answer(self, req: AskRequest) -> Answer | None:
        """The assistant's answer, or None on any problem (not configured, timeout, HTTP error, invalid body)."""
        if not self.configured:
            return None
        try:
            return Answer.model_validate(self._post("/answer", req.model_dump(exclude_none=True)))
        except (httpx.HTTPError, ValueError, ValidationError, TypeError) as e:   # ValidationError is a ValueError
            log.warning("llm answer failed: %s: %s", type(e).__name__, str(e)[:300])
            return None


def get_llm() -> LlmClient:
    s = get_settings()
    return LlmClient(s.llm_service_url, s.llm_service_token, s.llm_timeout_s)
