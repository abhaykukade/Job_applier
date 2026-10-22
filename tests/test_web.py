from fastapi.testclient import TestClient
from sqlmodel import select

from job_applier import db
from job_applier.db import Company, Job, Question
from job_applier.pipeline.run import run_daily
from job_applier.web.app import app

from .test_pipeline import seeded  # noqa: F401  (fixture)


def test_pages_render_empty():
    web = TestClient(app)
    for path in ("/", "/questions", "/companies", "/history", "/runs", "/setup"):
        resp = web.get(path)
        assert resp.status_code == 200, path
    assert "Import your resume" in web.get("/").text


def test_review_flow(seeded, fake_llm, client):  # noqa: F811
    run_daily(llm=fake_llm, client=client, submit=False)
    web = TestClient(app)
    assert "Waiting for your answers" in web.get("/").text
    page = web.get("/questions").text
    assert "Do you have real experience with Kubernetes?" in page

    with db.session() as s:
        job = s.exec(select(Job).where(Job.ats == "greenhouse", Job.status == "needs_input")).first()
        q = s.exec(select(Question).where(Question.key == "claim:kubernetes")).first()
    detail = web.get(f"/jobs/{job.id}").text
    assert "Suggested resume edits" in detail and "Kubernetes" in detail

    resp = web.post(f"/questions/{q.id}", data={"answer": "Yes", "reuse": "on", "back": "https://evil.example"}, follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"].startswith("/questions")

    resp = web.post(f"/jobs/{job.id}/answers", data={"field:question_105": "@jordan"}, follow_redirects=False)
    assert resp.status_code == 303
    with db.session() as s:
        assert s.get(Job, job.id).answers["question_105"] == "@jordan"

    web.post(f"/jobs/{job.id}/skip")
    with db.session() as s:
        assert s.get(Job, job.id).status == "skipped"


def test_add_company_with_explicit_token():
    web = TestClient(app)
    web.post("/companies", data={"name": "Acme", "ats": "greenhouse", "token": "acme"})
    web.post("/companies", data={"name": "Acme", "ats": "greenhouse", "token": "acme"})
    with db.session() as s:
        assert len(s.exec(select(Company)).all()) == 1


def test_settings_roundtrip():
    web = TestClient(app)
    web.post("/setup/settings", data={
        "titles_include": "Backend Engineer\nPlatform Engineer", "locations": "United States",
        "workplace": ["remote", "hybrid"], "min_score": "70", "cap_greenhouse": "5", "dry_run": "on",
        "llm_provider": "anthropic", "llm_model": "claude-opus-5-5", "llm_effort": "medium",
    })
    from job_applier.settings import load_settings

    s = load_settings()
    assert s.search.titles_include == ["Backend Engineer", "Platform Engineer"]
    assert s.search.workplace == ["remote", "hybrid"]
    assert s.min_score == 70 and s.caps["greenhouse"] == 5 and s.submit.dry_run


def test_profile_form_roundtrip():
    web = TestClient(app)
    web.post("/setup/profile", data={
        "first_name": "Jordan", "last_name": "Lee", "authorized_countries": "United States, Canada",
        "requires_sponsorship": "no", "years_experience": "8", "eeo_gender": "Decline to self-identify",
    })
    from job_applier.profile import store

    p = store.load_profile()
    assert p.authorized_countries == ["United States", "Canada"]
    assert p.requires_sponsorship is False and p.years_experience == 8 and p.willing_to_relocate is None


def test_flash_message_survives_fragment_redirect():
    web = TestClient(app)
    resp = web.post("/setup/resume-yaml", data={"content": "basics: {name: Jordan}"}, follow_redirects=False)
    assert resp.headers["location"] == "/setup?msg=Resume%20saved#resume"
