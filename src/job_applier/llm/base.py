"""Provider-neutral interface: every call asks for JSON matching a Pydantic model."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def structured(self, system: str, prompt: str, output: type[T]) -> T:
        """Return an instance of `output` generated from the prompt."""
        ...


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema accepted by strict structured-output modes (Anthropic, OpenAI, Ollama).

    Inlines $refs, requires every property and forbids extra properties.
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            target = copy.deepcopy(defs[node["$ref"].split("/")[-1]])
            return walk(target)
        out = {k: walk(v) for k, v in node.items() if k not in ("title", "default")}
        if out.get("type") == "object" and "properties" in out:
            out["required"] = list(out["properties"])
            out["additionalProperties"] = False
        return out

    return walk(schema)


def extract_json(text: str) -> Any:
    """Parse the JSON in a model's reply, tolerating <think> blocks, code fences and chatter."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def schema_hint(output: type[BaseModel]) -> str:
    """Spelled-out schema; local models follow constrained output better when they can read it too."""
    return (
        "\n\nReply with only a JSON object (no prose, no code fences) that matches this JSON schema:\n"
        + json.dumps(strict_schema(output))
    )


def parse_with_retry(ask: Callable[[str], str], prompt: str, output: type[T], attempts: int = 2) -> T:
    """Call `ask(prompt)` and validate the reply; on a bad reply, ask again quoting the error."""
    error = ""
    for _ in range(attempts):
        text = ask(prompt if not error else f"{prompt}\n\nYour previous reply was invalid ({error}). Try again.")
        try:
            return output.model_validate(extract_json(text))
        except (json.JSONDecodeError, ValidationError) as exc:
            error = str(exc).splitlines()[0][:300]
    raise LLMError(f"The model didn't return valid {output.__name__} JSON: {error}")
