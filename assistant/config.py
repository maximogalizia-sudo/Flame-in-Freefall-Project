"""Settings from environment variables (never from the repo). Empty values = feature off."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    tools_mode: str              # "local" (flame + parquet in this repo) or "http" (MG's API)
    api_base_url: str | None     # MG's API, e.g. https://api-production-....up.railway.app
    assistant_api_token: str | None   # token this service sends to MG's tool endpoints
    service_token: str | None    # token MG's API must send to POST /answer (LLM_SERVICE_TOKEN)
    llm_base_url: str | None     # OpenAI-compatible endpoint, e.g. https://api.groq.com/openai/v1 or http://localhost:11434/v1
    llm_api_key: str | None
    llm_model: str
    llm_timeout_s: float
    trace_path: Path
    reranker_model: str | None   # None = keep the order given by /rag/search


@lru_cache
def get_settings() -> Settings:
    e = os.environ.get
    return Settings(
        tools_mode=(e("ASSISTANT_TOOLS_MODE") or ("http" if e("API_BASE_URL") else "local")).lower(),
        api_base_url=(e("API_BASE_URL") or None),
        assistant_api_token=e("ASSISTANT_API_TOKEN") or None,
        service_token=e("LLM_SERVICE_TOKEN") or None,
        llm_base_url=e("LLM_BASE_URL") or None,
        llm_api_key=e("LLM_API_KEY") or None,
        llm_model=e("LLM_MODEL") or "qwen/qwen3.8-27b,openai/gpt-oss-120b,openai/gpt-oss-20b",
        llm_timeout_s=float(e("LLM_TIMEOUT_S") or 30),
        trace_path=Path(e("ASSISTANT_TRACE_PATH") or "assistant_traces.jsonl"),
        reranker_model=e("RERANKER_MODEL") or None,
    )
