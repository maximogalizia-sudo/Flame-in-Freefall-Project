"""HTTPS clients for the two external services the backend calls when needed.

  RAG service  POST {RAG_SERVICE_URL}/search  {"query", "psi_ids", "k"}  -> {"results": [Citation...]}
  LLM service  POST {LLM_SERVICE_URL}/parse   {"text"}                    -> {"conditions": {...}, "intent": "..."}

Per D2 the LLM is only an optional fallback to translate the question into
`Conditions` + intent: it never sees data and never writes numbers. Every response
is validated against the contract; anything invalid is discarded.

Both clients degrade gracefully: if a service is not configured, unreachable or
returns garbage, they return None and the API answers without it.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from .contracts import Citation, Conditions
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


class RagClient(ExternalService):
    name = "rag"

    def search(self, query: str, psi_ids: list[str] | None = None, k: int = 3) -> list[Citation] | None:
        if not self.configured:
            return None
        try:
            data = self._post("/search", {"query": query, "psi_ids": psi_ids or [], "k": k})
            return [Citation(**c) for c in data.get("results", [])][:k]
        except (httpx.HTTPError, ValueError, ValidationError, TypeError) as e:
            log.warning("rag search failed: %s", e)
            return None


class LlmClient(ExternalService):
    name = "llm"

    def parse(self, text: str) -> tuple[Conditions, str] | None:
        """Fallback input translation only (D2). Returns None on any problem."""
        if not self.configured:
            return None
        try:
            data = self._post("/parse", {"text": text})
            return Conditions(**data.get("conditions", {})), str(data.get("intent", "will_it_burn"))
        except (httpx.HTTPError, ValueError, ValidationError, TypeError) as e:
            log.warning("llm parse failed: %s", e)
            return None


def get_rag() -> RagClient:
    s = get_settings()
    return RagClient(s.rag_service_url, s.rag_service_token, s.service_timeout_s)


def get_llm() -> LlmClient:
    s = get_settings()
    return LlmClient(s.llm_service_url, s.llm_service_token, s.service_timeout_s)
