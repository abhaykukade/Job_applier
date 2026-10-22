"""Decide what to enter in each form field from the profile, saved EEO answers and the answer bank.

Anything required that can't be answered with confidence is returned as unresolved,
so the pipeline can hold the application and ask you.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from job_applier.forms.models import FormField, Option
from job_applier.profile.models import Profile

RESUME = "@resume"
COVER_LETTER = "@cover_letter"

YES = {"yes", "y", "true", "1", "i am", "i do", "i will", "i have"}
NO = {"no", "n", "false", "0", "i am not", "i do not", "i will not", "i don't"}
DECLINE_WORDS = ("decline", "prefer not", "don't wish", "do not wish", "not wish", "choose not", "not to answer", "not to disclose", "rather not")

COUNTRY_ALIASES = {
    "united states": ("united states", "america"),
    "canada": ("canada",),
    "united kingdom": ("united kingdom", "great britain", "britain", "england"),
    "india": ("india",),
    "germany": ("germany", "deutschland"),
    "ireland": ("ireland",),
    "australia": ("australia",),
    "netherlands": ("netherlands",),
    "france": ("france",),
    "singapore": ("singapore",),
}
# Short codes are matched case-sensitively so "work for us" doesn't mean the USA.
COUNTRY_CODES = {
    "united states": re.compile(r"\b(US|USA|U\.S\.A?\.?)(?![A-Za-z])"),
    "canada": re.compile(r"\bCA\b|\bCAN\b"),
    "united kingdom": re.compile(r"\bUK\b|\bU\.K\."),
}


def normalize(text: str) -> str:
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def yes_no(value: bool) -> str:
    return "Yes" if value else "No"


def _countries_in(text: str) -> set[str]:
    norm = f" {normalize(text)} "
    found = set()
    for country, aliases in COUNTRY_ALIASES.items():
        if any(f" {normalize(a)} " in norm for a in aliases):
            found.add(country)
    for country, pattern in COUNTRY_CODES.items():
        if pattern.search(text):
            found.add(country)
    return found


def match_option(answer: str, options: list[Option]) -> str | None:
    """Return the label of the option that corresponds to `answer`."""
    if not options:
        return answer
    ans = normalize(answer)
    labels = {normalize(o.label): o.label for o in options}
    labels.update({normalize(o.value): o.label for o in options if o.value})
    if ans in labels:
        return labels[ans]
    if ans in YES or ans.startswith("yes"):
        for norm, label in labels.items():
            if norm.startswith("yes"):
                return label
    if ans in NO or ans.startswith("no "):
        for norm, label in labels.items():
            if norm == "no" or norm.startswith("no "):
                return label
    if any(w in ans for w in DECLINE_WORDS):
        for norm, label in labels.items():
            if any(w in norm for w in DECLINE_WORDS):
                return label
        return None
    for norm, label in labels.items():
        if len(ans) >= 3 and len(norm) >= 3 and (ans in norm or norm in ans):
            return label
    close = difflib.get_close_matches(ans, list(labels), n=1, cutoff=0.75)
    return labels[close[0]] if close else None


@dataclass
class Context:
    profile: Profile
    bank: Mapping[str, str]
    job_location: str = ""
    cover_letter_available: bool = False


@dataclass
class Resolution:
    answers: dict[str, str | list[str]] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    unresolved: list[FormField] = field(default_factory=list)


Rule = tuple[str, Callable[[Context, FormField], str | None]]


def _p(attr: str) -> Callable[[Context, FormField], str | None]:
    return lambda ctx, f: getattr(ctx.profile, attr) or None


def _authorized(ctx: Context, f: FormField) -> str | None:
    allowed: set[str] = set()
    for country in ctx.profile.authorized_countries:
        allowed |= _countries_in(country) or {normalize(country)}
    if not allowed:
        return None
    mentioned = _countries_in(f.label + " " + f.description) or _countries_in(ctx.job_location)
    if not mentioned:
        return None
    return yes_no(bool(mentioned & allowed))


def _sponsorship(ctx: Context, f: FormField) -> str | None:
    if ctx.profile.requires_sponsorship is None:
        return None
    return yes_no(ctx.profile.requires_sponsorship)


def _relocate(ctx: Context, f: FormField) -> str | None:
    if ctx.profile.willing_to_relocate is None:
        return None
    return yes_no(ctx.profile.willing_to_relocate)


def _years(ctx: Context, f: FormField) -> str | None:
    return str(ctx.profile.years_experience) if ctx.profile.years_experience is not None else None


def _preferred(ctx: Context, f: FormField) -> str | None:
    return ctx.profile.preferred_name or ctx.profile.first_name or None


def _website(ctx: Context, f: FormField) -> str | None:
    return ctx.profile.website_url or ctx.profile.github_url or None


def _eeo(attr: str) -> Callable[[Context, FormField], str | None]:
    return lambda ctx, f: getattr(ctx.profile.eeo, attr) or None


# Order matters: the first matching rule wins.
RULES: list[Rule] = [
    (r"^(legal )?first name|^given name|^first$", _p("first_name")),
    (r"^(legal )?last name|^surname|^family name|^last$", _p("last_name")),
    (r"preferred (first )?name", _preferred),
    (r"^(full |legal |your )?name$", lambda ctx, f: ctx.profile.full_name or None),
    (r"\bemail\b", _p("email")),
    (r"phone|mobile", _p("phone")),
    (r"linkedin", _p("linkedin_url")),
    (r"github", _p("github_url")),
    (r"website|portfolio|personal (site|url)|^other website", _website),
    (r"current (company|employer)|^company( name)?$|^org$|most recent (company|employer)", _p("current_company")),
    (r"current (job )?title|^job title$|most recent (job )?title", _p("current_title")),
    (r"pronoun", _eeo("pronouns")),
    (r"how did you (hear|find|learn)|referral source|^source$", _p("how_did_you_hear")),
    (r"^(how many |total )?years of (professional |relevant |work |industry )?experience( do you have)?$", _years),
    (r"authori[sz]ed to work|eligible to work|right to work|legally (able|permitted) to work", _authorized),
    (r"sponsor|visa", _sponsorship),
    (r"relocat", _relocate),
    (r"salary|compensation (expectation|requirement)|desired (pay|compensation)|expected (pay|compensation)", _p("salary_expectation")),
    (r"notice period", _p("notice_period")),
    (r"start date|when can you start|available to start|earliest (date|start)", _p("earliest_start_date")),
    (r"^(current )?(location|city)( city)?$|where are you (currently )?(located|based)|city of residence", lambda ctx, f: ctx.profile.location or None),
    (r"^country( of residence)?$", _p("country")),
    (r"^(state|province)$", _p("state")),
    (r"zip|postal code", _p("postal_code")),
]

EEO_RULES: list[Rule] = [
    (r"hispanic|latin[oax]", _eeo("hispanic_latino")),
    (r"\bgender\b|\bsex\b", _eeo("gender")),
    (r"\brace\b|ethnicity", _eeo("race")),
    (r"veteran", _eeo("veteran_status")),
    (r"disabilit", _eeo("disability_status")),
    (r"sexual orientation", _eeo("sexual_orientation")),
]

KEY_ALIASES = {
    "first_name": "first name", "last_name": "last name", "email": "email", "phone": "phone",
    "name": "full name", "_systemfield_name": "full name", "_systemfield_email": "email",
    "_systemfield_phone": "phone", "org": "current company", "location": "location",
    "urls[linkedin]": "linkedin", "urls[github]": "github", "urls[portfolio]": "portfolio",
    "urls[twitter]": "twitter", "urls[other]": "other website",
}


def _file_value(f: FormField, ctx: Context) -> str | None:
    text = normalize(f"{f.key} {f.label}")
    if "cover" in text:
        return COVER_LETTER if ctx.cover_letter_available else None
    if "resume" in text or "cv" in text.split():
        return RESUME
    return None


def _apply_rules(rules: list[Rule], label: str, ctx: Context, f: FormField) -> str | None:
    for pattern, getter in rules:
        if re.search(pattern, label):
            return getter(ctx, f)
    return None


def bank_lookup(bank: Mapping[str, str], label: str) -> str | None:
    key = normalize(label)
    if key in bank:
        return bank[key]
    close = difflib.get_close_matches(key, [k for k in bank if not k.startswith("claim:")], n=1, cutoff=0.92)
    return bank[close[0]] if close else None


def resolve_field(f: FormField, ctx: Context) -> tuple[str | list[str] | None, str]:
    if f.type == "file":
        return _file_value(f, ctx), "file"

    label = normalize(f.label) or KEY_ALIASES.get(f.key.lower(), "")
    alias = KEY_ALIASES.get(f.key.lower())
    raw: str | None = None
    source = ""
    if f.section == "eeo" or any(re.search(p, label) for p, _ in EEO_RULES):
        raw, source = _apply_rules(EEO_RULES, label, ctx, f), "eeo"
    if raw is None:
        raw = _apply_rules(RULES, label, ctx, f)
        if raw is None and alias:
            raw = _apply_rules(RULES, alias, ctx, f)
        source = "profile"
    if raw is None:
        raw, source = bank_lookup(ctx.bank, f.label), "bank"
    if raw is None:
        return None, ""

    if f.is_choice:
        if f.type in ("multi_select", "checkbox") and len(f.options) > 1:
            picks = [match_option(part.strip(), f.options) for part in raw.split(";")]
            if not picks or any(p is None for p in picks):
                return None, ""
            return [p for p in picks if p], source
        matched = match_option(raw, f.options)
        return (matched, source) if matched else (None, "")
    return raw, source


def resolve_form(fields: list[FormField], ctx: Context) -> Resolution:
    result = Resolution()
    for f in fields:
        value, source = resolve_field(f, ctx)
        if value not in (None, "", []):
            result.answers[f.key] = value  # type: ignore[assignment]
            result.sources[f.key] = source
        elif f.required:
            result.unresolved.append(f)
    return result
