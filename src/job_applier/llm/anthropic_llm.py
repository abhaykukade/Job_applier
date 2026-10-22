from __future__ import annotations

import json

import anthropic

from job_applier.llm.base import LLMError, T, strict_schema


class AnthropicLLM:
    def __init__(self, api_key: str | None, model: str, effort: str = "medium"):
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model
        self.effort = effort

    def structured(self, system: str, prompt: str, output: type[T]) -> T:
        try:
            # Server-side fallbacks re-run a request a safety classifier declines
            # (e.g. a security-engineering resume) on a suitable model instead of failing.
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": strict_schema(output)},
                },
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError as exc:
            raise LLMError(f"Anthropic rate limit: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Cannot reach the Anthropic API: {exc}") from exc
        except TypeError as exc:  # raised by the SDK when no credentials are configured
            if "authentication" not in str(exc):
                raise
            raise LLMError("No Anthropic API key: add it in Setup") from exc

        if response.stop_reason == "refusal":
            raise LLMError("The model declined this request")
        if response.stop_reason == "max_tokens":
            raise LLMError("Response was cut off (max_tokens)")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LLMError("No text in model response")
        return output.model_validate(json.loads(text))
