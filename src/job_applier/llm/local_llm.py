"""Models running on your own PC: Ollama, or any OpenAI-compatible local server
(LM Studio, llama.cpp's llama-server, vLLM, Jan, LocalAI).

Nothing leaves your machine. Local models are less reliable at strict JSON, so replies
are cleaned up and re-asked once if they don't validate.
"""

from __future__ import annotations

import httpx

from job_applier.llm.base import LLMError, T, parse_with_retry, schema_hint, strict_schema

OLLAMA_URL = "http://localhost:11434"
LOCAL_SERVER_URL = "http://localhost:1234/v1"  # LM Studio's default; llama-server uses :8080/v1


def _connect_error(name: str, url: str, exc: Exception) -> LLMError:
    return LLMError(f"Can't reach {name} at {url}; is it running? ({exc.__class__.__name__})")


class OllamaLLM:
    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        context_tokens: int = 16384,
        timeout_s: float = 900,
        client: httpx.Client | None = None,
    ):
        self.model = model
        self.base_url = (base_url or OLLAMA_URL).rstrip("/")
        self.context_tokens = context_tokens
        self.http = client or httpx.Client(timeout=timeout_s)

    def _chat(self, system: str, prompt: str, schema: dict) -> str:
        try:
            resp = self.http.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "format": schema,
                    "keep_alive": "15m",
                    # Ollama's default context is small and it silently drops the start of longer
                    # prompts (your resume!), so size it explicitly.
                    "options": {"num_ctx": self.context_tokens, "temperature": 0.2},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                },
            )
        except httpx.TransportError as exc:
            raise _connect_error("Ollama", self.base_url, exc) from exc
        if resp.status_code == 404:
            raise LLMError(f"Model '{self.model}' isn't installed in Ollama; run: ollama pull {self.model}")
        if resp.status_code >= 400:
            raise LLMError(f"Ollama error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if data.get("prompt_eval_count", 0) >= self.context_tokens - 16:
            raise LLMError(
                f"The prompt filled the whole {self.context_tokens}-token context, so part of it was dropped. "
                "Raise 'Context size' in Setup (and make sure your PC has the memory for it)."
            )
        if data.get("done_reason") == "length":
            raise LLMError("The model's reply was cut off; raise 'Context size' in Setup")
        return data.get("message", {}).get("content", "")

    def structured(self, system: str, prompt: str, output: type[T]) -> T:
        schema = strict_schema(output)
        return parse_with_retry(lambda p: self._chat(system, p, schema), prompt + schema_hint(output), output)

    def list_models(self) -> list[str]:
        try:
            resp = self.http.get(f"{self.base_url}/api/tags", timeout=3)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise _connect_error("Ollama", self.base_url, exc) from exc
        return sorted(m["name"] for m in resp.json().get("models", []))


class LocalServerLLM:
    """Any server speaking the OpenAI chat-completions API on your machine."""

    # Strongest first; servers that reject one get the next.
    MODES = ("json_schema", "json_object", "none")

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        timeout_s: float = 900,
        client: httpx.Client | None = None,
    ):
        self.model = model
        self.base_url = (base_url or LOCAL_SERVER_URL).rstrip("/")
        self.http = client or httpx.Client(timeout=timeout_s)
        self.mode = self.MODES[0]

    def _payload(self, system: str, prompt: str, output: type[T]) -> dict:
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        }
        if self.mode == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": output.__name__, "schema": strict_schema(output), "strict": True},
            }
        elif self.mode == "json_object":
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _chat(self, system: str, prompt: str, output: type[T]) -> str:
        while True:
            try:
                resp = self.http.post(f"{self.base_url}/chat/completions", json=self._payload(system, prompt, output))
            except httpx.TransportError as exc:
                raise _connect_error("the local model server", self.base_url, exc) from exc
            if resp.status_code in (400, 422, 500) and "response_format" in resp.text and self.mode != "none":
                self.mode = self.MODES[self.MODES.index(self.mode) + 1]
                continue
            if resp.status_code == 404:
                raise LLMError(f"Model '{self.model}' not found on {self.base_url}; load it in your local server")
            if resp.status_code >= 400:
                raise LLMError(f"Local server error {resp.status_code}: {resp.text[:300]}")
            choice = resp.json()["choices"][0]
            if choice.get("finish_reason") == "length":
                raise LLMError("The model's reply was cut off; raise the context length in your local server")
            return choice["message"].get("content") or ""

    def structured(self, system: str, prompt: str, output: type[T]) -> T:
        return parse_with_retry(lambda p: self._chat(system, p, output), prompt + schema_hint(output), output)

    def list_models(self) -> list[str]:
        try:
            resp = self.http.get(f"{self.base_url}/models", timeout=3)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise _connect_error("the local model server", self.base_url, exc) from exc
        return sorted(m["id"] for m in resp.json().get("data", []))
