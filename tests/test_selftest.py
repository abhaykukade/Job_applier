"""The live-check logic itself, run against the recorded fixtures."""

from job_applier import selftest


def test_checks_pass_on_fixtures(client, monkeypatch):
    monkeypatch.setenv("JOBAPPLIER_CHECK_BOARDS", "greenhouse:missing,acme;lever:globex;ashby:initech")
    monkeypatch.setattr("job_applier.linkedin.search.PAUSE", (0, 0))
    results = selftest.run(linkedin=True, client=client)
    by_name = {c.name: c for c in results}
    assert all(c.ok for c in results), selftest.report(results)
    assert "acme: 3 jobs" in by_name["greenhouse: list jobs"].detail
    assert "resume upload found" in by_name["lever: read form"].detail
    assert "would ask about" in by_name["greenhouse: auto-answer"].detail
    assert "linkedin: job details" in by_name


def test_reports_unreachable_boards(client, monkeypatch):
    monkeypatch.setenv("JOBAPPLIER_CHECK_BOARDS", "greenhouse:nope")
    results = selftest.run(linkedin=False, client=client)
    assert [c.ok for c in results] == [False]
    assert "✗ greenhouse: list jobs" in selftest.report(results)


import os

import pytest

from job_applier.ats.base import JobPosting
from job_applier.forms.html_parser import parse_form

from .conftest import FIXTURES


@pytest.mark.skipif("JOBAPPLIER_CHROMIUM" not in os.environ, reason="no Chromium available")
def test_browser_check_fills_without_submitting():
    html = FIXTURES / "lever_apply.html"
    posting = JobPosting(
        ats="lever", company_token="globex", company_name="Globex", external_id="x", title="Engineer",
        location="Seattle, WA", workplace="onsite", url="", apply_url=html.as_uri(), description="", posted_at=None,
    )
    sample = selftest.Sample(posting, parse_form(html.read_text(), "form#application-form"))
    check = selftest.check_browser_fill(sample)
    # Name, email, phone, company, LinkedIn, work authorization and gender get filled.
    assert check.ok, check.detail
    assert check.detail.startswith("filled 7/7")
