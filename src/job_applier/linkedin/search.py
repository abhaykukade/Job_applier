"""Find jobs through LinkedIn's public (logged-out) job pages.

Searching never uses your account, so it can't put the account at risk; only
applying happens in your logged-in browser.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from job_applier.ats.base import html_to_text, make_client
from job_applier.settings import LinkedInSettings

GUEST = "https://www.linkedin.com/jobs-guest/jobs/api"
WORKPLACE_CODES = {"onsite": "1", "remote": "2", "hybrid": "3"}
PAUSE = (1.5, 4.0)  # seconds between requests, to stay well under LinkedIn's limits


class RateLimited(RuntimeError):
    pass


@dataclass
class Card:
    job_id: str
    title: str
    company: str
    location: str
    url: str
    posted_at: datetime | None


@dataclass
class Detail:
    description: str
    apply_url: str | None  # company-site URL; None means Easy Apply (or apply on LinkedIn)
    seniority: str = ""
    employment_type: str = ""


def parse_cards(html: str) -> list[Card]:
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for card in soup.select("[data-entity-urn*='jobPosting']"):
        job_id = card["data-entity-urn"].rsplit(":", 1)[-1]
        title = card.select_one(".base-search-card__title")
        company = card.select_one(".base-search-card__subtitle")
        location = card.select_one(".job-search-card__location")
        link = card.select_one("a.base-card__full-link") or card.find("a", href=True)
        when = card.select_one("time[datetime]")
        posted = None
        if when is not None:
            try:
                posted = datetime.fromisoformat(when["datetime"]).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        cards.append(
            Card(
                job_id=job_id,
                title=title.get_text(" ", strip=True) if title else "",
                company=company.get_text(" ", strip=True) if company else "",
                location=location.get_text(" ", strip=True) if location else "",
                url=(link["href"].split("?")[0] if link else f"https://www.linkedin.com/jobs/view/{job_id}/"),
                posted_at=posted,
            )
        )
    return cards


def parse_detail(html: str) -> Detail:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
    apply_url = None
    code = soup.select_one("code#applyUrl")
    if code is not None:
        raw = code.decode_contents()
        match = re.search(r'"(https?://[^"]+)"', raw)
        if match:
            link = match.group(1).replace("&amp;", "&")
            target = parse_qs(urlparse(link).query).get("url")
            apply_url = unquote(target[0]) if target else link
    criteria = {}
    for item in soup.select(".description__job-criteria-item"):
        head = item.select_one(".description__job-criteria-subheader")
        value = item.select_one(".description__job-criteria-text")
        if head and value:
            criteria[head.get_text(strip=True).lower()] = value.get_text(strip=True)
    return Detail(
        description=html_to_text(str(body)) if body else "",
        apply_url=apply_url,
        seniority=criteria.get("seniority level", ""),
        employment_type=criteria.get("employment type", ""),
    )


class LinkedInSearch:
    def __init__(self, client: httpx.Client | None = None, pause: tuple[float, float] | None = None):
        self.http = client or make_client()
        self.pause = pause or PAUSE

    def _get(self, url: str, params: dict | None = None) -> str:
        time.sleep(random.uniform(*self.pause))
        resp = self.http.get(url, params=params)
        if resp.status_code == 429:
            raise RateLimited("LinkedIn is rate limiting searches; try again later")
        if resp.status_code >= 400:
            return ""
        return resp.text

    def search(self, query: str, location: str, cfg: LinkedInSettings, workplace: list[str], page: int) -> list[Card]:
        params = {"keywords": query, "f_TPR": cfg.posted_within, "start": page * 25}
        if location:
            params["location"] = location
        codes = [WORKPLACE_CODES[w] for w in workplace if w in WORKPLACE_CODES]
        if codes and len(codes) < 3:
            params["f_WT"] = ",".join(codes)
        if cfg.experience_levels:
            params["f_E"] = ",".join(str(e) for e in cfg.experience_levels)
        return parse_cards(self._get(f"{GUEST}/seeMoreJobPostings/search", params))

    def detail(self, job_id: str) -> Detail | None:
        html = self._get(f"{GUEST}/jobPosting/{job_id}")
        return parse_detail(html) if html else None
