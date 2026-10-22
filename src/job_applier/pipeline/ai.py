"""All LLM tasks: resume import, search terms, match scoring, tailoring, cover letters, answers."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from job_applier.llm import LLM
from job_applier.profile.models import Resume

TRUTH_RULES = (
    "Never invent experience, employers, titles, dates, degrees, certifications, metrics or skills. "
    "Only use facts present in the candidate's resume or explicitly confirmed by the candidate."
)


def _job_block(title: str, company: str, location: str, description: str) -> str:
    return (
        f"<job>\nTitle: {title}\nCompany: {company}\nLocation: {location}\n\n{description}\n</job>"
    )


def _resume_block(resume: Resume) -> str:
    return f"<resume_json>\n{resume.model_dump_json(indent=1)}\n</resume_json>"


# ---------- resume import ----------

def parse_resume(llm: LLM, text: str) -> Resume:
    return llm.structured(
        system=(
            "You convert resume text into structured data. Copy wording faithfully; do not "
            "rewrite, summarize or add anything. Keep bullets as written. Use empty strings for "
            "unknown values and keep dates as written (e.g. 'Jan 2021')."
        ),
        prompt=f"<resume_text>\n{text}\n</resume_text>\n\nExtract this resume.",
        output=Resume,
    )


class SearchSuggestion(BaseModel):
    titles: list[str] = Field(description="Job titles this person is a strong fit for")
    keywords: list[str] = Field(description="Core skills/technologies to search for")
    seniority: list[str] = Field(description="Seniority words matching their level, e.g. Senior")


def suggest_search(llm: LLM, resume: Resume) -> SearchSuggestion:
    return llm.structured(
        system="You are a career coach helping someone set up a job search.",
        prompt=(
            f"{_resume_block(resume)}\n\nSuggest 3-8 target job titles (short, as they appear in "
            "postings), 5-12 search keywords, and 1-3 seniority words that match this person's "
            "current level."
        ),
        output=SearchSuggestion,
    )


# ---------- scoring ----------

class MatchResult(BaseModel):
    score: int = Field(description="0-100 fit between candidate and job")
    summary: str = Field(description="One or two sentences on overall fit")
    strengths: list[str]
    gaps: list[str] = Field(description="Requirements the resume does not show evidence for")
    dealbreakers: list[str] = Field(
        description="Hard requirements the candidate clearly fails (clearance, license, location, years)"
    )


def score_job(llm: LLM, resume: Resume, title: str, company: str, location: str, description: str) -> MatchResult:
    result = llm.structured(
        system=(
            "You are an experienced technical recruiter. Score candidate-job fit honestly. "
            "90+: meets nearly all requirements; 70-89: strong with minor gaps; 50-69: partial; "
            "below 50: poor fit or dealbreakers."
        ),
        prompt=f"{_resume_block(resume)}\n\n{_job_block(title, company, location, description)}\n\nScore this match.",
        output=MatchResult,
    )
    result.score = max(0, min(100, result.score))
    return result


# ---------- tailoring ----------

class TailorEdit(BaseModel):
    kind: Literal["summary", "bullet", "skill_add", "skills_reorder", "bullets_reorder"]
    experience_index: int | None = Field(description="For bullet / bullets_reorder")
    bullet_index: int | None = Field(description="For bullet")
    skill_group_index: int | None = Field(description="For skill_add / skills_reorder")
    new_text: str | None = Field(description="New summary, new bullet text, or the skill to add")
    new_order: list[int] | None = Field(description="Permutation of indices for *_reorder")
    rationale: str
    claim: str | None = Field(
        description="If this edit states something the resume does not already show, the exact "
        "skill or claim the candidate must confirm (e.g. 'Terraform'); otherwise null"
    )


class TailorResult(BaseModel):
    edits: list[TailorEdit]


def tailor(llm: LLM, resume: Resume, title: str, company: str, description: str, gaps: list[str]) -> TailorResult:
    return llm.structured(
        system=(
            "You make small, high-impact, truthful edits so a resume reads well for a specific job "
            "and passes ATS keyword screens. " + TRUTH_RULES + " Allowed edits: rewrite the summary; "
            "reword a bullet to mirror the job's terminology when the bullet already describes that "
            "work; reorder bullets or skills to put the most relevant first; add a skill. Any edit that "
            "introduces a skill, tool or claim not already in the resume MUST set `claim` so the "
            "candidate can confirm it first. Prefer 2-6 edits; return none if the resume already fits."
        ),
        prompt=(
            f"{_resume_block(resume)}\n\n{_job_block(title, company, '', description)}\n\n"
            f"Gaps a recruiter noticed: {json.dumps(gaps)}\n\nPropose edits. Indices are 0-based "
            "positions in the resume JSON."
        ),
        output=TailorResult,
    )


# ---------- cover letter ----------

class CoverLetter(BaseModel):
    text: str


def cover_letter(llm: LLM, resume: Resume, title: str, company: str, description: str) -> str:
    return llm.structured(
        system=(
            "You write concise, specific cover letters (180-260 words, 3-4 short paragraphs, no "
            "clichés, no placeholders). " + TRUTH_RULES
        ),
        prompt=f"{_resume_block(resume)}\n\n{_job_block(title, company, '', description)}\n\nWrite the cover letter, signed with the candidate's name.",
        output=CoverLetter,
    ).text


# ---------- drafting answers for questions we hold ----------

class DraftAnswer(BaseModel):
    answer: str | None = Field(description="Suggested answer, or null if it can't be inferred")
    job_specific: bool = Field(description="True if the answer only makes sense for this one job")


def draft_answer(
    llm: LLM, resume: Resume, question: str, options: list[str], title: str, company: str, description: str
) -> DraftAnswer:
    choices = f"\nAllowed answers (pick exactly one, verbatim): {json.dumps(options)}" if options else ""
    return llm.structured(
        system=(
            "You draft answers to job application questions for the candidate to review. "
            + TRUTH_RULES
            + " If the answer depends on facts not in the resume (e.g. personal circumstances, "
            "preferences, legal status), return null."
        ),
        prompt=(
            f"{_resume_block(resume)}\n\n{_job_block(title, company, '', description[:6000])}\n\n"
            f"Question: {question}{choices}"
        ),
        output=DraftAnswer,
    )
