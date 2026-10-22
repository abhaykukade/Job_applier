from pathlib import Path

import pytest
from sqlmodel import select

from job_applier import db
from job_applier.db import Company, Job, JobStatus, Question, Suggestion, SuggestionStatus
from job_applier.pipeline import review
from job_applier.pipeline.answers import answer_question, open_questions
from job_applier.pipeline.run import run_daily
from job_applier.profile import store
from job_applier.settings import Settings, save_settings

from .conftest import CHROMIUM


@pytest.fixture
def seeded(resume, profile):
    store.save_resume(resume)
    store.save_profile(profile)
    settings = Settings()
    settings.search.titles_include = ["Engineer"]
    settings.search.max_age_days = 0  # fixtures have fixed dates
    save_settings(settings)
    with db.session() as s:
        s.add(Company(name="Acme", ats="greenhouse", token="acme"))
        s.add(Company(name="Globex", ats="lever", token="globex"))
        s.add(Company(name="Initech", ats="ashby", token="initech"))
        s.commit()


def jobs_by_title():
    with db.session() as s:
        return {(j.company_token, j.title): j for j in s.exec(select(Job))}


def test_daily_run_prepares_and_asks(seeded, fake_llm, client):
    run_daily(llm=fake_llm, client=client, submit=False)
    jobs = jobs_by_title()
    assert jobs[("acme", "Engineering Intern")].status == JobStatus.FILTERED_OUT
    acme = jobs[("acme", "Senior Backend Engineer")]
    assert acme.status == JobStatus.NEEDS_INPUT
    assert acme.answers["question_101"] == "Yes"  # authorized: US listed in profile as "USA"
    assert acme.answers["question_102"] == "No"  # no sponsorship
    assert acme.answers["resume"] == "@resume"
    assert acme.answers["gender"] == "Decline To Self Identify"
    assert "question_105" not in acme.answers  # optional unknown field left blank

    with db.session() as s:
        qs = open_questions(s, acme.id)
        keys = {q.key: q for q in qs}
        assert "claim:kubernetes" in keys
        why = keys["why do you want to work at acme"]
        assert why.draft and why.reusable is False  # job-specific, drafted for review
        go = keys["how many years of professional go experience do you have"]
        assert go.draft is None and go.reusable

        sugs = s.exec(select(Suggestion).where(Suggestion.job_id == acme.id)).all()
        kinds = {x.kind: x for x in sugs}
        assert set(kinds) == {"summary", "skill_add", "bullets_reorder"}  # bad index dropped
        assert kinds["skill_add"].status == SuggestionStatus.BLOCKED

        # One answer to the skill question unblocks every job that asked it.
        n = answer_question(s, keys["claim:kubernetes"].id, "Yes")
        assert n == 4
        assert s.get(Suggestion, kinds["skill_add"].id).status == SuggestionStatus.PENDING
        answer_question(s, go.id, "3")
        answer_question(s, why.id, "I like payments.")

    jobs = jobs_by_title()
    assert jobs[("acme", "Senior Backend Engineer")].status == JobStatus.READY
    assert jobs[("acme", "Senior Backend Engineer")].answers["question_103"] == "3"
    # The second Acme job reused the remembered Go answer but still needs its own "why".
    payments = jobs[("acme", "Senior Backend Engineer, Payments")]
    with db.session() as s:
        assert [q.key for q in open_questions(s, payments.id)] == ["why do you want to work at acme"]
    ashby = jobs[("initech", "Senior Software Engineer")]
    assert ashby.status == JobStatus.READY
    assert ashby.answers["q_sponsor"] == "No"

    # Lever: the checkbox and office questions are held for you.
    lever = jobs[("globex", "Staff Python Engineer")]
    with db.session() as s:
        prompts = sorted(q.prompt for q in open_questions(s, lever.id) if q.kind == "form")
    assert prompts == ["Preferred office", "Which of these have you used?"]


def test_second_run_does_not_duplicate(seeded, fake_llm, client):
    run_daily(llm=fake_llm, client=client, submit=False)
    count = len(jobs_by_title())
    calls = len(fake_llm.calls)
    run_daily(llm=fake_llm, client=client, submit=False)
    assert len(jobs_by_title()) == count
    assert len(fake_llm.calls) == calls  # nothing new to score


def test_low_score_is_parked(seeded, client):
    from .conftest import FakeLLM

    run_daily(llm=FakeLLM(score=30), client=client, submit=False)
    statuses = {j.status for j in jobs_by_title().values()}
    assert statuses == {JobStatus.FILTERED_OUT, JobStatus.LOW_SCORE}


def test_claim_no_rejects_suggestion(seeded, fake_llm, client):
    run_daily(llm=fake_llm, client=client, submit=False)
    with db.session() as s:
        q = s.exec(select(Question).where(Question.key == "claim:kubernetes")).first()
        answer_question(s, q.id, "No")
        assert {x.status for x in s.exec(select(Suggestion).where(Suggestion.kind == "skill_add"))} == {SuggestionStatus.REJECTED}


@pytest.mark.skipif(not CHROMIUM.exists() and "JOBAPPLIER_CHROMIUM" not in __import__("os").environ, reason="no Chromium")
def test_approve_renders_tailored_resume(seeded, fake_llm, client):
    run_daily(llm=fake_llm, client=client, submit=False)
    with db.session() as s:
        q = s.exec(select(Question).where(Question.key == "claim:kubernetes")).first()
        answer_question(s, q.id, "Yes")
        job = s.exec(select(Job).where(Job.ats == "ashby")).one()
        review.accept_all(s, job.id)
        approved = review.approve(s, job.id)
        assert approved.status == JobStatus.APPROVED
        pdf = Path(approved.resume_path)
        assert pdf.exists() and pdf.name == "Jordan_Lee_Resume.pdf"
        assert pdf.with_suffix(".docx").exists()
        tailored = review.tailored_resume(s, approved)
    assert "Kubernetes" in tailored.skills[0].items
    assert tailored.experience[0].bullets[0] == "Ran the on-call rotation"
    assert "Kubernetes" not in store.load_resume().skills[0].items


def test_cannot_approve_with_open_questions(seeded, fake_llm, client):
    run_daily(llm=fake_llm, client=client, submit=False)
    with db.session() as s:
        job = s.exec(select(Job).where(Job.status == JobStatus.NEEDS_INPUT)).first()
        with pytest.raises(review.ReviewError):
            review.approve(s, job.id)
