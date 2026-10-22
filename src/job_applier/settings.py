"""User settings (search filters, caps, LLM provider) stored in data/settings.yaml."""

from __future__ import annotations

from typing import Literal

import yaml
from pydantic import BaseModel, Field

from job_applier import paths

Workplace = Literal["remote", "hybrid", "onsite"]


class LLMSettings(BaseModel):
    provider: Literal["anthropic", "openai", "ollama"] = "anthropic"
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    base_url: str | None = None  # Ollama server or OpenAI-compatible endpoint


class SearchSettings(BaseModel):
    # A job's title must contain at least one of these (empty = any title).
    titles_include: list[str] = Field(default_factory=list)
    titles_exclude: list[str] = Field(default_factory=list)
    # Seniority words such as "senior", "staff"; include = must have one, exclude = must have none.
    seniority_include: list[str] = Field(default_factory=list)
    seniority_exclude: list[str] = Field(default_factory=lambda: ["intern", "internship"])
    # At least one keyword must appear in the title or description (empty = no keyword check).
    keywords: list[str] = Field(default_factory=list)
    # Location substrings, e.g. "Seattle", "United States", "Canada" (empty = anywhere).
    locations: list[str] = Field(default_factory=list)
    workplace: list[Workplace] = Field(default_factory=lambda: ["remote", "hybrid", "onsite"])
    max_age_days: int = 21


class SubmitSettings(BaseModel):
    # Fill forms and screenshot them without pressing submit. Turn off once you trust it.
    dry_run: bool = True
    headless: bool = False
    min_delay_s: float = 20
    max_delay_s: float = 60
    # How long to wait for you to solve a CAPTCHA in the open browser window.
    manual_wait_s: int = 300


class Settings(BaseModel):
    llm: LLMSettings = Field(default_factory=LLMSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    submit: SubmitSettings = Field(default_factory=SubmitSettings)
    # Maximum submissions per day, per source.
    caps: dict[str, int] = Field(
        default_factory=lambda: {"greenhouse": 15, "lever": 15, "ashby": 15, "linkedin": 10}
    )
    min_score: int = 60
    max_scored_per_run: int = 40
    schedule_time: str = "08:00"
    dashboard_port: int = 8765
    browser_executable: str | None = None


def load_settings() -> Settings:
    path = paths.settings_file()
    if not path.exists():
        return Settings()
    return Settings.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def save_settings(settings: Settings) -> None:
    paths.settings_file().write_text(
        yaml.safe_dump(settings.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
