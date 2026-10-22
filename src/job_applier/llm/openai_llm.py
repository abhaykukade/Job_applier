from __future__ import annotations

import json

from job_applier.llm.base import LLMError, T, strict_schema


class OpenAILLM:
    def __init__(self, api_key: str | None, model: str, base_url: str | None = None):
        try:
            import openai
        except ImportError as exc:  # optional dependency
            raise LLMError("Install the OpenAI extra: pip install 'job-applier[openai]'") from exc
        self.openai = openai
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def structured(self, system: str, prompt: str, output: type[T]) -> T:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": output.__name__,
                        "schema": strict_schema(output),
                        "strict": True,
                    },
                },
            )
        except self.openai.OpenAIError as exc:
            raise LLMError(f"OpenAI error: {exc}") from exc
        content = response.choices[0].message.content
        if not content:
            raise LLMError("Empty OpenAI response")
        return output.model_validate(json.loads(content))
