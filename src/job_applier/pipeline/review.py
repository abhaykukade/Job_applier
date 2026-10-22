"""Your review actions: accept/reject edits, approve (renders the tailored resume) or skip."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from job_applier import paths
from job_applier.db import Job, JobStatus, Suggestion, SuggestionStatus, utcnow
from job_applier.pipeline.answers import open_questions
from job_applier.profile import store
from job_applier.profile.models import Resume
from job_applier.resume import render
from job_applier.resume.tailoring import apply_edits


class ReviewError(RuntimeError):
    pass


def set_suggestion(s: Session, suggestion_id: int, accept: bool) -> Suggestion:
    sug = s.get(Suggestion, suggestion_id)
    if sug is None:
        raise ReviewError("Suggestion not found")
    if sug.status == SuggestionStatus.BLOCKED and accept:
        raise ReviewError("Answer the confirmation question first")
    sug.status = SuggestionStatus.ACCEPTED if accept else SuggestionStatus.REJECTED
    s.add(sug)
    s.commit()
    return sug


def accept_all(s: Session, job_id: int) -> None:
    for sug in s.exec(select(Suggestion).where(Suggestion.job_id == job_id, Suggestion.status == SuggestionStatus.PENDING)):
        sug.status = SuggestionStatus.ACCEPTED
        s.add(sug)
    s.commit()


def tailored_resume(s: Session, job: Job, master: Resume | None = None) -> Resume:
    master = master or store.load_resume()
    accepted = s.exec(
        select(Suggestion).where(Suggestion.job_id == job.id, Suggestion.status == SuggestionStatus.ACCEPTED)
    ).all()
    return apply_edits(master, [(a.kind, a.target, a.after) for a in accepted])


def build_documents(s: Session, job: Job) -> None:
    resume = tailored_resume(s, job)
    out = paths.output_dir(job.id)
    job.resume_path = str(render.render_pdf(resume, out / f"{render.file_stem(resume.basics.name, 'Resume')}.pdf"))
    render.render_docx(resume, out / f"{render.file_stem(resume.basics.name, 'Resume')}.docx")
    if job.cover_letter:
        name = render.file_stem(resume.basics.name, "Cover_Letter")
        job.cover_letter_path = str(render.cover_letter_pdf(job.cover_letter, out / f"{name}.pdf"))
    s.add(job)


def approve(s: Session, job_id: int) -> Job:
    job = s.get(Job, job_id)
    if job is None:
        raise ReviewError("Job not found")
    if job.status not in (JobStatus.READY, JobStatus.MANUAL, JobStatus.FAILED):
        raise ReviewError(f"Can't approve a job that is {job.status.value}")
    if open_questions(s, job.id):
        raise ReviewError("Answer this job's open questions first")
    if not job.form_fields:
        raise ReviewError("The application form hasn't been read yet")
    build_documents(s, job)
    job.status, job.status_reason, job.approved_at = JobStatus.APPROVED, None, utcnow()
    s.add(job)
    s.commit()
    return job


def skip(s: Session, job_id: int, reason: str = "Skipped by you") -> None:
    job = s.get(Job, job_id)
    if job is not None:
        job.status, job.status_reason = JobStatus.SKIPPED, reason
        s.add(job)
        s.commit()


def update_answers(s: Session, job_id: int, updates: dict[str, str]) -> None:
    job = s.get(Job, job_id)
    if job is None:
        return
    answers = dict(job.answers or {})
    for key, value in updates.items():
        if value.strip():
            answers[key] = value.strip()
        else:
            answers.pop(key, None)
    job.answers = answers
    s.add(job)
    s.commit()


def resume_preview_path(job: Job) -> Path | None:
    return Path(job.resume_path) if job.resume_path and Path(job.resume_path).exists() else None
