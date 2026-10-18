"""Minimal client for a local Ollama server (default http://127.0.0.1:11434).

* Only talks to the local machine by default.
* ``keep_alive`` lets Ollama unload the model from GPU memory when idle.
* Every failure is caught: the trading system must work without the model.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)


class OllamaUnavailable(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, settings: dict, transport: httpx.BaseTransport | None = None):
        a = settings["ai"]
        self.url = a["ollama_url"].rstrip("/")
        host = urlparse(self.url).hostname
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Ollama URL must point at this computer (127.0.0.1 / localhost)")
        self.model = a["model"]
        self.keep_alive = a.get("keep_alive", "5m")
        self.num_ctx = int(a.get("context_tokens", 4096))
        self.temperature = float(a.get("temperature", 0.2))
        self.timeout = float(a.get("timeout_seconds", 120))
        self._transport = transport

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(timeout=timeout, transport=self._transport)

    def status(self) -> dict:
        try:
            with self._client(5) as c:
                r = c.get(f"{self.url}/api/tags")
                r.raise_for_status()
                models = [m.get("name", "") for m in r.json().get("models", [])]
            # "llama3.1:8b" also matches tags such as "llama3.1:8b-instruct-q4_K_M"
            installed = any(m == self.model or m.startswith(self.model) for m in models)
            return {"available": True, "model_installed": installed, "models": models}
        except Exception as exc:
            return {"available": False, "model_installed": False, "error": type(exc).__name__}

    def generate(self, system: str, prompt: str) -> str:
        body = {"model": self.model, "system": system, "prompt": prompt, "stream": False,
                "keep_alive": self.keep_alive,
                "options": {"temperature": self.temperature, "num_ctx": self.num_ctx}}
        try:
            with self._client(self.timeout) as c:
                r = c.post(f"{self.url}/api/generate", json=body)
                r.raise_for_status()
                text = r.json().get("response", "")
        except Exception as exc:
            raise OllamaUnavailable(f"local model unavailable: {type(exc).__name__}") from None
        if not text.strip():
            raise OllamaUnavailable("local model returned an empty response")
        return text.strip()
