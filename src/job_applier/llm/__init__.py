from __future__ import annotations

from job_applier.llm.base import LLM, LLMError, strict_schema
from job_applier.secrets import get_secret
from job_applier.settings import LLMSettings

__all__ = ["LLM", "LLMError", "LOCAL_PROVIDERS", "PROVIDERS", "get_llm", "list_local_models", "strict_schema"]

PROVIDERS = {
    "anthropic": "Claude (Anthropic API)",
    "openai": "OpenAI API",
    "ollama": "Ollama (local)",
    "local": "Local server: LM Studio, llama.cpp, vLLM… (OpenAI-compatible)",
}
LOCAL_PROVIDERS = ("ollama", "local")


def _local(cfg: LLMSettings, require_model: bool = True):
    from job_applier.llm.local_llm import LocalServerLLM, OllamaLLM

    if require_model and (not cfg.model or cfg.model.startswith("claude-")):
        raise LLMError("Choose a local model in Setup → AI model")
    if cfg.provider == "ollama":
        return OllamaLLM(cfg.model, cfg.base_url, cfg.context_tokens, cfg.timeout_s)
    return LocalServerLLM(cfg.model, cfg.base_url, cfg.timeout_s)


def get_llm(cfg: LLMSettings) -> LLM:
    if cfg.provider == "anthropic":
        from job_applier.llm.anthropic_llm import AnthropicLLM

        return AnthropicLLM(get_secret("anthropic_api_key"), cfg.model, cfg.effort)
    if cfg.provider == "openai":
        from job_applier.llm.openai_llm import OpenAILLM

        return OpenAILLM(get_secret("openai_api_key"), cfg.model, cfg.base_url)
    if cfg.provider in LOCAL_PROVIDERS:
        return _local(cfg)
    raise LLMError(f"Unknown LLM provider {cfg.provider!r}")


def list_local_models(cfg: LLMSettings) -> list[str]:
    """Models installed in Ollama / loaded in the local server (empty if it isn't running)."""
    if cfg.provider not in LOCAL_PROVIDERS:
        return []
    try:
        return _local(cfg, require_model=False).list_models()
    except LLMError:
        return []
