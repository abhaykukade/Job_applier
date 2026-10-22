"""Playwright browser helpers: a persistent profile for applying, plus CAPTCHA detection."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from job_applier import paths


def _executable() -> str | None:
    from job_applier.settings import load_settings

    return os.environ.get("JOBAPPLIER_CHROMIUM") or load_settings().browser_executable


@contextmanager
def launch_browser(headless: bool = True) -> Iterator[Browser]:
    """A throwaway browser (used for PDF rendering)."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, executable_path=_executable())
        try:
            yield browser
        finally:
            browser.close()


@contextmanager
def applying_context(headless: bool = False) -> Iterator[BrowserContext]:
    """A persistent profile so cookies and logins survive between runs."""
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(paths.browser_profile_dir()),
            headless=headless,
            executable_path=_executable(),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        try:
            yield context
        finally:
            context.close()


CAPTCHA_SELECTORS = (
    'iframe[src*="hcaptcha.com"][src*="challenge"]',
    'iframe[title*="hCaptcha challenge"]',
    'iframe[src*="recaptcha"][src*="bframe"]',
    'iframe[title*="recaptcha challenge"]',
    'iframe[src*="challenges.cloudflare.com"]',
)


def captcha_visible(page: Page) -> bool:
    for sel in CAPTCHA_SELECTORS:
        loc = page.locator(sel)
        if loc.count() and loc.first.is_visible():
            return True
    return False
