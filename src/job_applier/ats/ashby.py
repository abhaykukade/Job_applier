"""Ashby: public posting API for listings; the hosted job page's GraphQL endpoint for the form."""

from __future__ import annotations

import httpx

from job_applier.ats.base import ATSError, BaseATS, JobPosting, html_to_text, infer_workplace, parse_dt
from job_applier.forms.models import FieldType, FormField, Option

API = "https://api.ashbyhq.com/posting-api/job-board"
GRAPHQL = "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting"

FORM_QUERY = """
query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {
  jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) {
    id
    title
    applicationForm {
      sections {
        title
        fieldEntries {
          ... on FormFieldEntry { id field isRequired descriptionHtml }
        }
      }
    }
  }
}
"""

TYPE_MAP: dict[str, FieldType] = {
    "String": "text",
    "Email": "email",
    "Phone": "phone",
    "LongText": "textarea",
    "File": "file",
    "Boolean": "boolean",
    "ValueSelect": "select",
    "MultiValueSelect": "multi_select",
    "Date": "date",
    "Number": "number",
    "Location": "location",
    "SocialLink": "url",
}


class Ashby(BaseATS):
    name = "ashby"

    def list_jobs(self, token: str, company_name: str) -> list[JobPosting]:
        data = self._get_json(f"{API}/{token}", params={"includeCompensation": "true"})
        postings = []
        for job in data.get("jobs", []):
            if job.get("isListed") is False:
                continue
            locations = [job.get("location") or ""] + [
                s.get("location", "") for s in job.get("secondaryLocations") or []
            ]
            location = "; ".join(l for l in locations if l)
            hint = "remote" if job.get("isRemote") and not job.get("workplaceType") else job.get("workplaceType")
            postings.append(
                JobPosting(
                    ats=self.name,
                    company_token=token,
                    company_name=company_name,
                    external_id=job["id"],
                    title=job.get("title", "").strip(),
                    location=location,
                    workplace=infer_workplace(location, job.get("title", ""), hint),
                    url=job.get("jobUrl", ""),
                    apply_url=job.get("applyUrl") or f"https://jobs.ashbyhq.com/{token}/{job['id']}/application",
                    description=job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml")),
                    posted_at=parse_dt(job.get("publishedAt")),
                )
            )
        return postings

    def get_form(self, token: str, external_id: str, apply_url: str) -> list[FormField]:
        try:
            resp = self.http.post(
                GRAPHQL,
                json={
                    "operationName": "ApiJobPosting",
                    "variables": {"organizationHostedJobsPageName": token, "jobPostingId": external_id},
                    "query": FORM_QUERY,
                },
            )
            resp.raise_for_status()
            posting = (resp.json().get("data") or {}).get("jobPosting")
        except (httpx.HTTPError, ValueError) as exc:
            raise ATSError(f"ashby: cannot load form: {exc}") from exc
        if not posting or not posting.get("applicationForm"):
            raise ATSError("ashby: job has no application form (closed?)")

        fields = []
        for section in posting["applicationForm"].get("sections", []):
            for entry in section.get("fieldEntries", []):
                spec = entry.get("field") or {}
                path = spec.get("path") or entry.get("id")
                if not path:
                    continue
                ftype = TYPE_MAP.get(spec.get("type", ""), "text")
                options = [
                    Option(label=str(v.get("label", "")), value=str(v.get("value", "")))
                    for v in spec.get("selectableValues") or []
                ]
                if ftype == "boolean" and not options:
                    options = [Option(label="Yes", value="true"), Option(label="No", value="false")]
                label = spec.get("title") or path
                fields.append(
                    FormField(
                        key=path,
                        label=label,
                        type=ftype,
                        required=bool(entry.get("isRequired")),
                        options=options,
                        section="standard" if path.startswith("_systemfield_") else "custom",
                        description=html_to_text(entry.get("descriptionHtml")),
                    )
                )
        return fields
