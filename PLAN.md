# Job Applier — Implementation Plan

A local, daily job-search-and-apply assistant. It finds jobs, scores them against
your profile, suggests small truthful resume tweaks, asks you for anything it is
missing, and submits applications you approve on LinkedIn (Easy Apply) and on
company ATS sites.

## Decisions so far

| Area | Decision |
|---|---|
| LinkedIn | **Assisted Easy Apply**: drives a real, logged-in browser profile on your PC, keeps to daily caps and paces itself like a person, and submits only after you approve |
| Autonomy | **Daily review queue**: discover → score → tailor → pre-fill, then you approve in one batch, then it submits |
| Runtime | **Your own Windows PC**, run daily by Windows Task Scheduler |
| LLM | **Pluggable**: Anthropic (Claude), OpenAI or Ollama, picked in config |
| Sources | LinkedIn, **Greenhouse / Lever / Ashby**, **Workday / iCIMS / Taleo** |
| UI | **Local web dashboard** at `http://localhost:8765` |
| Resume | **Structured master resume (YAML)**: imported once from your DOCX/PDF, then rendered to PDF and DOCX from a clean ATS-friendly template (reason below) |
| Tailoring | **Suggest and ask**: reword, reorder and mirror keywords only where true. If a job wants a skill your resume doesn't show, it **asks you** before adding it. Nothing is invented, and every change is shown as a diff you accept or reject |
| Filters | Location / remote, seniority & title (include and exclude lists) |
| Daily caps | **Set per source** (e.g. LinkedIn 10, Greenhouse 15 …) |
| Cover letters | Only when the form **requires** one; you can edit the draft before it is sent |
| ATS accounts | **One shared password** for Workday/iCIMS/Taleo sign-ups, stored in Windows Credential Manager (never in files or git) |
| EEO questions | **Your saved answers**, entered once |
| Unknown questions | **Hold & ask, then remember**: the application waits, the question shows up in the dashboard, and your answer goes into a reusable answer bank |
| Stack | Python 3.12, Playwright, FastAPI + HTMX/Jinja2, SQLite (SQLModel), `keyring` |

### Why a structured resume rather than editing the DOCX in place
Editing a DOCX in place is fragile: the text is split across "runs", and the
formatting tends to break once bullets are reordered. A YAML master resume
makes every change an exact, reviewable diff, gives the same layout every time,
and renders to an ATS-safe PDF and DOCX. You still start from your existing
file: the importer reads it, you check the result once, and you can change the
template's look.

## Architecture

```
job_applier/

  data/              ← gitignored: settings, profile, resume.yaml, answer bank, DB, browser profile, outputs
  src/job_applier/
    llm/             provider interface + anthropic / openai / ollama adapters (structured JSON output)
    profile/         resume import (DOCX/PDF → YAML), profile model, answer bank, EEO answers
    resume/          tailoring engine (suggestions + diff), renderer (HTML→PDF via Playwright, DOCX via python-docx)
    sources/         linkedin, greenhouse, lever, ashby, workday, icims, taleo  → normalized Job records
    pipeline/        dedupe → hard filters → LLM match score + gap analysis → questions → cover letter
    appliers/        one form-filler per site; field-mapping → profile/answer bank; unknown → hold & ask
    browser/         persistent Playwright context, human-like pacing, CAPTCHA/checkpoint detection
    web/             FastAPI dashboard (queue, job detail, diffs, questions, history, settings)
    scheduler/       `register-task` for Windows Task Scheduler
    cli.py           `jobapplier setup | run-daily | dashboard | submit-approved | register-task`
```

### Daily flow
1. **Discover**: search LinkedIn with your keywords and locations, and pull ATS job
   boards for known companies.
2. **Filter**: remove duplicates, check location/remote, seniority and title rules,
   and drop anything already applied to.
3. **Score**: the LLM gives a 0–100 match with reasons and finds gaps (skills the
   job wants that your resume doesn't show).
4. **Prepare**: resume suggestions, questions for you ("Do you have Terraform
   experience?"), pre-filled form answers, and a cover letter when one is required.
5. **Review** (you, in the dashboard): accept or reject edits, answer questions,
   then approve or skip.
6. **Submit**: approved applications go out within the per-source caps. A
   screenshot of every submitted form is kept. If a CAPTCHA, email verification
   or LinkedIn checkpoint appears, the run pauses and you finish that step in the
   browser window.

### How jobs are found on each source
- **LinkedIn**: searches in your logged-in browser profile. Easy Apply jobs go to
  the LinkedIn applier. "Apply on company site" links are routed to the right ATS
  applier, and they also add new companies to the ATS list automatically.
- **Greenhouse / Lever / Ashby**: their public job-board JSON APIs, for each company.
- **Workday**: the site's own JSON search endpoint (`/wday/cxs/...`), for each company.
- **iCIMS / Taleo**: browser scraping, for each company.
- ATS boards have no global search, so they need a **company list**. It starts
  from a list you give and grows from LinkedIn links.

### Reliability tiers (setting expectations)
- **High**: Greenhouse, Lever, Ashby. Simple forms that rarely change.
- **Medium**: LinkedIn Easy Apply. Works, but the UI changes often and there is a
  ToS/ban risk, which caps and pacing reduce.
- **Best-effort**: Workday, iCIMS, Taleo. Forms differ by company, and the account
  creation and email-verification steps will often need you.

## Milestones

Status: milestones **1–4 are built** (Greenhouse/Lever/Ashby first, as agreed). Remaining: 5–7.
Live API shapes are covered by recorded fixtures and still need a first real run on your PC
(this sandbox can't reach the job boards).

1. ✅ **Foundation**: project skeleton, config, SQLite models, LLM provider
   interface, resume import → YAML, PDF/DOCX rendering, first-run setup wizard
   in the dashboard.
2. ✅ **Discovery + queue**: Greenhouse/Lever/Ashby sources (Workday moves to 6), filters,
   scoring, dashboard queue.
3. ✅ **Tailoring + ask flow**: suggestion diffs, gap questions, answer bank, EEO
   answers, cover letters.
4. ✅ **ATS appliers (high tier)**: Greenhouse, Lever, Ashby, with a dry-run mode
   that fills forms without submitting.
5. **LinkedIn**: search plus assisted Easy Apply.
6. **Workday / iCIMS / Taleo** appliers (best-effort).
7. **Scheduling + polish**: Task Scheduler registration, run reports, retries.

Each milestone ships with unit tests (parsers, field mapping, filters) and
recorded HTML fixtures for the form-fillers.

## Information the app will ask for (first-run wizard)
None of this goes into git. It stays in `data/` on your PC.
- Resume file (DOCX or PDF)
- Contact details: name, email, phone, city, LinkedIn/GitHub/portfolio URLs
- Target titles / keywords, excluded titles, seniority levels
- Locations, plus remote / hybrid / onsite preference
- Work authorization and sponsorship needs, notice period / earliest start date
- Salary expectation (only for forms that require it)
- EEO answers
- Seed company list for the ATS sources (optional; it also grows automatically)
- Daily cap per source, and the time of day for the daily run
- LLM provider + API key (kept in Credential Manager)
- Shared ATS password (kept in Credential Manager)

## Safety rules (built in)
- Never invents experience, degrees, titles or dates. A new skill is added only
  after you confirm it.
- Never submits anything you haven't approved.
- Stops and alerts you on CAPTCHA or LinkedIn checkpoints instead of trying to
  get around them.
- Keeps to the caps per source and per day, with randomized human-like delays.
- Keeps secrets in Windows Credential Manager. `data/` is gitignored.
