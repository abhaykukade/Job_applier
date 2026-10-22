"""The daily pipeline: discover → filter → score → prepare (→ submit what you approved)."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

import httpx
from sqlmodel import Session, select

from job_applier import db, paths
from job_applier.ats import ATSError, get_ats
from job_applier.db import Company, Job, JobStatus, Question, Run, Suggestion, SuggestionStatus, utcnow
from job_applier.forms.mapping import Context, normalize, resolve_form
from job_applier.llm import LLM, LLMError, get_llm
from job_applier.pipeline import ai, filters
from job_applier.pipeline.answers import bank, claim_key, refresh_status
from job_applier.profile import store
from job_applier.profile.models import Profile, Resume
from job_applier.resume.tailoring import to_proposed
from job_applier.settings import Settings, load_settings

log = logging.getLogger("job_applier")


class Busy(RuntimeError):
    pass


@contextmanager
def run_lock() -> Iterator[None]:
    """One run at a time, across the dashboard and the scheduled task."""
    lock = paths.data_dir() / "run.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime > 3 * 3600:
        lock.unlink(missing_ok=True)  # stale lock from a crashed run
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise Busy("Another run is in progress") from None
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


class RunLog:
    """Writes progress to the log file and to the Run row the dashboard shows."""

    def __init__(self, kind: str):
        with db.session() as s:
            self.run = Run(kind=kind)
            s.add(self.run)
            s.commit()
            s.refresh(self.run)
        self.lines: list[str] = []
        self.stats: dict[str, int] = {}

    def __call__(self, message: str, *args) -> None:
        text = message % args if args else message
        log.info(text)
        self.lines.append(f"{utcnow():%H:%M:%S} {text}")
        self._save()

    def count(self, key: str, n: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + n

    def _save(self, status: str | None = None) -> None:
        with db.session() as s:
            run = s.get(Run, self.run.id)
            run.log = "\n".join(self.lines)
            run.stats = dict(self.stats)
            if status:
                run.status, run.finished_at = status, utcnow()
            s.add(run)
            s.commit()

    def finish(self, status: str = "ok") -> None:
        self._save(status)


# ---------- stages ----------

def discover(s: Session, log_: RunLog, client: httpx.Client | None = None) -> int:
    new = 0
    for company in s.exec(select(Company).where(Company.active == True)):  # noqa: E712
        ats = get_ats(company.ats, client)
        try:
            postings = ats.list_jobs(company.token, company.name)
            company.last_error = None
        except ATSError as exc:
            company.last_error = str(exc)
            log_("! %s (%s): %s", company.name, company.ats, exc)
            postings = []
        company.last_checked_at = utcnow()
        s.add(company)
        known = set(
            s.exec(select(Job.external_id).where(Job.ats == company.ats, Job.company_token == company.token))
        )
        for p in postings:
            if p.external_id in known:
                continue
            s.add(Job(**p.__dict__))
            new += 1
        s.commit()
        if postings:
            log_("%s: %d open roles", company.name, len(postings))
    log_.count("discovered", new)
    log_("Discovered %d new jobs", new)
    return new


def apply_filters(s: Session, settings: Settings, log_: RunLog) -> int:
    passed = 0
    for job in s.exec(select(Job).where(Job.status == JobStatus.NEW)):
        reason = filters.check(job.title, job.description, job.location, job.workplace, job.posted_at, settings.search)
        if reason:
            job.status, job.status_reason = JobStatus.FILTERED_OUT, reason
            s.add(job)
        else:
            passed += 1
    s.commit()
    log_.count("passed_filters", passed)
    log_("%d jobs passed your filters", passed)
    return passed


def score_jobs(s: Session, llm: LLM, resume: Resume, settings: Settings, log_: RunLog) -> list[Job]:
    query = (
        select(Job)
        .where(Job.status == JobStatus.NEW, Job.score == None)  # noqa: E711
        .order_by(Job.posted_at.desc())
        .limit(settings.max_scored_per_run)
    )
    good = []
    for job in s.exec(query).all():
        try:
            match = ai.score_job(llm, resume, job.title, job.company_name, job.location, job.description)
        except LLMError as exc:
            log_("! scoring %s failed: %s", job.title, exc)
            continue
        job.score, job.match = match.score, match.model_dump()
        if match.score < settings.min_score:
            job.status, job.status_reason = JobStatus.LOW_SCORE, match.summary
        else:
            good.append(job)
        s.add(job)
        s.commit()
        log_("Scored %s @ %s: %d", job.title, job.company_name, match.score)
    log_.count("good_matches", len(good))
    return good


def _add_question(s: Session, job: Job, **kwargs) -> None:
    exists = s.exec(select(Question).where(Question.job_id == job.id, Question.key == kwargs["key"])).first()
    if exists is None:
        s.add(Question(job_id=job.id, **kwargs))


def prepare_job(
    s: Session,
    job: Job,
    llm: LLM,
    resume: Resume,
    profile: Profile,
    log_: RunLog,
    client: httpx.Client | None = None,
) -> None:
    ats = get_ats(job.ats, client)
    try:
        fields = ats.get_form(job.company_token, job.external_id, job.apply_url)
    except ATSError as exc:
        job.status, job.status_reason = JobStatus.FAILED, f"Could not read the application form: {exc}"
        s.add(job)
        s.commit()
        log_("! %s: %s", job.title, exc)
        return
    job.form_fields = [f.model_dump() for f in fields]
    job.cover_letter_required = ats.needs_cover_letter(fields)
    if job.cover_letter_required and not job.cover_letter:
        try:
            job.cover_letter = ai.cover_letter(llm, resume, job.title, job.company_name, job.description)
        except LLMError as exc:
            log_("! cover letter for %s failed: %s", job.title, exc)

    answers_bank = bank(s)
    ctx = Context(profile, answers_bank, job.location, cover_letter_available=bool(job.cover_letter))
    resolution = resolve_form(fields, ctx)
    job.answers = resolution.answers
    s.add(job)
    s.flush()

    for f in resolution.unresolved:
        draft, reusable = None, f.type != "textarea"
        if f.type != "file":
            try:
                d = ai.draft_answer(llm, resume, f.label, f.option_labels, job.title, job.company_name, job.description)
                draft, reusable = d.answer, reusable and not d.job_specific
            except LLMError:
                pass
        prompt = f.label + (f"\n{f.description}" if f.description else "")
        if f.type == "file":
            prompt += "\n(File upload: answer with the full path to the file on this PC.)"
        _add_question(
            s, job, kind="form", key=normalize(f.label) or f.key, field_key=f.key, prompt=prompt,
            field_type=f.type, options=f.option_labels or None, draft=draft, reusable=reusable,
        )

    # Resume tailoring: suggestions you accept or reject; new claims need your confirmation.
    gaps = (job.match or {}).get("gaps", [])
    try:
        edits = ai.tailor(llm, resume, job.title, job.company_name, job.description, gaps).edits
    except LLMError as exc:
        log_("! tailoring %s failed: %s", job.title, exc)
        edits = []
    for edit in edits:
        prop = to_proposed(edit, resume)
        if prop is None:
            continue
        unknown = []
        denied = False
        for claim in prop.claims:
            known = answers_bank.get(claim_key(claim))
            if known is None:
                unknown.append(claim)
            elif normalize(known) not in ("yes", "y", "true"):
                denied = True
        if denied:
            continue  # you already told us you don't have this skill
        status = SuggestionStatus.BLOCKED if unknown else SuggestionStatus.PENDING
        s.add(
            Suggestion(
                job_id=job.id, kind=prop.kind, target=prop.target, before=prop.before, after=prop.after,
                rationale=prop.rationale, claim="; ".join(unknown) or None, status=status,
            )
        )
        for claim in unknown:
            _add_question(
                s, job, kind="claim", key=claim_key(claim),
                prompt=f"Do you have real experience with {claim}? A suggested resume edit for "
                f"this job would mention it. Answer No if it isn't true.",
                field_type="radio", options=["Yes", "No"], reusable=True,
            )

    job.status = JobStatus.NEEDS_INPUT
    s.add(job)
    s.flush()
    refresh_status(s, job)
    s.commit()
    log_("Prepared %s @ %s → %s", job.title, job.company_name, job.status.value)


def run_daily(llm: LLM | None = None, client: httpx.Client | None = None, submit: bool = True) -> int:
    """Run the whole daily pipeline. Returns the Run id."""
    settings = load_settings()
    run_log = RunLog("daily")
    try:
        with run_lock():
            if not store.has_resume():
                raise RuntimeError("No resume yet: import one in the dashboard (Setup) first.")
            resume, profile = store.load_resume(), store.load_profile()
            llm = llm or get_llm(settings.llm)
            with db.session() as s:
                discover(s, run_log, client)
                apply_filters(s, settings, run_log)
                for job in score_jobs(s, llm, resume, settings, run_log):
                    prepare_job(s, job, llm, resume, profile, run_log, client)
            if submit:
                from job_applier.pipeline.submit import submit_approved

                submit_approved(settings, run_log)
        run_log.finish("ok")
    except Exception as exc:  # recorded for the dashboard, then re-raised for the CLI
        run_log(f"Run failed: {exc}")
        run_log.finish("error")
        raise
    return run_log.run.id


def run_submit(job_ids: list[int] | None = None) -> int:
    """Submit approved applications now (from the dashboard or CLI). Returns the Run id."""
    from job_applier.pipeline.submit import submit_approved

    run_log = RunLog("submit")
    try:
        with run_lock():
            submit_approved(load_settings(), run_log, job_ids)
        run_log.finish("ok")
    except Exception as exc:
        run_log(f"Run failed: {exc}")
        run_log.finish("error")
        raise
    return run_log.run.id


def submitted_today(s: Session, ats: str) -> int:
    since = utcnow() - timedelta(hours=24)
    return len(s.exec(select(Job.id).where(Job.ats == ats, Job.submitted_at >= since)).all())
