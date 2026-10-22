"""Log in to LinkedIn once, by hand, in the bot's own browser profile.

Your password is never stored: LinkedIn's session cookie lives in data/browser-profile.
"""

from __future__ import annotations

import json
import time

from job_applier import paths
from job_applier.browser import applying_context
from job_applier.db import utcnow

LOGIN_URL = "https://www.linkedin.com/login"


def _state_file():
    return paths.data_dir() / "linkedin_session.json"


def status() -> dict:
    try:
        return json.loads(_state_file().read_text())
    except (OSError, ValueError):
        return {"logged_in": False}


def mark(logged_in: bool, note: str = "") -> None:
    _state_file().write_text(json.dumps({"logged_in": logged_in, "checked_at": utcnow().isoformat(), "note": note}))


def login_interactively(timeout_s: int = 600) -> bool:
    """Open LinkedIn's sign-in page and wait for you to finish signing in (2FA included)."""
    with applying_context(headless=False) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            url = page.url
            if "/feed" in url or "/jobs" in url or "/in/" in url:
                mark(True)
                return True
            page.wait_for_timeout(2000)
    mark(False, "Sign-in timed out")
    return False
