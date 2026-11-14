"""Minimal OpenAI-compatible chat client (Groq, Ollama, llama.cpp, vLLM... all expose /v1/chat/completions).

Criterion 6: the provider is a URL + key + model name in environment variables. Switching to a local model
(e.g. Ollama: LLM_BASE_URL=http://localhost:11434/v1) needs no code change. Returns None on any failure.
"""
from __future__ import annotations

import logging

import httpx

from .config import get_settings

log = logging.getLogger('assistant.llm')


class LLM:
    def __init__(self, base_url: str, api_key: str | None, model: str, timeout_s: float = 30.0,
                 transport: httpx.BaseTransport | None = None):
        self.url = base_url.rstrip('/') + '/chat/completions'
        self.headers = {'Content-Type': 'application/json', **({'Authorization': f'Bearer {api_key}'} if api_key else {})}
        self.model, self.timeout, self.transport = model, timeout_s, transport

    def chat(self, system: str, user: str, max_tokens: int = 700, temperature: float = 0.1) -> str | None:
        body = {'model': self.model, 'temperature': temperature, 'max_tokens': max_tokens,
                'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]}
        for attempt in range(2):
            try:
                with httpx.Client(timeout=self.timeout, transport=self.transport) as cl:
                    r = cl.post(self.url, json=body, headers=self.headers)
                    r.raise_for_status()
                    return r.json()['choices'][0]['message']['content']
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                log.warning('llm call failed (attempt %d): %s', attempt + 1, e)
        return None


def get_llm() -> LLM | None:
    s = get_settings()
    if not s.llm_base_url:
        return None
    return LLM(s.llm_base_url, s.llm_api_key, s.llm_model, s.llm_timeout_s)
