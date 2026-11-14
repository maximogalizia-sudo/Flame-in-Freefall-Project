"""Minimal OpenAI-compatible chat client (Groq, Ollama, llama.cpp, vLLM... all expose /v1/chat/completions).

Criterion 6: the provider is a URL + key + model name(s) in environment variables. Switching to a local model
(e.g. Ollama: LLM_BASE_URL=http://localhost:11434/v1) needs no code change. Returns None on any failure.

Durability (the system must keep working while NASA judges it, weeks later): LLM_MODEL accepts a comma-separated
list of models. If the provider retires one (it happened: llama-3.3-70b-versatile disappeared from Groq), the client
moves to the next and remembers it. If all fail, the pipeline answers with templates (criterion 4).
"""
from __future__ import annotations

import logging
import re
import time

import httpx

from .config import get_settings

log = logging.getLogger('assistant.llm')
DEFAULT_MODELS = 'qwen/qwen3.8-27b,openai/gpt-oss-120b,openai/gpt-oss-20b'


class LLM:
    def __init__(self, base_url: str, api_key: str | None, model: str, timeout_s: float = 30.0,
                 transport: httpx.BaseTransport | None = None):
        self.url = base_url.rstrip('/') + '/chat/completions'
        self.headers = {'Content-Type': 'application/json', **({'Authorization': f'Bearer {api_key}'} if api_key else {})}
        self.models = [m.strip() for m in (model or DEFAULT_MODELS).split(',') if m.strip()]
        self.idx, self.timeout, self.transport = 0, timeout_s, transport

    @property
    def active_model(self) -> str | None:
        return self.models[self.idx] if self.idx < len(self.models) else None

    def chat(self, system: str, user: str, max_tokens: int = 450, temperature: float = 0.1,
             max_wait_s: float = 12.0) -> str | None:
        waited = False
        while self.active_model:
            body = {'model': self.active_model, 'temperature': temperature, 'max_tokens': max_tokens,
                    'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]}
            try:
                with httpx.Client(timeout=self.timeout, transport=self.transport) as cl:
                    r = cl.post(self.url, json=body, headers=self.headers)
                if r.status_code == 429 and not waited:          # free-tier token budget per minute: wait once
                    wait = _retry_after(r)
                    if wait is not None and wait <= max_wait_s:
                        log.warning('llm rate-limited, waiting %.1f s', wait)
                        time.sleep(wait); waited = True
                        continue
                if r.status_code == 404 or (r.status_code == 400 and 'model' in r.text.lower()):
                    log.warning('llm model %s not available (%s): trying the next one', self.active_model, r.status_code)
                    self.idx += 1
                    continue
                r.raise_for_status()
                text = r.json()['choices'][0]['message']['content'] or ''
                return re.sub(r'<think>.*?</think>', '', text, flags=re.S).strip()   # reasoning models (Qwen3)
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                log.warning('llm call failed: %s', e)
                return None
        return None


def _retry_after(r: httpx.Response) -> float | None:
    """Seconds to wait from 'retry-after' or Groq's 'x-ratelimit-reset-tokens' (e.g. '7.5s', '1m2s')."""
    v = r.headers.get('retry-after') or r.headers.get('x-ratelimit-reset-tokens')
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        m = re.fullmatch(r'(?:(\d+)m)?([\d.]+)s', v.strip())
        return (int(m[1] or 0) * 60 + float(m[2]) + 0.5) if m else None


def get_llm() -> LLM | None:
    s = get_settings()
    if not s.llm_base_url:
        return None
    return LLM(s.llm_base_url, s.llm_api_key, s.llm_model, s.llm_timeout_s)
