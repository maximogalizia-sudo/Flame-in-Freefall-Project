"""Runtime settings, read from environment variables (or the repo-root .env for local runs).

The backend does all the work. The RAG and LLM are separate services that the backend
calls over HTTPS only when needed; when a service is not configured, the API keeps
working without it and reports it in /health.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _load_env_file() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    rag_service_url: str | None
    rag_service_token: str | None
    llm_service_url: str | None
    llm_service_token: str | None
    service_timeout_s: float
    assistant_api_token: str | None = None
    cors_origins: list[str] = field(default_factory=list)


@lru_cache
def get_settings() -> Settings:
    _load_env_file()
    origins = os.environ.get("CORS_ORIGINS", "http://localhost:8787,http://127.0.0.1:8787")
    return Settings(
        database_url=os.environ.get("DATABASE_URL"),
        rag_service_url=os.environ.get("RAG_SERVICE_URL") or None,
        rag_service_token=os.environ.get("RAG_SERVICE_TOKEN") or None,
        llm_service_url=os.environ.get("LLM_SERVICE_URL") or None,
        llm_service_token=os.environ.get("LLM_SERVICE_TOKEN") or None,
        service_timeout_s=float(os.environ.get("SERVICE_TIMEOUT_S", "8")),
        assistant_api_token=os.environ.get("ASSISTANT_API_TOKEN") or None,
        cors_origins=[o.strip() for o in origins.split(",") if o.strip()],
    )
