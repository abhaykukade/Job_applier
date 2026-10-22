"""Registry of supported ATS integrations and company auto-detection."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from job_applier.ats.ashby import Ashby
from job_applier.ats.base import ATSError, BaseATS, JobPosting, make_client
from job_applier.ats.greenhouse import Greenhouse
from job_applier.ats.lever import Lever

__all__ = ["ATSError", "BaseATS", "JobPosting", "SUPPORTED", "detect_company", "get_ats", "parse_board_url"]

SUPPORTED = ("greenhouse", "lever", "ashby")
_CLASSES: dict[str, type[BaseATS]] = {"greenhouse": Greenhouse, "lever": Lever, "ashby": Ashby}


def get_ats(name: str, client: httpx.Client | None = None) -> BaseATS:
    try:
        return _CLASSES[name](client)
    except KeyError:
        raise ATSError(f"Unsupported ATS {name!r}") from None


def parse_board_url(url: str) -> tuple[str, str] | None:
    """Map a careers-board URL to (ats, token)."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = parsed.netloc.lower()
    parts = [p for p in parsed.path.split("/") if p]
    if "greenhouse.io" in host:
        if parts and parts[0] != "embed":
            return "greenhouse", parts[0]
        token = re.search(r"for=([\w-]+)", parsed.query)
        return ("greenhouse", token.group(1)) if token else None
    if host.endswith("lever.co") and parts:
        return "lever", parts[0]
    if host.endswith("ashbyhq.com") and parts:
        return "ashby", parts[0]
    return None


def _token_candidates(name: str) -> list[str]:
    base = name.strip().lower()
    base = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|technologies|labs)\b\.?", "", base).strip()
    words = re.findall(r"[a-z0-9]+", base)
    candidates = ["".join(words), "-".join(words), "_".join(words)]
    if words:
        candidates.append(words[0])
    seen: list[str] = []
    for c in candidates:
        if c and c not in seen:
            seen.append(c)
    return seen


def detect_company(name_or_url: str, client: httpx.Client | None = None) -> list[tuple[str, str]]:
    """Find which supported ATS boards exist for a company name or careers URL."""
    from_url = parse_board_url(name_or_url) if ("/" in name_or_url or "." in name_or_url) else None
    if from_url:
        return [from_url]
    client = client or make_client()
    found = []
    for ats_name in SUPPORTED:
        ats = get_ats(ats_name, client)
        for token in _token_candidates(name_or_url):
            if ats.board_exists(token):
                found.append((ats_name, token))
                break
    return found
