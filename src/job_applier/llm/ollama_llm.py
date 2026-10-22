from __future__ import annotations

import json

import httpx

from job_applier.llm.base import LLMError, T, strict_schema


class OllamaLLM:
    def __init__(self, model: str, base_url: str | None = None):
        self.model = model
        self.base_url = (base_url or "http://localhost:11434").rstrip("/")

    def structured(self, system: str, prompt: str, output: type[T]) -> T:
        try:
            response = httpx.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "format": strict_schema(output),
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=600,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama error: {exc}") from exc
        return output.model_validate(json.loads(response.json()["message"]["content"]))
