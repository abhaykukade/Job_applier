import os
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright
from sqlmodel import select

from job_applier import db
from job_applier.db import Company, Job, JobStatus, Question
from job_applier.forms.mapping import RESUME, Context
from job_applier.linkedin import easy_apply as ea
from job_applier.linkedin import search
from job_applier.linkedin.discovery import discover_linkedin
from job_applier.linkedin.search import parse_cards, parse_detail
from job_applier.pipeline.answers import answer_question
from job_applier.pipeline.run import RunLog, run_daily
from job_applier.profile import store
from job_applier.settings import Settings, load_settings, save_settings

from .conftest import FIXTURES, fixture_text

LINKEDIN_PAGE = (FIXTURES / "linkedin_job.html").as_uri()
needs_browser = pytest.mark.skipif("JOBAPPLIER_CHROMIUM" not in os.environ, reason="no Chromium available")

YEARS_Q = "how many years of work experience do you have with python"
HYBRID_Q = "are you comfortable working in a hybrid setting"


@pytest.fixture(autouse=True)
def no_pauses(monkeypatch):
    monkeypatch.setattr(search, "PAUSE", (0, 0))
    monkeypatch.setattr(ea, "_pause", lambda lo=0, hi=0: 50)


# ---------- public search parsing & discovery ----------

def test_parse_cards_and_details():
    cards = parse_cards(fixture_text("linkedin_search.html"))
    assert [c.job_id for c in cards] == ["3900000111", "3900000222", "3900000333", "3900000444"]
    first = cards[0]
    assert (first.title, first.company, first.location) == ("Senior Backend Engineer", "Umbrella", "Seattle, WA")
    assert first.url == "https://www.linkedin.com/jobs/view/senior-backend-engineer-at-umbrella-3900000111"
    assert first.posted_at.year == 2026

    easy = parse_detail(fixture_text("linkedin_detail_easy.html"))
    assert easy.apply_url is None and "6+ years Python" in easy.description
    assert easy.seniority == "Mid-Senior level"
    gh = parse_detail(fixture_text("linkedin_detail_greenhouse.html"))
    assert gh.apply_url == "https://boards.greenhouse.io/newco/jobs/555"


def _settings() -> Settings:
    s = Settings()
    s.search.titles_include = ["Engineer"]
    s.search.max_age_days = 0
    s.linkedin.enabled = True
    s.linkedin.locations = ["United States"]
    return s


def test_discovery_routes_each_job(client):
    log = RunLog("test")
    with db.session() as s:
        added = discover_linkedin(s, _settings(), log, client)
        jobs = {j.external_id: j for j in s.exec(select(Job))}
        companies = s.exec(select(Company)).all()
    assert added == 2
    assert jobs["3900000111"].ats == "linkedin" and jobs["3900000111"].status == JobStatus.NEW
    assert jobs["3900000111"].apply_url == "https://www.linkedin.com/jobs/view/3900000111/"
    assert "Postgres" in jobs["3900000111"].description
    # Greenhouse-hosted: the company is watched instead; the LinkedIn copy is parked.
    assert [(c.ats, c.token, c.name) for c in companies] == [("greenhouse", "newco", "NewCo")]
    assert jobs["3900000222"].status == JobStatus.FILTERED_OUT
    # Workday: kept for you to apply yourself.
    assert jobs["3900000333"].ats == "external" and "myworkdayjobs" in jobs["3900000333"].apply_url
    assert "3900000444" not in jobs  # intern filtered before fetching details

    with db.session() as s:
        assert discover_linkedin(s, _settings(), log, client) == 0  # nothing refetched


def test_disabled_does_nothing(client):
    s = _settings()
    s.linkedin.enabled = False
    with db.session() as ses:
        assert discover_linkedin(ses, s, RunLog("test"), client) == 0


def test_daily_run_with_linkedin(client, fake_llm, resume, profile):
    store.save_resume(resume)
    store.save_profile(profile)
    save_settings(_settings())
    run_daily(llm=fake_llm, client=client, submit=False)
    with db.session() as s:
        jobs = {j.external_id: j for j in s.exec(select(Job).where(Job.ats.in_(["linkedin", "external"])))}
    easy, workday = jobs["3900000111"], jobs["3900000333"]
    # Location "Seattle, WA" isn't re-filtered against "United States": LinkedIn did that already.
    assert easy.status == JobStatus.NEEDS_INPUT  # only the Kubernetes claim question
    assert easy.form_fields is None
    assert workday.status == JobStatus.APPLY_YOURSELF
    assert "bigcorp.wd5.myworkdayjobs.com" in workday.status_reason


def test_session_checks():
    with pytest.raises(ea.NotLoggedIn):
        ea.check_session(SimpleNamespace(url="https://www.linkedin.com/authwall?trk=x"))
    with pytest.raises(ea.SecurityCheck):
        ea.check_session(SimpleNamespace(url="https://www.linkedin.com/checkpoint/challenge/abc"))
    ea.check_session(SimpleNamespace(url="https://www.linkedin.com/jobs/view/1/"))


# ---------- Easy Apply against the stand-in page ----------

@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ["JOBAPPLIER_CHROMIUM"])
        yield browser.new_page()
        browser.close()


@pytest.fixture
def files(tmp_path):
    path = tmp_path / "Jordan_Lee_Resume.pdf"
    path.write_bytes(b"%PDF-1.4\n%fake\n")
    return {RESUME: path}


@needs_browser
def test_easy_apply_submits(page, profile, files):
    bank = {YEARS_Q: "6", HYBRID_Q: "Yes"}
    attempt = ea.easy_apply(page, LINKEDIN_PAGE, Context(profile, bank, "Seattle, WA"), {}, files, dry_run=False)
    assert attempt.result == ea.Result.SUBMITTED, attempt.message
    sent = page.evaluate("window.submitted")
    assert sent == {
        "phone": "+1 206 555 0100", "city": "Seattle, Washington, United States", "resume": "Jordan_Lee_Resume.pdf",
        "years": "6", "sponsor": "No", "hybrid": "Yes", "follow": False,
    }
    assert attempt.used[YEARS_Q] == "6"
    assert attempt.used["email address"] == "jordan@example.com"


@needs_browser
def test_easy_apply_holds_unknown_question(page, profile, files):
    attempt = ea.easy_apply(page, LINKEDIN_PAGE, Context(profile, {HYBRID_Q: "Yes"}, ""), {}, files, dry_run=False)
    assert attempt.result == ea.Result.HOLD
    assert [f.label for f in attempt.unresolved] == ["How many years of work experience do you have with Python?"]
    assert page.evaluate("window.discarded") is True
    assert page.evaluate("window.submitted") is None


@needs_browser
def test_easy_apply_job_answers_override(page, profile, files):
    answers = {YEARS_Q: "4", HYBRID_Q: "No"}  # e.g. from answering held questions
    attempt = ea.easy_apply(page, LINKEDIN_PAGE, Context(profile, {}, ""), answers, files, dry_run=False)
    assert attempt.result == ea.Result.SUBMITTED, attempt.message
    assert page.evaluate("window.submitted.years") == "4"
    assert page.evaluate("window.submitted.hybrid") == "No"


@needs_browser
def test_easy_apply_dry_run(page, profile, files):
    bank = {YEARS_Q: "6", HYBRID_Q: "Yes"}
    attempt = ea.easy_apply(page, LINKEDIN_PAGE, Context(profile, bank, ""), {}, files, dry_run=True)
    assert attempt.result == ea.Result.DRY_RUN
    assert page.evaluate("window.submitted") is None


@needs_browser
def test_held_job_returns_to_queue_and_submits(profile, resume, files):
    """Full loop: approved → LinkedIn asks something new → you answer → submitted next time."""
    store.save_resume(resume)
    store.save_profile(profile)
    s = load_settings()
    s.submit.dry_run, s.submit.headless = False, True
    s.linkedin.min_delay_s = s.linkedin.max_delay_s = 0
    save_settings(s)
    with db.session() as ses:
        ses.add(db.AnswerBankEntry(key=HYBRID_Q, question="hybrid?", answer="Yes"))
        job = Job(
            ats="linkedin", company_token="linkedin", company_name="Umbrella", external_id="3900000111",
            title="Senior Backend Engineer", apply_url=LINKEDIN_PAGE, status=JobStatus.APPROVED,
            approved_at=db.utcnow(), resume_path=str(files[RESUME]), answers={},
        )
        ses.add(job)
        ses.commit()
        job_id = job.id

    from job_applier.pipeline.submit import submit_approved

    counts = submit_approved(load_settings(), RunLog("test"))
    assert counts["held"] == 1
    with db.session() as ses:
        row = ses.get(Job, job_id)
        assert row.status == JobStatus.NEEDS_INPUT
        q = ses.exec(select(Question).where(Question.job_id == job_id)).one()
        assert q.field_key == YEARS_Q
        answer_question(ses, q.id, "6")
        assert ses.get(Job, job_id).status == JobStatus.APPROVED  # back in the queue, no re-approval

    counts = submit_approved(load_settings(), RunLog("test"))
    assert counts["submitted"] == 1
    with db.session() as ses:
        row = ses.get(Job, job_id)
        assert row.status == JobStatus.SUBMITTED
        labels = {f["key"] for f in row.form_fields}
        assert YEARS_Q in labels and row.answers[YEARS_Q] == "6"
