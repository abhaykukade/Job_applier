"""Greenhouse: public Job Board API for listings and application questions."""

from __future__ import annotations

from job_applier.ats.base import BaseATS, JobPosting, html_to_text, infer_workplace, parse_dt
from job_applier.forms.models import FieldType, FormField, Option

API = "https://boards-api.greenhouse.io/v1/boards"

TYPE_MAP: dict[str, FieldType] = {
    "input_text": "text",
    "textarea": "textarea",
    "input_file": "file",
    "multi_value_single_select": "select",
    "multi_value_multi_select": "multi_select",
}
STANDARD = {"first_name", "last_name", "email", "phone", "resume", "cover_letter"}


class Greenhouse(BaseATS):
    name = "greenhouse"

    def list_jobs(self, token: str, company_name: str) -> list[JobPosting]:
        data = self._get_json(f"{API}/{token}/jobs", params={"content": "true"})
        postings = []
        for job in data.get("jobs", []):
            location = (job.get("location") or {}).get("name", "") or ""
            postings.append(
                JobPosting(
                    ats=self.name,
                    company_token=token,
                    company_name=job.get("company_name") or company_name,
                    external_id=str(job["id"]),
                    title=job.get("title", "").strip(),
                    location=location,
                    workplace=infer_workplace(location, job.get("title", "")),
                    url=job.get("absolute_url", ""),
                    apply_url=self.apply_url(token, str(job["id"])),
                    description=html_to_text(job.get("content")),
                    posted_at=parse_dt(job.get("first_published") or job.get("updated_at")),
                )
            )
        return postings

    @staticmethod
    def apply_url(token: str, external_id: str) -> str:
        # The hosted board works even when the company embeds Greenhouse on its own site.
        return f"https://job-boards.greenhouse.io/{token}/jobs/{external_id}"

    def get_form(self, token: str, external_id: str, apply_url: str) -> list[FormField]:
        data = self._get_json(f"{API}/{token}/jobs/{external_id}", params={"questions": "true"})
        fields: list[FormField] = []
        for q in data.get("questions", []) + data.get("location_questions", []):
            field = self._question(q, "custom")
            if field:
                fields.append(field)
        for block in data.get("compliance", []) or []:
            for q in block.get("questions", []):
                field = self._question(q, "eeo")
                if field:
                    fields.append(field)
        return fields

    def _question(self, q: dict, section: str) -> FormField | None:
        candidates = [f for f in q.get("fields", []) if f.get("type") != "input_hidden"]
        if not candidates:
            return None
        # "Resume" offers a file input and a paste-text box; prefer the file upload.
        chosen = next((f for f in candidates if f.get("type") == "input_file"), candidates[0])
        name = chosen["name"]
        options = [
            Option(label=str(v.get("label", "")), value=str(v.get("value", "")))
            for v in chosen.get("values", []) or []
        ]
        ftype = TYPE_MAP.get(chosen.get("type", ""), "text")
        if name == "email":
            ftype = "email"
        elif name == "phone":
            ftype = "phone"
        return FormField(
            key=name,
            label=q.get("label", name),
            type=ftype,
            required=bool(q.get("required")),
            options=options,
            section="standard" if name in STANDARD else section,  # type: ignore[arg-type]
            description=html_to_text(q.get("description")),
            meta={"selector": f'[id="{name}"]'},
        )
