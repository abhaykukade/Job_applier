"""Step through LinkedIn's Easy Apply dialog in your logged-in browser.

Questions are read as each step appears. Anything we can't answer from your profile,
saved answers or the answer bank stops the attempt: the draft is discarded and the
question goes to the dashboard. The job is retried once you answer it.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from job_applier.forms.filler import _is_place, fill_field
from job_applier.forms.html_parser import parse_form
from job_applier.forms.mapping import COVER_LETTER, RESUME, Context, match_option, normalize, resolve_field
from job_applier.forms.models import FormField

DIALOG = '[role="dialog"]:has(form), .jobs-easy-apply-modal'
AUTH_URL = re.compile(r"linkedin\.com/(login|authwall|uas/login|signup|checkpoint/lg/login)")
CHECKPOINT_URL = re.compile(r"linkedin\.com/checkpoint/")
SENT = re.compile(r"application (was )?sent|your application was sent|application submitted", re.I)
ALREADY_APPLIED = re.compile(r"\bApplied \d+ \w+ ago\b|\bApplication submitted\b|\bYou applied\b", re.I)
MAX_STEPS = 15


class NotLoggedIn(RuntimeError):
    pass


class SecurityCheck(RuntimeError):
    pass


class Result:
    SUBMITTED = "submitted"
    DRY_RUN = "dry_run"
    HOLD = "hold"  # needs your answers; the draft was discarded
    COVER_LETTER = "cover_letter"  # a cover letter is required and none exists yet
    MANUAL = "manual"


@dataclass
class Attempt:
    result: str
    message: str
    unresolved: list[FormField] = field(default_factory=list)
    # Every question seen, keyed by normalized label, with what was entered.
    seen: list[FormField] = field(default_factory=list)
    used: dict[str, str | list[str]] = field(default_factory=dict)


def _pause(lo: float = 0.6, hi: float = 1.6) -> float:
    return random.uniform(lo, hi) * 1000


def check_session(page: Page) -> None:
    if CHECKPOINT_URL.search(page.url) and not AUTH_URL.search(page.url):
        raise SecurityCheck("LinkedIn is asking for a security check; open LinkedIn and complete it")
    if AUTH_URL.search(page.url):
        raise NotLoggedIn("Not logged in to LinkedIn; use 'Log in to LinkedIn' in Setup")


def _body_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=3000)
    except PlaywrightError:
        return ""


def _easy_apply_button(page: Page) -> Locator | None:
    for loc in (
        page.locator("button.jobs-apply-button").filter(has_text=re.compile("Easy Apply", re.I)),
        page.get_by_role("button", name=re.compile(r"Easy Apply", re.I)),
    ):
        if loc.count() and loc.first.is_visible():
            return loc.first
    return None


def _dialog(page: Page) -> Locator:
    return page.locator(DIALOG).last


def _button(dialog: Locator, pattern: str) -> Locator | None:
    loc = dialog.get_by_role("button", name=re.compile(pattern, re.I))
    for i in range(loc.count()):
        if loc.nth(i).is_visible() and loc.nth(i).is_enabled():
            return loc.nth(i)
    return None


def discard(page: Page) -> None:
    """Close the dialog without saving or sending anything."""
    try:
        dialog = _dialog(page)
        close = dialog.get_by_role("button", name=re.compile(r"^(dismiss|close)", re.I))
        if close.count():
            close.first.click()
            page.wait_for_timeout(800)
        confirm = page.locator('[data-control-name="discard_application_confirm_btn"]')
        if not confirm.count():
            confirm = page.get_by_role("button", name=re.compile(r"^discard", re.I))
        if confirm.count():
            confirm.first.click()
    except PlaywrightError:
        pass


def _current(page: Page, f: FormField) -> str:
    """What the field already holds (LinkedIn pre-fills contact details)."""
    try:
        if f.type in ("radio", "checkbox"):
            checked = page.locator(f'[name="{f.key}"]:checked')
            return (checked.first.get_attribute("value") or "on") if checked.count() else ""
        if f.type == "file":
            return ""
        loc = page.locator(f.meta["selector"]) if f.meta.get("selector") else page.locator(f'[name="{f.key}"]')
        if not loc.count():
            return ""
        if f.type == "select":
            text = loc.first.evaluate("el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : ''")
            return "" if re.match(r"^(select|choose)", text.strip(), re.I) else text.strip()
        return loc.first.input_value().strip()
    except PlaywrightError:
        return ""


def _uncheck_follow(page: Page, dialog: Locator) -> None:
    box = dialog.locator("#follow-company-checkbox")
    try:
        if box.count() and box.first.is_checked():
            label = dialog.locator('label[for="follow-company-checkbox"]')
            (label.first if label.count() else box.first).click()
    except PlaywrightError:
        pass


def _step_errors(dialog: Locator) -> list[str]:
    errors = []
    loc = dialog.locator(".artdeco-inline-feedback--error, [role='alert']")
    for i in range(min(loc.count(), 6)):
        try:
            if loc.nth(i).is_visible():
                text = loc.nth(i).inner_text().strip()
                if text:
                    errors.append(text[:120])
        except PlaywrightError:
            pass
    return list(dict.fromkeys(errors))


def _plan_step(
    page: Page, fields: list[FormField], ctx: Context, answers: dict, files: dict[str, Path], attempt: Attempt
) -> list[tuple[FormField, str | list[str]]]:
    """Decide each field's value. Unknown required fields go to attempt.unresolved."""
    plan = []
    for f in fields:
        label_key = normalize(f.label) or f.key
        attempt.seen.append(f.model_copy(update={"key": label_key}))
        if f.type == "file":
            value, _ = resolve_field(f, ctx)
            if value == RESUME and RESUME in files:
                plan.append((f, RESUME))
                attempt.used[label_key] = RESUME
            elif value == COVER_LETTER and COVER_LETTER in files:
                plan.append((f, COVER_LETTER))
                attempt.used[label_key] = COVER_LETTER
            elif f.required and "cover" in label_key:
                attempt.result = Result.COVER_LETTER
            continue

        value = answers.get(label_key)
        if isinstance(value, str) and f.is_choice:
            value = match_option(value, f.options)
        if value in (None, "", []):
            value, _ = resolve_field(f, ctx)
        current = _current(page, f)
        if value in (None, "", []):
            if current:
                attempt.used[label_key] = current  # keep LinkedIn's pre-filled value
            elif f.required:
                attempt.unresolved.append(f.model_copy(update={"key": label_key}))
            continue
        if current and isinstance(value, str) and _is_place(f):
            city = value.split(",")[0].strip().lower()
            if city and city in current.lower():
                value = current  # LinkedIn's own wording of the same place, e.g. "Seattle, Washington, United States"
        attempt.used[label_key] = value
        if value != current:
            plan.append((f, value))
    return plan


def easy_apply(
    page: Page, apply_url: str, ctx: Context, answers: dict, files: dict[str, Path], dry_run: bool
) -> Attempt:
    page.set_default_timeout(10000)
    page.goto(apply_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(_pause(2.0, 4.0))
    check_session(page)

    button = _easy_apply_button(page)
    if button is None:
        if ALREADY_APPLIED.search(_body_text(page)):
            return Attempt(Result.SUBMITTED, "You had already applied to this job on LinkedIn")
        return Attempt(Result.MANUAL, "No Easy Apply button: the job may be closed or apply on the company site")
    button.click()
    page.wait_for_timeout(_pause(1.5, 3.0))
    check_session(page)

    attempt = Attempt(Result.MANUAL, "")
    for _ in range(MAX_STEPS):
        dialog = _dialog(page)
        try:
            dialog.wait_for(state="visible", timeout=10000)
        except PlaywrightError:
            attempt.message = "The Easy Apply dialog didn't open"
            return attempt
        if SENT.search(dialog.inner_text()):
            break

        fields = parse_form(dialog.inner_html(), None)
        fields = [f for f in fields if f.key != "follow-company-checkbox" and f.label]
        plan = _plan_step(page, fields, ctx, answers, files, attempt)
        if attempt.unresolved or attempt.result == Result.COVER_LETTER:
            discard(page)
            if attempt.result == Result.COVER_LETTER:
                attempt.message = "LinkedIn requires a cover letter for this job"
                return attempt
            attempt.result = Result.HOLD
            attempt.message = "LinkedIn asked: " + "; ".join(f.label for f in attempt.unresolved)
            return attempt

        problems = []
        for f, value in plan:
            try:
                fill_field(page, f, value, files)
            except (PlaywrightError, LookupError) as exc:
                problems.append(f"{f.label}: {str(exc).splitlines()[0]}")
            page.wait_for_timeout(_pause(0.4, 1.2))
        if problems:
            discard(page)
            attempt.message = "Couldn't fill: " + "; ".join(problems)
            return attempt

        _uncheck_follow(page, dialog)
        submit = _button(dialog, r"^submit application")
        if submit is not None:
            if dry_run:
                attempt.result, attempt.message = Result.DRY_RUN, "Dry run: every step filled, not submitted"
                return attempt  # caller screenshots, then discards
            submit.click()
            try:
                page.get_by_text(SENT).first.wait_for(timeout=20000)
            except PlaywrightError:
                attempt.message = "Clicked submit but saw no confirmation; check LinkedIn's Applied jobs"
                return attempt
            break

        step = _button(dialog, r"^review") or _button(dialog, r"^(next|continue)")
        if step is None:
            attempt.message = "Couldn't find the Next/Review button"
            return attempt
        before = dialog.inner_text()
        step.click()
        page.wait_for_timeout(_pause(1.5, 3.5))
        errors = _step_errors(_dialog(page))
        if errors or _dialog(page).inner_text() == before:
            discard(page)
            attempt.message = "LinkedIn didn't accept the answers: " + ("; ".join(errors) or "the step didn't change")
            return attempt
    else:
        discard(page)
        attempt.message = "Too many steps; finish this one yourself"
        return attempt

    attempt.result, attempt.message = Result.SUBMITTED, "Application sent on LinkedIn"
    try:
        done = page.get_by_role("button", name=re.compile(r"^(done|dismiss|close)", re.I))
        if done.count():
            done.first.click()
    except PlaywrightError:
        pass
    return attempt
