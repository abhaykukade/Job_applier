"""Read-only checks against the real job sites: `jobapplier check-sites`.

Confirms that listings, application forms and LinkedIn search still parse the way the
app expects. Nothing is ever submitted. The optional browser check fills a real form
with an obviously fake test profile, skips file uploads (some sites upload a file the
moment it is chosen), and closes the page without pressing submit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx

from job_applier.ats import ATSError, get_ats
from job_applier.ats.base import JobPosting, make_client
from job_applier.forms.mapping import Context, resolve_form
from job_applier.forms.models import FormField
from job_applier.profile.models import Profile

# Public boards that have used each ATS for years. Override with JOBAPPLIER_CHECK_BOARDS,
# e.g. "greenhouse:stripe,figma;lever:palantir;ashby:openai".
DEFAULT_BOARDS = {
    "greenhouse": ["stripe", "airbnb", "figma", "discord"],
    "lever": ["palantir", "spotify", "plaid"],
    "ashby": ["openai", "notion", "ramp", "linear"],
}

TEST_PROFILE = Profile(
    first_name="Test", last_name="Applicant", email="test.applicant@example.com", phone="+1 555 0100",
    city="Seattle", state="WA", country="United States", linkedin_url="https://www.linkedin.com/in/test-applicant",
    current_company="Example Co", years_experience=5, authorized_countries=["United States"],
    requires_sponsorship=False,
)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    warn: bool = False  # worth a look, not a failure


@dataclass
class Sample:
    """A job whose form was read successfully, kept for the optional browser check."""

    posting: JobPosting
    fields: list[FormField] = field(default_factory=list)


def boards() -> dict[str, list[str]]:
    raw = os.environ.get("JOBAPPLIER_CHECK_BOARDS")
    if not raw:
        return DEFAULT_BOARDS
    out: dict[str, list[str]] = {}
    for part in raw.split(";"):
        ats, _, tokens = part.partition(":")
        out[ats.strip()] = [t.strip() for t in tokens.split(",") if t.strip()]
    return out


def check_ats(ats_name: str, tokens: list[str], client: httpx.Client) -> tuple[list[Check], Sample | None]:
    ats = get_ats(ats_name, client)
    checks: list[Check] = []
    postings: list[JobPosting] = []
    errors = []
    for token in tokens:
        try:
            postings = ats.list_jobs(token, token)
        except ATSError as exc:
            errors.append(f"{token}: {exc}")
            continue
        if postings:
            break
        errors.append(f"{token}: no open jobs")
    if not postings:
        checks.append(Check(f"{ats_name}: list jobs", False, "; ".join(errors) or "no boards to try"))
        return checks, None

    token = postings[0].company_token
    with_desc = sum(1 for p in postings if p.description.strip())
    avg = sum(len(p.description) for p in postings) // len(postings)
    dated = sum(1 for p in postings if p.posted_at)
    checks.append(Check(
        f"{ats_name}: list jobs", True,
        f"{token}: {len(postings)} jobs, e.g. '{postings[0].title}' ({postings[0].location or 'no location'})",
    ))
    checks.append(Check(
        f"{ats_name}: job details", with_desc >= len(postings) * 0.8,
        f"{with_desc}/{len(postings)} have a description (avg {avg} chars), {dated}/{len(postings)} have a date",
        warn=dated < len(postings) * 0.5,
    ))

    # Read the application form of the first few jobs until one works.
    for posting in postings[:3]:
        try:
            fields = ats.get_form(posting.company_token, posting.external_id, posting.apply_url)
        except ATSError as exc:
            errors.append(str(exc))
            continue
        keys = " ".join(f"{f.key} {f.label}".lower() for f in fields)
        has_resume = any(f.type == "file" and "resume" in f"{f.key} {f.label}".lower() for f in fields)
        has_email = "email" in keys
        required = [f for f in fields if f.required]
        resolution = resolve_form(fields, Context(TEST_PROFILE, {}, posting.location))
        unresolved = [f.label for f in resolution.unresolved]
        checks.append(Check(
            f"{ats_name}: read form", has_resume and has_email and bool(fields),
            f"'{posting.title}': {len(fields)} fields ({len(required)} required); "
            f"resume upload {'found' if has_resume else 'MISSING'}, email {'found' if has_email else 'MISSING'}",
        ))
        checks.append(Check(
            f"{ats_name}: auto-answer", True,
            f"{len(required) - len(unresolved)}/{len(required)} required fields answered from a profile; "
            f"would ask about: {', '.join(unresolved[:6]) or 'nothing'}",
            warn=len(unresolved) > len(required) / 2,
        ))
        return checks, Sample(posting, fields)
    checks.append(Check(f"{ats_name}: read form", False, "; ".join(errors[-3:])))
    return checks, None


def check_linkedin(client: httpx.Client) -> list[Check]:
    from job_applier.linkedin.search import LinkedInSearch, SearchError
    from job_applier.settings import LinkedInSettings

    searcher = LinkedInSearch(client)
    try:
        cards = searcher.search("software engineer", "United States", LinkedInSettings(), [], 0)
    except SearchError as exc:
        return [Check("linkedin: search", False, str(exc))]
    if not cards:
        return [Check("linkedin: search", False, "no results parsed: the public search page may have changed")]
    complete = sum(1 for c in cards if c.title and c.company and c.job_id)
    checks = [Check("linkedin: search", complete >= len(cards) * 0.8,
                    f"{len(cards)} results, {complete} complete; e.g. '{cards[0].title}' at {cards[0].company}")]
    details = []
    for card in cards[:4]:
        try:
            detail = searcher.detail(card.job_id)
        except SearchError as exc:
            checks.append(Check("linkedin: job details", False, str(exc)))
            return checks
        if detail:
            details.append(detail)
    described = sum(1 for d in details if d.description.strip())
    offsite = sum(1 for d in details if d.apply_url)
    checks.append(Check(
        "linkedin: job details", bool(details) and described == len(details),
        f"{described}/{len(details)} descriptions read; {offsite} apply on a company site, "
        f"{len(details) - offsite} look like Easy Apply",
    ))
    return checks


def check_browser_fill(sample: Sample) -> Check:
    """Fill (never submit) one real form with the fake test profile, skipping uploads."""
    from job_applier.browser import applying_context
    from job_applier.forms.filler import fill_form
    from job_applier.pipeline import submit as submit_mod

    name = f"{sample.posting.ats}: fill form in browser"
    fields = [f for f in sample.fields if f.type != "file"]
    answers = resolve_form(fields, Context(TEST_PROFILE, {}, sample.posting.location)).answers

    def refuse(*args, **kwargs):  # belt and braces: this check must never submit
        raise AssertionError("check-sites tried to submit")

    original = submit_mod._click_submit
    submit_mod._click_submit = refuse
    try:
        with applying_context(headless=True) as ctx:
            page = ctx.new_page()
            page.set_default_timeout(10000)
            page.goto(sample.posting.apply_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)
            problems = fill_form(page, fields, answers, {}, pace=(0.1, 0.3))
            # Questions with no answer are expected (the app would ask you); only count fill failures.
            problems = [p for p in problems if not p.endswith(": no answer")]
            filled = [f for f in fields if f.key in answers]
            page.close()  # discard: nothing was sent
    except Exception as exc:  # report, don't crash the other checks
        return Check(name, False, f"{sample.posting.apply_url}: {str(exc).splitlines()[0]}")
    finally:
        submit_mod._click_submit = original
    return Check(
        name, not problems,
        f"filled {len(filled) - len(problems)}/{len(filled)} answered fields on '{sample.posting.title}'"
        + (f"; problems: {'; '.join(problems[:4])}" if problems else ""),
    )


def run(browser: bool = False, linkedin: bool = True, client: httpx.Client | None = None) -> list[Check]:
    client = client or make_client()
    results: list[Check] = []
    samples: list[Sample] = []
    for ats_name, tokens in boards().items():
        checks, sample = check_ats(ats_name, tokens, client)
        results += checks
        if sample:
            samples.append(sample)
    if linkedin:
        results += check_linkedin(client)
    if browser:
        results += [check_browser_fill(s) for s in samples]
    return results


def report(results: list[Check]) -> str:
    lines = []
    for c in results:
        mark = "✗" if not c.ok else "!" if c.warn else "✓"
        lines.append(f"{mark} {c.name}: {c.detail}")
    failed = sum(1 for c in results if not c.ok)
    lines.append(f"\n{len(results) - failed}/{len(results)} checks passed" + (f", {failed} failed" if failed else ""))
    return "\n".join(lines)
