"""Questions held for you, the reusable answer bank, and job status bookkeeping."""

from __future__ import annotations

from sqlmodel import Session, select

from job_applier.db import (
    AnswerBankEntry,
    Job,
    JobStatus,
    Question,
    QuestionStatus,
    Suggestion,
    SuggestionStatus,
    utcnow,
)
from job_applier.forms.mapping import match_option, normalize
from job_applier.forms.models import FormField


def claim_key(claim: str) -> str:
    return f"claim:{normalize(claim)}"


def is_yes(answer: str) -> bool:
    return normalize(answer) in ("yes", "y", "true")


def _bank_answer(s: Session, key: str) -> str | None:
    entry = s.get(AnswerBankEntry, key)
    return entry.answer if entry else None


def bank(s: Session) -> dict[str, str]:
    return {e.key: e.answer for e in s.exec(select(AnswerBankEntry))}


def remember(s: Session, key: str, question: str, answer: str) -> None:
    entry = s.get(AnswerBankEntry, key)
    if entry is None:
        entry = AnswerBankEntry(key=key, question=question, answer=answer)
    else:
        entry.answer, entry.question, entry.updated_at = answer, question, utcnow()
    s.add(entry)


def open_questions(s: Session, job_id: int | None = None) -> list[Question]:
    query = select(Question).where(Question.status == QuestionStatus.OPEN)
    if job_id is not None:
        query = query.where(Question.job_id == job_id)
    return list(s.exec(query.order_by(Question.created_at)))


def refresh_status(s: Session, job: Job) -> None:
    if job.status not in (JobStatus.NEEDS_INPUT, JobStatus.READY):
        return
    job.status = JobStatus.NEEDS_INPUT if open_questions(s, job.id) else JobStatus.READY
    s.add(job)


def _field(job: Job, key: str | None) -> FormField | None:
    for raw in job.form_fields or []:
        if raw.get("key") == key:
            return FormField.model_validate(raw)
    return None


def _apply_answer(s: Session, q: Question, answer: str) -> None:
    q.answer, q.status = answer, QuestionStatus.ANSWERED
    s.add(q)
    job = s.get(Job, q.job_id)
    if job is None:
        return
    if q.kind == "form":
        field = _field(job, q.field_key)
        value: str | list[str] = answer
        if field is not None and field.is_choice:
            if field.type in ("multi_select", "checkbox") and len(field.options) > 1:
                value = [match_option(p.strip(), field.options) or p.strip() for p in answer.split(";")]
            else:
                value = match_option(answer, field.options) or answer
        job.answers = {**(job.answers or {}), q.field_key: value}
    elif q.kind == "claim":
        for sug in s.exec(
            select(Suggestion).where(Suggestion.job_id == job.id, Suggestion.status == SuggestionStatus.BLOCKED)
        ):
            keys = [claim_key(c) for c in (sug.claim or "").split("; ") if c]
            if q.key not in keys:
                continue
            answers = [answer if k == q.key else _bank_answer(s, k) for k in keys]
            if any(a is not None and not is_yes(a) for a in answers):
                sug.status = SuggestionStatus.REJECTED
            elif all(a is not None for a in answers):
                sug.status = SuggestionStatus.PENDING
            s.add(sug)
    s.add(job)
    refresh_status(s, job)


def answer_question(s: Session, question_id: int, answer: str, reuse: bool | None = None) -> int:
    """Record your answer. Reusable answers also resolve the same question on other jobs.

    Returns how many questions were answered in total.
    """
    q = s.get(Question, question_id)
    if q is None or not answer.strip():
        return 0
    answer = answer.strip()
    reuse = q.reusable if reuse is None else reuse
    if reuse or q.kind == "claim":
        remember(s, q.key, q.prompt, answer)
        s.flush()
    _apply_answer(s, q, answer)
    count = 1
    if reuse or q.kind == "claim":
        others = s.exec(
            select(Question).where(
                Question.key == q.key, Question.status == QuestionStatus.OPEN, Question.id != q.id
            )
        )
        for other in others:
            _apply_answer(s, other, answer)
            count += 1
    s.commit()
    return count
