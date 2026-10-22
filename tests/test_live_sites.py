"""Opt-in checks against the real sites: JOBAPPLIER_LIVE=1 pytest tests/test_live_sites.py

Read-only. Add JOBAPPLIER_LIVE_BROWSER=1 to also fill (never submit) one real form per site.
"""

import os

import pytest

from job_applier import selftest

pytestmark = pytest.mark.skipif(os.environ.get("JOBAPPLIER_LIVE") != "1", reason="live checks are opt-in")


@pytest.fixture(scope="module")
def results():
    checks = selftest.run(browser=os.environ.get("JOBAPPLIER_LIVE_BROWSER") == "1")
    print("\n" + selftest.report(checks))
    return checks


@pytest.mark.parametrize("prefix", ["greenhouse", "lever", "ashby", "linkedin"])
def test_site(results, prefix):
    failures = [f"{c.name}: {c.detail}" for c in results if c.name.startswith(prefix) and not c.ok]
    assert not failures, "\n".join(failures)
