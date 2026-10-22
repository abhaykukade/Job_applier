from __future__ import annotations

from job_applier.llm.base import LLM, LLMError, strict_schema
from job_applier.secrets import get_secret
from job_applier.settings import LLMSettings

__all__ = ["LLM", "LLMError", "get_llm", "strict_schema"]


def get_llm(cfg: LLMSettings) -> LLM:
    if cfg.provider == "anthropic":
        from job_applier.llm.anthropic_llm import AnthropicLLM

        return AnthropicLLM(get_secret("anthropic_api_key"), cfg.model, cfg.effort)
    if cfg.provider == "openai":
        from job_applier.llm.openai_llm import OpenAILLM

        return OpenAILLM(get_secret("openai_api_key"), cfg.model, cfg.base_url)
    if cfg.provider == "ollama":
        from job_applier.llm.ollama_llm import OllamaLLM

        return OllamaLLM(cfg.model, cfg.base_url)
    raise LLMError(f"Unknown LLM provider {cfg.provider!r}")
