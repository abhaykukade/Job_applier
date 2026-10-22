"""Common pieces for applicant-tracking-system integrations."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from job_applier.forms.models import FormField

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) job-applier/0.1 (personal job search)"


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}, transport=transport
    )


@dataclass
class JobPosting:
    ats: str
    company_token: str
    company_name: str
    external_id: str
    title: str
    location: str
    workplace: str
    url: str
    apply_url: str
    description: str
    posted_at: datetime | None


class ATSError(RuntimeError):
    pass


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    soup = BeautifulSoup(html.unescape(raw), "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(["p", "li", "h1", "h2", "h3", "h4", "div"]):
        block.insert_after("\n")
    text = soup.get_text()
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def infer_workplace(location: str, title: str = "", hint: str | None = None) -> str:
    if hint:
        h = hint.lower().replace("-", "").replace(" ", "")
        if h in ("remote", "hybrid", "onsite"):
            return h
    text = f"{location} {title}".lower()
    if "hybrid" in text:
        return "hybrid"
    if "remote" in text or "anywhere" in text:
        return "remote"
    return "onsite" if location.strip() else "unknown"


def parse_dt(value: str | int | float | None) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class BaseATS:
    name = "base"
    # Words on the page after submitting that mean it worked.
    success_patterns = (
        r"thank(s| you) for (applying|your application|your interest)",
        r"application (has been |was )?(submitted|received)",
        r"we('ve| have) received your application",
        r"successfully (submitted|applied)",
    )
    submit_selectors: tuple[str, ...] = ('button[type="submit"]', 'input[type="submit"]')

    def __init__(self, client: httpx.Client | None = None):
        self.http = client or make_client()

    def _get_json(self, url: str, **kwargs):
        try:
            resp = self.http.get(url, **kwargs)
        except httpx.HTTPError as exc:
            raise ATSError(f"{self.name}: request failed: {exc}") from exc
        if resp.status_code == 404:
            raise ATSError(f"{self.name}: not found ({url})")
        if resp.status_code >= 400:
            raise ATSError(f"{self.name}: HTTP {resp.status_code} for {url}")
        return resp.json()

    # Subclasses implement these.
    def list_jobs(self, token: str, company_name: str) -> list[JobPosting]:
        raise NotImplementedError

    def get_form(self, token: str, external_id: str, apply_url: str) -> list[FormField]:
        raise NotImplementedError

    def board_exists(self, token: str) -> bool:
        try:
            return bool(self.list_jobs(token, token))
        except ATSError:
            return False

    @staticmethod
    def needs_cover_letter(fields: list[FormField]) -> bool:
        return any(f.required and "cover" in f"{f.key} {f.label}".lower() for f in fields)
