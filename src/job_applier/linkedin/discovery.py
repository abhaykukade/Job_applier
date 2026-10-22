"""Turn LinkedIn search results into jobs, routing each by how it is applied to."""

from __future__ import annotations

import httpx
from sqlmodel import Session, func, select

from job_applier.ats import parse_board_url
from job_applier.ats.base import infer_workplace
from job_applier.db import Company, Job, JobStatus
from job_applier.linkedin.search import Card, LinkedInSearch, SearchError
from job_applier.pipeline import filters
from job_applier.settings import Settings

EASY_APPLY = "linkedin"  # applied to with Easy Apply in your logged-in browser
EXTERNAL = "external"  # company site we can't apply on yet: you apply yourself


def _workplace(card: Card) -> str:
    # LinkedIn already filtered by workplace; only trust explicit words in the text.
    guess = infer_workplace(card.location, card.title)
    return guess if guess in ("remote", "hybrid") else "unknown"


def _duplicate(s: Session, company: str, title: str) -> bool:
    return (
        s.exec(
            select(func.count()).select_from(Job).where(
                func.lower(Job.company_name) == company.lower(), func.lower(Job.title) == title.lower()
            )
        ).one()
        > 0
    )


def discover_linkedin(
    s: Session, settings: Settings, log, client: httpx.Client | None = None, searcher: LinkedInSearch | None = None
) -> int:
    cfg = settings.linkedin
    if not cfg.enabled:
        return 0
    queries = cfg.queries or settings.search.titles_include
    if not queries:
        log("LinkedIn: add search phrases or target titles in Setup")
        return 0
    locations = cfg.locations or settings.search.locations or [""]
    searcher = searcher or LinkedInSearch(client)
    seen = set(s.exec(select(Job.external_id).where(Job.ats.in_([EASY_APPLY, EXTERNAL]))))
    # Location and keywords are applied by LinkedIn's search itself.
    prefilter = settings.search.model_copy(update={"locations": [], "keywords": []})

    candidates: dict[str, Card] = {}
    added = 0
    try:
        for query in queries:
            for location in locations:
                for page in range(cfg.pages_per_query):
                    cards = searcher.search(query, location, cfg, settings.search.workplace, page)
                    for card in cards:
                        if card.job_id in seen or card.job_id in candidates:
                            continue
                        if filters.check(card.title, "", card.location, _workplace(card), card.posted_at, prefilter):
                            continue
                        candidates[card.job_id] = card
                    if len(cards) < 25:
                        break
        log("LinkedIn: %d new matching listings", len(candidates))

        ordered = sorted(candidates.values(), key=lambda c: c.posted_at.timestamp() if c.posted_at else 0, reverse=True)
        for card in ordered[: cfg.max_details_per_run]:
            detail = searcher.detail(card.job_id)
            if detail is None:
                continue
            job = Job(
                ats=EASY_APPLY, company_token=EASY_APPLY, company_name=card.company, external_id=card.job_id,
                title=card.title, location=card.location, workplace=_workplace(card), url=card.url,
                apply_url=f"https://www.linkedin.com/jobs/view/{card.job_id}/",
                description=detail.description, posted_at=card.posted_at,
            )
            board = parse_board_url(detail.apply_url) if detail.apply_url else None
            if board:
                ats, token = board
                if s.exec(select(Company).where(Company.ats == ats, Company.token == token)).first() is None:
                    s.add(Company(name=card.company or token, ats=ats, token=token))
                    log("LinkedIn: now watching %s on %s (%s)", card.company, ats, token)
                # The company's board is checked directly; keep this row only so we don't refetch it.
                job.ats, job.company_token = EXTERNAL, EXTERNAL
                job.status, job.status_reason = JobStatus.FILTERED_OUT, f"Applied via {ats} board"
            elif detail.apply_url:
                job.ats, job.company_token, job.apply_url = EXTERNAL, EXTERNAL, detail.apply_url
            if job.status == JobStatus.NEW and _duplicate(s, card.company, card.title):
                job.status, job.status_reason = JobStatus.FILTERED_OUT, "Already found on the company's board"
            s.add(job)
            s.commit()
            if job.status == JobStatus.NEW:
                added += 1
    except SearchError as exc:  # rate limit or network trouble: keep what we have, skip the rest
        s.commit()
        log("! LinkedIn: %s", exc)
    log("LinkedIn: %d new jobs added", added)
    return added
