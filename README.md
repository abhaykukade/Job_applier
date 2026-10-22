# Job Applier

A local assistant that runs once a day on your Windows PC. It:

1. **Finds jobs** on the Greenhouse, Lever and Ashby boards of companies you watch.
2. **Filters** them by your titles, seniority, keywords, locations and remote/hybrid/onsite preference.
3. **Scores** each match with an AI model (Claude, OpenAI or a local Ollama model).
4. **Prepares** each application: reads the real form, fills what it can from your profile,
   suggests small, truthful resume edits, and writes a cover letter only when one is required.
5. **Asks you** for anything it doesn't know and remembers the answer for next time.
6. **Submits** what you approve, in a visible browser window, within your daily caps. Dry run is
   **on** by default: it fills the form and takes a screenshot without pressing submit.

Everything personal (resume, profile, answers, database, screenshots) stays in `data/` on your PC
and is git-ignored. API keys and passwords are kept in Windows Credential Manager.

> LinkedIn Easy Apply and Workday/iCIMS/Taleo come in later milestones; see [PLAN.md](PLAN.md).

## Install (Windows)

1. Install **Python 3.12** from python.org (tick "Add python.exe to PATH").
2. In PowerShell, from this folder:

   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -e .            # add [openai] for the OpenAI provider
   playwright install chromium
   ```

3. Open the dashboard:

   ```powershell
   jobapplier dashboard
   ```

   It opens `http://127.0.0.1:8765`. Then go to **Setup**:

   - **Keys**: paste your Anthropic API key (or pick another provider under *AI model*).
   - **Resume**: import your DOCX or PDF, then check the structured YAML it produces. Titles and
     keywords for your search are suggested from it.
   - **Profile**: contact details, the countries you can work in, sponsorship, notice period and
     your EEO answers (entered once).
   - **Search & limits**: titles, locations, workplace types, minimum score and daily caps.

4. **Companies**: add companies by name (auto-detects Greenhouse/Lever/Ashby) or paste a
   careers-board URL such as `https://boards.greenhouse.io/acme` or `https://jobs.lever.co/acme`.

5. Click **Search now** for a first run, then answer **Questions** and review the **Queue**.

6. Schedule the daily run (your PC needs to be on and you signed in at that time):

   ```powershell
   jobapplier register-task --time 08:00
   ```

## Daily routine

- The scheduled task searches, scores and prepares new jobs, then submits anything you approved
  earlier.
- Open the dashboard and:
  - answer **Questions**. A remembered answer fills the same question on every job.
  - open each **Ready** job: accept or reject resume edits, check the answers and the cover
    letter, then **Approve**.
  - click **Submit approved**, or let tomorrow's run submit them.
- If a site shows a CAPTCHA or an email-verification step, the browser waits up to 5 minutes for you
  to finish it. Otherwise the job moves to **Needs you** with a screenshot.
- When dry-run screenshots look right, untick **Dry run** in Setup.

## Command line

| Command | What it does |
|---|---|
| `jobapplier dashboard` | Open the dashboard |
| `jobapplier run-daily [--no-submit]` | Full daily run |
| `jobapplier submit` | Submit approved applications now |
| `jobapplier import-resume path\to\resume.docx` | Import a resume |
| `jobapplier add-company Stripe https://jobs.lever.co/acme` | Watch companies |
| `jobapplier set-secret anthropic_api_key` | Store a key in Credential Manager |
| `jobapplier register-task --time 08:00` / `unregister-task` | Windows Task Scheduler |

`--data-dir PATH` (before the command) chooses a different data folder.

## How it stays honest

- Resume edits only rephrase, reorder or mirror the posting's wording for things already on your
  resume. If an edit would add a skill or claim the resume doesn't show, it waits until you confirm
  you really have it. A code check also catches unflagged new terms. Your master resume never
  changes; each job gets its own tailored PDF and DOCX in `data/output/<job id>/`.
- Required questions it can't answer from your profile or answer bank are never guessed. The
  application waits for you. Free-text questions get an AI draft for you to edit.
- Nothing is submitted without your approval.

## Development

```bash
pip install -e ".[dev,openai]"
pytest
```

Tests use recorded API responses (`tests/fixtures/`) and fill real forms in headless Chromium.
To point Playwright at a specific Chromium, set `JOBAPPLIER_CHROMIUM`.
