"""Lever: public Postings API for listings; the apply page is plain HTML we can parse."""

from __future__ import annotations

import httpx

from job_applier.ats.base import ATSError, BaseATS, JobPosting, html_to_text, infer_workplace, parse_dt
from job_applier.forms.html_parser import parse_form
from job_applier.forms.models import FormField

API = "https://api.lever.co/v0/postings"


class Lever(BaseATS):
    name = "lever"
    submit_selectors = ("#btn-submit", 'button[type="submit"]')

    def list_jobs(self, token: str, company_name: str) -> list[JobPosting]:
        data = self._get_json(f"{API}/{token}", params={"mode": "json"})
        if not isinstance(data, list):
            raise ATSError(f"lever: unexpected response for {token}")
        postings = []
        for job in data:
            cats = job.get("categories") or {}
            location = cats.get("location") or ", ".join(cats.get("allLocations") or [])
            parts = [job.get("descriptionPlain") or html_to_text(job.get("description"))]
            for section in job.get("lists") or []:
                parts.append(f"{section.get('text', '')}\n{html_to_text(section.get('content'))}")
            parts.append(job.get("additionalPlain") or "")
            hosted = job.get("hostedUrl", "")
            postings.append(
                JobPosting(
                    ats=self.name,
                    company_token=token,
                    company_name=company_name,
                    external_id=job["id"],
                    title=job.get("text", "").strip(),
                    location=location or "",
                    workplace=infer_workplace(location or "", job.get("text", ""), job.get("workplaceType")),
                    url=hosted,
                    apply_url=job.get("applyUrl") or f"{hosted}/apply",
                    description="\n\n".join(p for p in parts if p).strip(),
                    posted_at=parse_dt(job.get("createdAt")),
                )
            )
        return postings

    def get_form(self, token: str, external_id: str, apply_url: str) -> list[FormField]:
        try:
            resp = self.http.get(apply_url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ATSError(f"lever: cannot load apply page: {exc}") from exc
        fields = parse_form(resp.text, "form#application-form, form")
        for f in fields:
            if f.key == "resume":
                f.section = "standard"
                f.required = True
        return fields
