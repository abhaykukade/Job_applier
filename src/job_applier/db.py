"""SQLite storage for companies, jobs, resume suggestions, questions and the answer bank."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import JSON, Column, Text, UniqueConstraint
from sqlmodel import Field, Session, SQLModel, create_engine, select

from job_applier import paths


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, Enum):
    NEW = "new"  # discovered, not yet filtered
    FILTERED_OUT = "filtered_out"  # failed location/title/seniority/age filters
    LOW_SCORE = "low_score"  # matched filters but scored below min_score
    NEEDS_INPUT = "needs_input"  # waiting for answers to questions
    READY = "ready"  # prepared, waiting for your review
    APPROVED = "approved"  # approved, queued for submission
    SUBMITTED = "submitted"
    MANUAL = "manual"  # needs you to finish it in the browser (CAPTCHA, verification)
    APPLY_YOURSELF = "apply_yourself"  # good match on a site we can't apply on yet
    FAILED = "failed"
    SKIPPED = "skipped"  # you rejected it


class Company(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("ats", "token"),)

    id: int | None = Field(default=None, primary_key=True)
    name: str
    ats: str  # greenhouse | lever | ashby
    token: str  # board token / site name used in ATS URLs
    active: bool = True
    added_at: datetime = Field(default_factory=utcnow)
    last_checked_at: datetime | None = None
    last_error: str | None = None


class Job(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("ats", "company_token", "external_id"),)

    id: int | None = Field(default=None, primary_key=True)
    ats: str
    company_token: str
    company_name: str
    external_id: str
    title: str
    location: str = ""
    workplace: str = "unknown"  # remote | hybrid | onsite | unknown
    url: str = ""
    apply_url: str = ""
    description: str = Field(default="", sa_column=Column(Text))
    posted_at: datetime | None = None
    discovered_at: datetime = Field(default_factory=utcnow)
    status: JobStatus = JobStatus.NEW
    status_reason: str | None = None
    score: int | None = None
    match: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    form_fields: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON))
    answers: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    cover_letter_required: bool = False
    cover_letter: str | None = Field(default=None, sa_column=Column(Text))
    resume_path: str | None = None
    cover_letter_path: str | None = None
    screenshot_path: str | None = None
    approved_at: datetime | None = None
    submitted_at: datetime | None = None
    last_attempt_at: datetime | None = None
    attempts: int = 0


class SuggestionStatus(str, Enum):
    PENDING = "pending"  # waiting for you to accept or reject
    BLOCKED = "blocked"  # waiting for you to confirm a skill/claim
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class Suggestion(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    kind: str  # summary | bullet | skill_add | skills_reorder | bullets_reorder
    target: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    before: str = Field(default="", sa_column=Column(Text))
    after: str = Field(default="", sa_column=Column(Text))
    rationale: str = ""
    claim: str | None = None  # skill/claim that needs your confirmation
    status: SuggestionStatus = SuggestionStatus.PENDING


class QuestionStatus(str, Enum):
    OPEN = "open"
    ANSWERED = "answered"


class Question(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    kind: str  # form (application question) | claim (do you have skill X?)
    key: str = Field(index=True)  # normalized question, shared across jobs
    field_key: str | None = None  # form field the answer goes into
    prompt: str = Field(sa_column=Column(Text))
    field_type: str = "text"
    options: list[str] | None = Field(default=None, sa_column=Column(JSON))
    draft: str | None = Field(default=None, sa_column=Column(Text))
    answer: str | None = Field(default=None, sa_column=Column(Text))
    reusable: bool = True  # store the answer in the answer bank
    status: QuestionStatus = QuestionStatus.OPEN
    created_at: datetime = Field(default_factory=utcnow)


class AnswerBankEntry(SQLModel, table=True):
    key: str = Field(primary_key=True)  # normalized question, or "claim:<skill>"
    question: str = Field(sa_column=Column(Text))
    answer: str = Field(sa_column=Column(Text))
    updated_at: datetime = Field(default_factory=utcnow)


class Run(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    kind: str  # daily | discover | submit
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: str = "running"  # running | ok | error
    stats: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    log: str = Field(default="", sa_column=Column(Text))


_engines: dict[str, Any] = {}


def engine():
    url = f"sqlite:///{paths.db_file()}"
    if url not in _engines:
        _engines[url] = create_engine(url, connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(_engines[url])
    return _engines[url]


def session() -> Session:
    return Session(engine(), expire_on_commit=False)


__all__ = [
    "AnswerBankEntry",
    "Company",
    "Job",
    "JobStatus",
    "Question",
    "QuestionStatus",
    "Run",
    "Session",
    "Suggestion",
    "SuggestionStatus",
    "engine",
    "select",
    "session",
    "utcnow",
]
