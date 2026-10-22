"""Fill a normalized form into a live page with Playwright."""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from job_applier.forms.mapping import COVER_LETTER, RESUME
from job_applier.forms.models import FormField

log = logging.getLogger(__name__)


def _attr(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _first_present(page: Page, selectors: list[str]) -> Locator | None:
    for sel in selectors:
        loc = page.locator(sel)
        if loc.count():
            return loc
    return None


def locate(page: Page, f: FormField) -> Locator | None:
    selectors = [f.meta["selector"]] if f.meta.get("selector") else []
    selectors += [f'[name="{_attr(f.key)}"]', f'[id="{_attr(f.key)}"]']
    loc = _first_present(page, selectors)
    if loc is None and f.label:
        by_label = page.get_by_label(f.label, exact=True)
        if not by_label.count():
            by_label = page.get_by_label(f.label)
        if by_label.count():
            loc = by_label
    return loc


def _tag(loc: Locator) -> str:
    return loc.first.evaluate("el => el.tagName.toLowerCase()")


def _choose_combobox(page: Page, loc: Locator, value: str) -> None:
    """Custom dropdowns (react-select and similar): type, then pick the matching option."""
    box = loc.first
    box.click()
    try:
        box.fill(value)
    except PlaywrightError:
        box.press_sequentially(value, delay=40)
    option = page.get_by_role("option", name=value, exact=True)
    try:
        option.first.wait_for(timeout=2500)
        option.first.click()
        return
    except PlaywrightError:
        pass
    partial = page.get_by_role("option", name=value)
    if partial.count():
        partial.first.click()
    else:
        box.press("Enter")


def _option_value(f: FormField, label: str) -> str:
    for opt in f.options:
        if opt.label == label:
            return opt.value
    return label


def _click_button_choice(page: Page, f: FormField, label: str) -> bool:
    """Yes/No style button groups (e.g. Ashby boolean questions)."""
    group = page.locator("fieldset, div").filter(has_text=f.label)
    scope = group.last if group.count() else page
    button = scope.get_by_role("button", name=label, exact=True)
    if button.count():
        button.first.click()
        return True
    return False


def _check_options(page: Page, f: FormField, labels: list[str]) -> None:
    for label in labels:
        value = _option_value(f, label)
        target = page.locator(f'[name="{_attr(f.key)}"][value="{_attr(value)}"]')
        if target.count():
            target.first.check()
            continue
        by_label = page.get_by_label(label, exact=True)
        if by_label.count():
            by_label.first.check()
            continue
        if not _click_button_choice(page, f, label):
            raise LookupError(f"option {label!r} not found")


def fill_field(page: Page, f: FormField, value: str | list[str], files: dict[str, Path]) -> None:
    values = value if isinstance(value, list) else [value]

    if f.type in ("radio", "checkbox", "boolean") and f.options:
        _check_options(page, f, values)
        return

    loc = locate(page, f)
    if loc is None:
        if f.type in ("select", "boolean") and _click_button_choice(page, f, values[0]):
            return
        raise LookupError("input not found on page")

    if f.type == "file":
        path = files.get(values[0]) if values[0] in (RESUME, COVER_LETTER) else Path(values[0])
        if path is None:
            raise LookupError(f"no file for {values[0]}")
        loc.first.set_input_files(str(path))
        return

    tag = _tag(loc)
    if tag == "select":
        try:
            loc.first.select_option(label=values if len(values) > 1 else values[0])
        except PlaywrightError:
            loc.first.select_option(value=[_option_value(f, v) for v in values])
        return
    if f.type in ("select", "multi_select", "location") or loc.first.get_attribute("role") == "combobox":
        for v in values:
            _choose_combobox(page, loc, v)
        return
    if f.type == "checkbox" or loc.first.get_attribute("type") == "checkbox":
        if values[0].lower() in ("yes", "true", "on", "1"):
            loc.first.check()
        return
    loc.first.fill(values[0])


def fill_form(
    page: Page,
    fields: list[FormField],
    answers: dict[str, str | list[str]],
    files: dict[str, Path],
    pace: tuple[float, float] = (0.2, 0.8),
) -> list[str]:
    """Fill every answered field. Returns problems found (empty list = all good)."""
    problems: list[str] = []
    for f in fields:
        value = answers.get(f.key)
        if value in (None, "", []):
            if f.required:
                problems.append(f"{f.label}: no answer")
            continue
        try:
            fill_field(page, f, value, files)  # type: ignore[arg-type]
        except (PlaywrightError, LookupError) as exc:
            msg = f"{f.label or f.key}: {str(exc).splitlines()[0]}"
            log.warning("fill failed: %s", msg)
            if f.required:
                problems.append(msg)
        time.sleep(random.uniform(*pace))
    return problems
