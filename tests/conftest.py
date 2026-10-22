from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from job_applier.ats.base import make_client
from job_applier.pipeline import ai
from job_applier.profile.models import Basics, EEO, Experience, Link, Profile, Resume, SkillGroup

FIXTURES = Path(__file__).parent / "fixtures"
CHROMIUM = Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
if CHROMIUM.exists() and "JOBAPPLIER_CHROMIUM" not in os.environ:
    os.environ["JOBAPPLIER_CHROMIUM"] = str(CHROMIUM)


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBAPPLIER_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


def routes(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "boards-api.greenhouse.io/v1/boards/acme/jobs/" in url:
        return httpx.Response(200, json=json.loads(fixture_text("greenhouse_questions.json")))
    if "boards-api.greenhouse.io/v1/boards/acme/jobs" in url:
        return httpx.Response(200, json=json.loads(fixture_text("greenhouse_jobs.json")))
    if "api.lever.co/v0/postings/globex" in url:
        return httpx.Response(200, json=json.loads(fixture_text("lever_postings.json")))
    if "jobs.lever.co/globex/" in url and url.endswith("/apply"):
        return httpx.Response(200, text=fixture_text("lever_apply.html"))
    if "api.ashbyhq.com/posting-api/job-board/initech" in url:
        return httpx.Response(200, json=json.loads(fixture_text("ashby_jobs.json")))
    if "jobs.ashbyhq.com/api/non-user-graphql" in url:
        return httpx.Response(200, json=json.loads(fixture_text("ashby_form.json")))
    return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def client() -> httpx.Client:
    return make_client(httpx.MockTransport(routes))


@pytest.fixture
def resume() -> Resume:
    return Resume(
        basics=Basics(
            name="Jordan Lee", email="jordan@example.com", phone="+1 206 555 0100", location="Seattle, WA",
            links=[Link(label="LinkedIn", url="https://linkedin.com/in/jordanlee")],
        ),
        summary="Backend engineer with 8 years building Python services.",
        skills=[SkillGroup(name="Languages", items=["Python", "SQL", "Bash"])],
        experience=[
            Experience(
                company="Initrode", title="Senior Software Engineer", start="2021", end="",
                bullets=["Built payment APIs in Python serving 2M requests/day", "Ran the on-call rotation"],
            ),
            Experience(company="Hooli", title="Software Engineer", start="2017", end="2021", bullets=["Wrote ETL jobs"]),
        ],
    )


@pytest.fixture
def profile() -> Profile:
    return Profile(
        first_name="Jordan", last_name="Lee", email="jordan@example.com", phone="+1 206 555 0100",
        city="Seattle", state="WA", country="United States", linkedin_url="https://linkedin.com/in/jordanlee",
        current_company="Initrode", years_experience=8, authorized_countries=["USA"],
        requires_sponsorship=False, eeo=EEO(),
    )


class FakeLLM:
    """Answers each structured request from a per-model handler."""

    def __init__(self, score: int = 85):
        self.calls: list[str] = []
        self.score = score

    def structured(self, system, prompt, output):
        self.calls.append(output.__name__)
        if output is ai.MatchResult:
            return ai.MatchResult(score=self.score, summary="Strong fit", strengths=["Python"], gaps=["Kubernetes"], dealbreakers=[])
        if output is ai.TailorResult:
            return ai.TailorResult(edits=[
                ai.TailorEdit(kind="summary", experience_index=None, bullet_index=None, skill_group_index=None,
                              new_text="Backend engineer with 8 years building high-scale Python APIs.",
                              new_order=None, rationale="Mirror the posting", claim=None),
                ai.TailorEdit(kind="skill_add", experience_index=None, bullet_index=None, skill_group_index=0,
                              new_text="Kubernetes", new_order=None, rationale="Required skill", claim="Kubernetes"),
                ai.TailorEdit(kind="bullets_reorder", experience_index=0, bullet_index=None, skill_group_index=None,
                              new_text=None, new_order=[1, 0], rationale="Lead with ops", claim=None),
                ai.TailorEdit(kind="bullet", experience_index=5, bullet_index=0, skill_group_index=None,
                              new_text="bad index", new_order=None, rationale="", claim=None),
            ])
        if output is ai.CoverLetter:
            return ai.CoverLetter(text="Dear team,\n\nI'd love to join.\n\nJordan Lee")
        if output is ai.DraftAnswer:
            if "why" in prompt.lower().split("question:")[-1]:
                return ai.DraftAnswer(answer="Acme's payments work matches my background.", job_specific=True)
            return ai.DraftAnswer(answer=None, job_specific=False)
        if output is ai.SearchSuggestion:
            return ai.SearchSuggestion(titles=["Backend Engineer"], keywords=["Python"], seniority=["Senior"])
        raise AssertionError(f"unexpected request for {output.__name__}")


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()
