"""Provider-neutral interface: every call asks for JSON matching a Pydantic model."""

from __future__ import annotations

import copy
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

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
