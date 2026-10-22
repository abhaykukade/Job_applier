"""Cheap rule-based filters that run before any LLM call."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from job_applier.db import utcnow
from job_applier.settings import SearchSettings


def _has_word(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase.lower())}(?![a-z0-9])", text.lower()) is not None


def _location_ok(location: str, workplace: str, cfg: SearchSettings) -> bool:
    if not cfg.locations:
        return True
    if any(loc.lower() in location.lower() for loc in cfg.locations):
        return True
    # A remote role with no region restriction ("Remote", "Anywhere") is fine anywhere.
    if workplace == "remote":
        stripped = re.sub(r"(?i)\b(remote|anywhere|worldwide|global|fully)\b|[-–,/()|;]", " ", location)
        return not stripped.strip()
    return False


def check(
    title: str,
    description: str,
    location: str,
    workplace: str,
    posted_at: datetime | None,
    cfg: SearchSettings,
    check_location: bool = True,
) -> str | None:
    """Return why the job is filtered out, or None if it passes."""
    if cfg.titles_include and not any(_has_word(title, t) for t in cfg.titles_include):
        return "title not in your target titles"
    for t in cfg.titles_exclude:
        if _has_word(title, t):
            return f"title contains excluded '{t}'"
    if cfg.seniority_include and not any(_has_word(title, s) for s in cfg.seniority_include):
        return "seniority not in your target levels"
    for s in cfg.seniority_exclude:
        if _has_word(title, s):
            return f"seniority '{s}' excluded"
    if cfg.keywords and not any(_has_word(f"{title}\n{description}", k) for k in cfg.keywords):
        return "none of your keywords appear"
    if workplace != "unknown" and workplace not in cfg.workplace:
        return f"{workplace} roles excluded"
    if check_location and not _location_ok(location, workplace, cfg):
        return f"location '{location}' not in your locations"
    if posted_at and cfg.max_age_days and posted_at < utcnow() - timedelta(days=cfg.max_age_days):
        return f"posted more than {cfg.max_age_days} days ago"
    return None
