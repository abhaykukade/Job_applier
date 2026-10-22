# Job Applier

A local assistant that runs once a day on your Windows PC. It:

1. **Finds jobs** on the Greenhouse, Lever and Ashby boards of companies you watch, and in LinkedIn
   search results (searched through LinkedIn's public job pages, never your account).
2. **Filters** them by your titles, seniority, keywords, locations and remote/hybrid/onsite preference.
3. **Scores** each match with an AI model: Claude, OpenAI, or a **local model on your own PC**
   (Ollama, LM Studio, llama.cpp, vLLM), so your data never leaves the machine.
4. **Prepares** each application: reads the real form, fills what it can from your profile,
   suggests small, truthful resume edits, and writes a cover letter only when one is required.
5. **Asks you** for anything it doesn't know and remembers the answer for next time.
6. **Submits** what you approve, in a visible browser window, within your daily caps. Dry run is
   **on** by default: it fills the form and takes a screenshot without pressing submit.

Everything personal (resume, profile, answers, database, screenshots) stays in `data/` on your PC
and is git-ignored. API keys and passwords are kept in Windows Credential Manager.

LinkedIn results are routed by how each job is applied to:

- **Easy Apply**: applied to in the bot's browser, logged in as you. This uses your account, so it
  keeps to its own daily cap and pauses of 1 to 3 minutes between applications.
- **Company site on Greenhouse, Lever or Ashby**: the company is added to your watch list and
  handled like any other board.
- **Any other company site** (Workday etc.): listed under *Apply on company site* with a link and
  a tailored resume, for you to apply yourself.

> Automating applications is against LinkedIn's User Agreement and can get an account restricted.
> Keep the LinkedIn cap low. If LinkedIn shows a security check, the bot stops and leaves it to you.

Workday/iCIMS/Taleo applications come in a later milestone; see [PLAN.md](PLAN.md).

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

4. **LinkedIn** (optional, Setup → LinkedIn): tick *Search LinkedIn*, then click **Log in to
   LinkedIn** and sign in yourself in the window that opens. 2FA is fine, and your password is never
   stored: only the browser session is kept, in `data/browser-profile`.

5. **Companies**: add companies by name (auto-detects Greenhouse/Lever/Ashby) or paste a
   careers-board URL such as `https://boards.greenhouse.io/acme` or `https://jobs.lever.co/acme`.

6. Click **Search now** for a first run, then answer **Questions** and review the **Queue**.

7. Schedule the daily run (your PC needs to be on and you signed in at that time):

   ```powershell
   jobapplier register-task --time 08:00
   ```

## Using a local AI model (optional)

Everything the AI does (resume import, scoring, tailoring, cover letters, answer drafts) can run
on your PC instead of a cloud API. Nothing leaves your machine and there's no per-use cost, but it
is slower and the resume edits are weaker than from a large cloud model.

**Ollama** (easiest):

1. Install it from ollama.com (Windows installer). It runs in the background.
2. Pull a model, e.g. `ollama pull qwen2.5:14b`. As a rough guide, 7–8B models need about 8 GB of
   RAM/VRAM, 14B about 12–16 GB, and 32B about 24 GB+. Bigger models tailor resumes better.
3. In **Setup → Search & limits → AI model**, pick *Ollama (local)*. The installed models appear
   in the Model box. Click **Test AI model** to check it works.

**LM Studio, llama.cpp `llama-server`, vLLM, Jan, LocalAI**: start the server, load a model, then
pick *Local server* and set **Base URL** if it's not LM Studio's default `http://localhost:1234/v1`
(llama-server: `http://localhost:8080/v1`).

Tips:

- **Context size** (default 16k tokens) must fit your resume plus a job description. If a prompt
  doesn't fit, the run says so instead of quietly dropping part of your resume. More context uses
  more memory.
- Lower *Max jobs scored per run* if daily runs take too long: each job takes 2–3 model calls.
- Replies that aren't valid JSON are retried once, and `<think>` blocks from reasoning models are
  ignored.

## Daily routine

- The scheduled task searches, scores and prepares new jobs, then submits anything you approved
  earlier.
- Open the dashboard and:
  - answer **Questions**. A remembered answer fills the same question on every job.
  - open each **Ready** job: accept or reject resume edits, check the answers and the cover
    letter, then **Approve**.
  - click **Submit approved**, or let tomorrow's run submit them.
- LinkedIn only shows its Easy Apply questions inside the apply dialog, so they're read when
  submitting. If it asks something the bot can't answer, the draft is discarded and the question
  appears on the Questions page. Once you answer, the job goes back in the queue without needing
  approval again. If a cover letter turns out to be required, a draft is written for you to review.
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
| `jobapplier check-sites [--browser]` | Read-only check that the live sites still parse |
| `jobapplier linkedin-login` | Sign in to LinkedIn in the bot's browser |
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

## Checking the live sites

Job sites change their pages without notice. To check that the app still reads them correctly:

```powershell
jobapplier check-sites            # job listings, application forms, LinkedIn search (read-only)
jobapplier check-sites --browser  # also fill one real form per site in a hidden browser
```

Nothing is ever submitted. The browser check types an obviously fake test profile into one real
form per site, skips file uploads (some sites upload a file as soon as it's picked), then closes the
page without pressing submit. It uses a few well-known public boards; set e.g.
`JOBAPPLIER_CHECK_BOARDS=greenhouse:stripe;lever:palantir;ashby:openai` to pick others.
LinkedIn Easy Apply itself needs your login, so it's checked by a dry run from the dashboard instead.

The same checks run as opt-in tests: `JOBAPPLIER_LIVE=1 pytest tests/test_live_sites.py`
(add `JOBAPPLIER_LIVE_BROWSER=1` for the browser check).

## Development

```bash
pip install -e ".[dev,openai]"
pytest
```

Tests use recorded API responses (`tests/fixtures/`) and fill real forms in headless Chromium.
To point Playwright at a specific Chromium, set `JOBAPPLIER_CHROMIUM`.
