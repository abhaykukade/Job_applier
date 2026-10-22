"""Submit approved applications in a real browser window, within your daily caps."""

from __future__ import annotations

import random
import re
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from sqlmodel import select

from job_applier import db, paths
from job_applier.ats import get_ats
from job_applier.ats.base import BaseATS
from job_applier.browser import applying_context, captcha_visible
from job_applier.db import Job, JobStatus, utcnow
from job_applier.forms.filler import fill_form
from job_applier.forms.mapping import COVER_LETTER, RESUME, Context
from job_applier.forms.models import FormField
from job_applier.settings import Settings, SubmitSettings

VERIFICATION = re.compile(r"(verification|security) code|check your email for a code", re.I)
ERROR_SELECTORS = ('[aria-invalid="true"]', ".error-message:visible", ".field-error:visible", '[role="alert"]:visible')


class Outcome:
    SUBMITTED = "submitted"
    DRY_RUN = "dry_run"
    MANUAL = "manual"
    FAILED = "failed"
    HELD = "held"  # waiting for your answers or a cover-letter review


def _shot(page: Page, job: Job, tag: str) -> str:
    path = paths.screenshots_dir() / f"{job.id}_{tag}_{utcnow():%Y%m%d_%H%M%S}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
    except PlaywrightError:
        return ""
    return str(path)


def _succeeded(page: Page, ats: BaseATS) -> bool:
    if re.search(r"/(thanks|thank-you|confirmation|success|submitted)\b", page.url, re.I):
        return True
    try:
        text = page.locator("body").inner_text(timeout=2000)
    except PlaywrightError:
        return False
    return any(re.search(p, text, re.I) for p in ats.success_patterns)


def _click_submit(page: Page, ats: BaseATS) -> bool:
    for sel in ats.submit_selectors:
        loc = page.locator(sel)
        for i in range(loc.count()):
            if loc.nth(i).is_visible():
                loc.nth(i).click()
                return True
    button = page.get_by_role("button", name=re.compile(r"submit", re.I))
    if button.count():
        button.last.click()
        return True
    return False


def _visible_errors(page: Page) -> list[str]:
    errors = []
    for sel in ERROR_SELECTORS:
        loc = page.locator(sel)
        for i in range(min(loc.count(), 5)):
            text = (loc.nth(i).get_attribute("aria-label") or loc.nth(i).inner_text() or "").strip()
            if text:
                errors.append(text[:120])
    return errors


def wait_for_result(page: Page, ats: BaseATS, cfg: SubmitSettings, timeout_s: float = 25) -> tuple[str, str]:
    """After clicking submit: wait for confirmation, or for you to solve a CAPTCHA/verification."""
    deadline = time.monotonic() + timeout_s
    waiting_for_you = False
    while time.monotonic() < deadline:
        if _succeeded(page, ats):
            return Outcome.SUBMITTED, "Application submitted"
        needs_you = captcha_visible(page)
        if not needs_you:
            try:
                needs_you = bool(VERIFICATION.search(page.locator("body").inner_text(timeout=1000)))
            except PlaywrightError:
                pass
        if needs_you:
            if cfg.headless:
                return Outcome.MANUAL, "CAPTCHA or email verification needed; open the job and finish it"
            if not waiting_for_you:
                waiting_for_you = True
                deadline = time.monotonic() + cfg.manual_wait_s
                page.bring_to_front()
        page.wait_for_timeout(1000)
    if waiting_for_you:
        return Outcome.MANUAL, "Timed out waiting for CAPTCHA/verification; finish it manually"
    errors = _visible_errors(page)
    if errors:
        return Outcome.FAILED, "Form errors: " + "; ".join(dict.fromkeys(errors))
    return Outcome.MANUAL, "No confirmation detected; check the screenshot and the job page"


def submit_one(page: Page, job: Job, cfg: SubmitSettings) -> tuple[str, str, str]:
    """Returns (outcome, message, screenshot path)."""
    ats = get_ats(job.ats)
    fields = [FormField.model_validate(f) for f in job.form_fields or []]
    files = {}
    if job.resume_path:
        files[RESUME] = Path(job.resume_path)
    if job.cover_letter_path:
        files[COVER_LETTER] = Path(job.cover_letter_path)

    page.goto(job.apply_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    if captcha_visible(page):
        return Outcome.MANUAL, "The page showed a CAPTCHA before the form", _shot(page, job, "blocked")

    problems = fill_form(page, fields, job.answers or {}, files)
    filled = _shot(page, job, "filled")
    if problems:
        return Outcome.MANUAL, "Couldn't fill: " + "; ".join(problems), filled
    if cfg.dry_run:
        return Outcome.DRY_RUN, "Dry run: form filled but not submitted (turn off dry run in Setup)", filled
    if not _click_submit(page, ats):
        return Outcome.MANUAL, "Submit button not found", filled
    outcome, message = wait_for_result(page, ats, cfg)
    return outcome, message, _shot(page, job, outcome)


def _llm_or_none(settings: Settings):
    from job_applier.llm import get_llm

    try:
        return get_llm(settings.llm)
    except Exception:  # no key configured: questions are still held, just without drafts
        return None


def linkedin_one(page: Page, job: Job, settings: Settings) -> tuple[str, str, str]:
    """Easy Apply one job. Handles held questions and cover letters; returns (outcome, message, shot)."""
    from job_applier.linkedin import easy_apply as ea
    from job_applier.pipeline.answers import bank
    from job_applier.pipeline.run import ask_about
    from job_applier.profile import store

    with db.session() as s:
        answers_bank = bank(s)
    files = {}
    if job.resume_path:
        files[RESUME] = Path(job.resume_path)
    if job.cover_letter_path:
        files[COVER_LETTER] = Path(job.cover_letter_path)
    ctx = Context(store.load_profile(), answers_bank, job.location, cover_letter_available=COVER_LETTER in files)

    attempt = ea.easy_apply(page, job.apply_url, ctx, job.answers or {}, files, settings.submit.dry_run)
    shot = _shot(page, job, attempt.result)
    if attempt.result == ea.Result.DRY_RUN:
        ea.discard(page)

    with db.session() as s:
        row = s.get(Job, job.id)
        seen = {f["key"]: f for f in row.form_fields or []}
        seen.update({f.key: f.model_dump() for f in attempt.seen})
        row.form_fields = list(seen.values())
        row.answers = {**attempt.used, **(row.answers or {})}
        outcome = {
            ea.Result.SUBMITTED: Outcome.SUBMITTED, ea.Result.DRY_RUN: Outcome.DRY_RUN, ea.Result.MANUAL: Outcome.MANUAL,
        }.get(attempt.result, Outcome.HELD)
        message = attempt.message
        if attempt.result == ea.Result.HOLD:
            llm = _llm_or_none(settings)
            ask_about(s, row, attempt.unresolved, llm, store.load_resume(), label_keys=True)
            row.status = JobStatus.NEEDS_INPUT
            message += ". Answer it on the Questions page and the job goes back in the queue."
        elif attempt.result == ea.Result.COVER_LETTER:
            llm = _llm_or_none(settings)
            if llm is None:
                outcome, message = Outcome.MANUAL, message + "; add an LLM key or apply yourself"
            else:
                from job_applier.pipeline import ai
                from job_applier.pipeline.review import build_documents

                resume = store.load_resume()
                row.cover_letter = ai.cover_letter(llm, resume, row.title, row.company_name, row.description)
                row.cover_letter_required = True
                build_documents(s, row)
                row.status, row.approved_at = JobStatus.READY, None
                message += ": a draft is ready on the job page; review it and approve again"
        s.add(row)
        s.commit()
    return outcome, message, shot


def submit_approved(settings: Settings, log, job_ids: list[int] | None = None) -> dict[str, int]:
    from job_applier.linkedin import session as li_session
    from job_applier.linkedin.discovery import EASY_APPLY
    from job_applier.linkedin.easy_apply import NotLoggedIn, SecurityCheck
    from job_applier.pipeline.run import submitted_today

    counts = {Outcome.SUBMITTED: 0, Outcome.DRY_RUN: 0, Outcome.MANUAL: 0, Outcome.FAILED: 0, Outcome.HELD: 0}
    with db.session() as s:
        query = select(Job).where(Job.status == JobStatus.APPROVED).order_by(Job.score.desc())
        jobs = [j for j in s.exec(query).all() if job_ids is None or j.id in job_ids]
        if not jobs:
            log("No approved applications waiting")
            return counts
        used = {ats: submitted_today(s, ats) for ats in {j.ats for j in jobs}}

    cfg = settings.submit
    linkedin_blocked = False
    with applying_context(headless=cfg.headless) as ctx:
        for i, job in enumerate(jobs):
            is_linkedin = job.ats == EASY_APPLY
            cap = settings.caps.get(job.ats, 10)
            if used[job.ats] >= cap:
                log("Daily cap reached for %s (%d); %s waits for tomorrow", job.ats, cap, job.title)
                continue
            if is_linkedin and linkedin_blocked:
                continue
            log("Applying: %s @ %s%s", job.title, job.company_name, " (dry run)" if cfg.dry_run else "")
            page = ctx.new_page()
            try:
                if is_linkedin:
                    outcome, message, shot = linkedin_one(page, job, settings)
                else:
                    outcome, message, shot = submit_one(page, job, cfg)
            except (NotLoggedIn, SecurityCheck) as exc:
                li_session.mark(False, str(exc))
                log("! %s. LinkedIn applications wait until you fix this.", exc)
                linkedin_blocked = True
                page.close()
                continue
            except PlaywrightError as exc:
                outcome, message, shot = Outcome.FAILED, f"Browser error: {str(exc).splitlines()[0]}", ""
            finally:
                if not page.is_closed():
                    page.close()
            with db.session() as s:
                row = s.get(Job, job.id)
                row.attempts += 1
                row.last_attempt_at = utcnow()
                row.status_reason = message
                row.screenshot_path = shot or row.screenshot_path
                if outcome == Outcome.SUBMITTED:
                    row.status, row.submitted_at = JobStatus.SUBMITTED, utcnow()
                    used[job.ats] += 1
                elif outcome == Outcome.MANUAL:
                    row.status = JobStatus.MANUAL
                elif outcome == Outcome.FAILED:
                    row.status = JobStatus.FAILED
                s.add(row)
                s.commit()
            if is_linkedin and outcome in (Outcome.SUBMITTED, Outcome.DRY_RUN, Outcome.HELD):
                li_session.mark(True)
            counts[outcome] += 1
            log.count(outcome)
            log("→ %s: %s", outcome, message)
            if i < len(jobs) - 1:
                lo, hi = (settings.linkedin.min_delay_s, settings.linkedin.max_delay_s) if is_linkedin else (cfg.min_delay_s, cfg.max_delay_s)
                time.sleep(random.uniform(lo, hi))
    return counts
