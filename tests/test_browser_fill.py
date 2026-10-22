"""Fill real forms in Chromium: the Lever-style fixture and custom JS widgets."""

import os

import pytest
from playwright.sync_api import sync_playwright

from job_applier.db import Job
from job_applier.forms.filler import fill_form
from job_applier.forms.html_parser import parse_form
from job_applier.forms.mapping import RESUME, Context, resolve_form
from job_applier.forms.models import FormField, Option
from job_applier.pipeline.submit import Outcome, submit_one
from job_applier.settings import SubmitSettings

from .conftest import FIXTURES

pytestmark = pytest.mark.skipif("JOBAPPLIER_CHROMIUM" not in os.environ, reason="no Chromium available")


@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ["JOBAPPLIER_CHROMIUM"])
        yield browser.new_page()
        browser.close()


@pytest.fixture
def resume_pdf(tmp_path):
    path = tmp_path / "Jordan_Lee_Resume.pdf"
    path.write_bytes(b"%PDF-1.4\n%fake\n")
    return path


def lever_answers(profile):
    html = (FIXTURES / "lever_apply.html").read_text()
    fields = parse_form(html, "form#application-form")
    answers = resolve_form(fields, Context(profile, {}, "Seattle, WA")).answers
    answers["cards[abc][field1]"] = ["Kafka", "Spark"]
    answers["cards[abc][field2]"] = "Seattle"
    return fields, answers


def test_fill_lever_form(page, profile, resume_pdf):
    fields, answers = lever_answers(profile)
    page.goto((FIXTURES / "lever_apply.html").as_uri())
    problems = fill_form(page, fields, answers, {RESUME: resume_pdf}, pace=(0, 0))
    assert problems == []
    assert page.input_value('[name="name"]') == "Jordan Lee"
    assert page.input_value('[name="email"]') == "jordan@example.com"
    assert page.input_value('[name="urls[LinkedIn]"]') == "https://linkedin.com/in/jordanlee"
    assert page.is_checked('[name="cards[abc][field0]"][value="Yes"]')
    assert page.is_checked('[name="cards[abc][field1]"][value="Kafka"]')
    assert page.is_checked('[name="cards[abc][field1]"][value="Spark"]')
    assert page.input_value('[name="cards[abc][field2]"]') == "sea"
    assert page.input_value('[name="eeo[gender]"]') == "Decline to self-identify"
    assert page.eval_on_selector('[name="resume"]', "el => el.files[0].name") == "Jordan_Lee_Resume.pdf"


def _job(fields, answers, url, resume_path):
    return Job(
        id=1, ats="lever", company_token="globex", company_name="Globex", external_id="x", title="Engineer",
        apply_url=url, form_fields=[f.model_dump() for f in fields], answers=answers, resume_path=str(resume_path),
    )


def test_submit_one_detects_success(page, profile, resume_pdf):
    fields, answers = lever_answers(profile)
    job = _job(fields, answers, (FIXTURES / "lever_apply.html").as_uri(), resume_pdf)
    outcome, message, shot = submit_one(page, job, SubmitSettings(dry_run=False, headless=True))
    assert outcome == Outcome.SUBMITTED, message
    assert os.path.exists(shot)


def test_dry_run_does_not_submit(page, profile, resume_pdf):
    fields, answers = lever_answers(profile)
    job = _job(fields, answers, (FIXTURES / "lever_apply.html").as_uri(), resume_pdf)
    outcome, _, _ = submit_one(page, job, SubmitSettings(dry_run=True, headless=True))
    assert outcome == Outcome.DRY_RUN
    assert page.locator("#btn-submit").count() == 1  # still on the form


def test_missing_required_answer_needs_you(page, profile, resume_pdf):
    fields, answers = lever_answers(profile)
    del answers["cards[abc][field2]"]
    job = _job(fields, answers, (FIXTURES / "lever_apply.html").as_uri(), resume_pdf)
    outcome, message, _ = submit_one(page, job, SubmitSettings(dry_run=False, headless=True))
    assert outcome == Outcome.MANUAL and "Preferred office" in message


WIDGETS = """
<html><body>
<div class="field">
  <label for="team">Team</label>
  <input id="team" role="combobox" aria-autocomplete="list" autocomplete="off">
  <ul role="listbox" id="team-list" hidden>
    <li role="option">Core Platform</li><li role="option">Growth</li>
  </ul>
  <input type="hidden" id="team-value">
</div>
<fieldset>
  <legend>Do you require visa sponsorship?</legend>
  <button type="button" onclick="pick(this)">Yes</button>
  <button type="button" onclick="pick(this)">No</button>
</fieldset>
<div id="picked"></div>
<script>
  const input = document.getElementById('team'), list = document.getElementById('team-list');
  input.addEventListener('input', () => { list.hidden = false; });
  list.querySelectorAll('[role=option]').forEach(o => o.addEventListener('click', () => {
    document.getElementById('team-value').value = o.textContent; input.value = o.textContent; list.hidden = true;
  }));
  function pick(b) { document.getElementById('picked').textContent = b.textContent; }
</script>
</body></html>
"""


def test_custom_widgets(page):
    page.set_content(WIDGETS)
    fields = [
        FormField(key="team", label="Team", type="select", required=True,
                  options=[Option(label="Core Platform", value="core"), Option(label="Growth", value="growth")]),
        FormField(key="q_sponsor", label="Do you require visa sponsorship?", type="boolean", required=True,
                  options=[Option(label="Yes", value="true"), Option(label="No", value="false")]),
    ]
    problems = fill_form(page, fields, {"team": "Core Platform", "q_sponsor": "No"}, {}, pace=(0, 0))
    assert problems == []
    assert page.input_value("#team-value") == "Core Platform"
    assert page.inner_text("#picked") == "No"
