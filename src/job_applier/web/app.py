"""Local dashboard: review queue, questions, resume diffs, companies and setup."""

from __future__ import annotations

import logging
import tempfile
import threading
from pathlib import Path
from typing import get_args
from urllib.parse import quote

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import func, select

from job_applier import db, secrets
from job_applier.ats import SUPPORTED, ATSError, detect_company
from job_applier.linkedin import session as li_session
from job_applier.db import (
    AnswerBankEntry,
    Company,
    Job,
    JobStatus,
    Question,
    QuestionStatus,
    Run,
    Suggestion,
)
from job_applier.forms.models import FormField
from job_applier.llm import LLMError, get_llm
from job_applier.pipeline import ai, review
from job_applier.pipeline.answers import answer_question, open_questions
from job_applier.pipeline.run import Busy, run_daily, run_submit
from job_applier.profile import importer, store
from job_applier.profile.models import EEO, Profile, Resume
from job_applier.settings import Settings, Workplace, load_settings, save_settings

log = logging.getLogger("job_applier")
HERE = Path(__file__).parent
app = FastAPI(title="Job Applier")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


def go(url: str, msg: str | None = None, error: bool = False) -> RedirectResponse:
    if msg:
        url += ("&" if "?" in url else "?") + ("err=" if error else "msg=") + quote(msg)
    return RedirectResponse(url, status_code=303)


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    with db.session() as s:
        nav = {
            "questions": s.exec(select(func.count()).select_from(Question).where(Question.status == QuestionStatus.OPEN)).one(),
            "ready": s.exec(select(func.count()).select_from(Job).where(Job.status == JobStatus.READY)).one(),
            "approved": s.exec(select(func.count()).select_from(Job).where(Job.status == JobStatus.APPROVED)).one(),
        }
    return templates.TemplateResponse(
        request,
        name,
        {
            "nav": nav,
            "msg": request.query_params.get("msg"),
            "err": request.query_params.get("err"),
            "settings": load_settings(),
            "running": _worker is not None and _worker.is_alive(),
            **ctx,
        },
    )


# ---------- background runs ----------

_worker: threading.Thread | None = None


def _start(target, *args) -> bool:
    global _worker
    if _worker is not None and _worker.is_alive():
        return False

    def wrapped():
        try:
            target(*args)
        except Busy:
            log.warning("Run skipped: another run is in progress")
        except Exception:
            log.exception("Background run failed")

    _worker = threading.Thread(target=wrapped, daemon=True)
    _worker.start()
    return True


@app.post("/run/daily")
def start_daily():
    if not _start(run_daily):
        return go("/runs", "A run is already in progress", True)
    return go("/runs", "Search started: new matches will appear in the queue")


@app.post("/run/submit")
def start_submit():
    if not _start(run_submit):
        return go("/runs", "A run is already in progress", True)
    return go("/runs", "Submitting approved applications: watch the browser window")


@app.get("/runs", response_class=HTMLResponse)
def runs(request: Request):
    with db.session() as s:
        rows = s.exec(select(Run).order_by(Run.id.desc()).limit(30)).all()
    return render(request, "runs.html", runs=rows)


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(request: Request, run_id: int):
    with db.session() as s:
        run = s.get(Run, run_id)
    if run is None:
        raise HTTPException(404)
    return render(request, "run.html", run=run)


# ---------- queue & jobs ----------

@app.get("/", response_class=HTMLResponse)
def queue(request: Request):
    groups = {}
    with db.session() as s:
        for status in (
            JobStatus.NEEDS_INPUT, JobStatus.READY, JobStatus.APPROVED, JobStatus.MANUAL, JobStatus.APPLY_YOURSELF, JobStatus.FAILED,
        ):
            groups[status.value] = s.exec(
                select(Job).where(Job.status == status).order_by(Job.score.desc(), Job.discovered_at.desc())
            ).all()
        companies = s.exec(select(func.count()).select_from(Company)).one()
    setup_todo = []
    if not store.has_resume():
        setup_todo.append("Import your resume")
    missing = store.load_profile().missing_essentials()
    if missing:
        setup_todo.append("Complete your profile: " + ", ".join(missing))
    if not companies:
        setup_todo.append("Add companies to watch")
    if load_settings().llm.provider in ("anthropic", "openai") and not secrets.has_secret(f"{load_settings().llm.provider}_api_key"):
        setup_todo.append("Add your LLM API key")
    return render(request, "queue.html", groups=groups, setup_todo=setup_todo)


def _job_or_404(s, job_id: int) -> Job:
    job = s.get(Job, job_id)
    if job is None:
        raise HTTPException(404)
    return job


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int):
    with db.session() as s:
        job = _job_or_404(s, job_id)
        suggestions = s.exec(select(Suggestion).where(Suggestion.job_id == job_id).order_by(Suggestion.id)).all()
        questions = open_questions(s, job_id)
    fields = [FormField.model_validate(f) for f in job.form_fields or []]
    return render(request, "job.html", job=job, suggestions=suggestions, questions=questions, fields=fields)


@app.post("/jobs/{job_id}/approve")
def approve_job(job_id: int):
    with db.session() as s:
        try:
            review.approve(s, job_id)
        except review.ReviewError as exc:
            return go(f"/jobs/{job_id}", str(exc), True)
    return go("/", "Approved: it will be submitted on the next run, or click Submit approved now")


@app.post("/jobs/{job_id}/skip")
def skip_job(job_id: int):
    with db.session() as s:
        review.skip(s, job_id)
    return go("/", "Skipped")


@app.post("/jobs/{job_id}/mark-submitted")
def mark_submitted(job_id: int):
    with db.session() as s:
        job = _job_or_404(s, job_id)
        job.status, job.submitted_at, job.status_reason = JobStatus.SUBMITTED, db.utcnow(), "Finished by you"
        s.add(job)
        s.commit()
    return go("/", "Marked as submitted")


@app.post("/jobs/{job_id}/answers")
async def save_answers(request: Request, job_id: int):
    form = await request.form()
    updates = {k[len("field:"):]: str(v) for k, v in form.items() if k.startswith("field:")}
    with db.session() as s:
        review.update_answers(s, job_id, updates)
    return go(f"/jobs/{job_id}", "Answers saved")


@app.post("/jobs/{job_id}/cover-letter")
def save_cover_letter(job_id: int, text: str = Form("")):
    with db.session() as s:
        job = _job_or_404(s, job_id)
        job.cover_letter = text.strip() or None
        s.add(job)
        s.commit()
    return go(f"/jobs/{job_id}", "Cover letter saved")


@app.post("/jobs/{job_id}/accept-all")
def accept_all(job_id: int):
    with db.session() as s:
        review.accept_all(s, job_id)
    return go(f"/jobs/{job_id}", "Accepted all pending edits")


@app.post("/suggestions/{sid}/{action}")
def suggestion_action(sid: int, action: str):
    with db.session() as s:
        try:
            sug = review.set_suggestion(s, sid, action == "accept")
        except review.ReviewError as exc:
            return go("/", str(exc), True)
    return go(f"/jobs/{sug.job_id}#resume")


@app.get("/jobs/{job_id}/resume.pdf")
def resume_pdf(job_id: int):
    with db.session() as s:
        job = _job_or_404(s, job_id)
        if job.status not in (JobStatus.APPROVED, JobStatus.SUBMITTED) or not job.resume_path or not Path(job.resume_path).exists():
            review.build_documents(s, job)
            s.commit()
    return FileResponse(job.resume_path, media_type="application/pdf")


@app.get("/jobs/{job_id}/screenshot")
def screenshot(job_id: int):
    with db.session() as s:
        job = _job_or_404(s, job_id)
    if not job.screenshot_path or not Path(job.screenshot_path).exists():
        raise HTTPException(404)
    return FileResponse(job.screenshot_path)


@app.get("/history", response_class=HTMLResponse)
def history(request: Request, status: str = "submitted"):
    with db.session() as s:
        statuses = [JobStatus(status)] if status in JobStatus._value2member_map_ else [JobStatus.SUBMITTED]
        jobs = s.exec(
            select(Job).where(Job.status.in_(statuses)).order_by(Job.submitted_at.desc(), Job.discovered_at.desc()).limit(300)
        ).all()
    return render(request, "history.html", jobs=jobs, status=statuses[0].value, statuses=[s.value for s in JobStatus])


# ---------- questions & answer bank ----------

@app.get("/questions", response_class=HTMLResponse)
def questions(request: Request):
    with db.session() as s:
        qs = open_questions(s)
        jobs = {j.id: j for j in s.exec(select(Job).where(Job.id.in_({q.job_id for q in qs}))).all()} if qs else {}
        bank = s.exec(select(AnswerBankEntry).order_by(AnswerBankEntry.updated_at.desc())).all()
    # Show each distinct question once; answering it fills every job that asked it.
    seen, unique = set(), []
    for q in qs:
        if q.key in seen and q.reusable:
            continue
        seen.add(q.key)
        unique.append(q)
    counts = {q.key: sum(1 for x in qs if x.key == q.key) for q in unique}
    return render(request, "questions.html", questions=unique, jobs=jobs, counts=counts, bank=bank)


@app.post("/questions/{qid}")
def answer(request: Request, qid: int, answer: str = Form(""), reuse: str | None = Form(None), back: str = Form("/questions")):
    if not back.startswith("/") or back.startswith("//"):
        back = "/questions"
    with db.session() as s:
        n = answer_question(s, qid, answer, reuse=reuse == "on")
    if not n:
        return go(back, "Please enter an answer", True)
    return go(back, f"Saved, filled {n} question{'s' if n > 1 else ''}")


@app.post("/answers/delete")
def delete_answer(key: str = Form(...)):
    with db.session() as s:
        entry = s.get(AnswerBankEntry, key)
        if entry:
            s.delete(entry)
            s.commit()
    return go("/questions", "Removed from the answer bank")


# ---------- companies ----------

@app.get("/companies", response_class=HTMLResponse)
def companies(request: Request):
    with db.session() as s:
        rows = s.exec(select(Company).order_by(Company.name)).all()
        open_counts = dict(
            s.exec(select(Job.company_token, func.count()).where(Job.status.not_in([JobStatus.FILTERED_OUT])).group_by(Job.company_token)).all()
        )
    return render(request, "companies.html", companies=rows, supported=SUPPORTED, open_counts=open_counts)


@app.post("/companies")
def add_company(name: str = Form(...), ats: str = Form(""), token: str = Form("")):
    name = name.strip()
    found: list[tuple[str, str]]
    if ats and token:
        found = [(ats, token.strip())]
    else:
        try:
            found = detect_company(name)
        except ATSError as exc:
            return go("/companies", str(exc), True)
    if not found:
        return go(
            "/companies",
            f"No Greenhouse/Lever/Ashby board found for '{name}'. Paste its careers-board URL, or pick the ATS and token.",
            True,
        )
    added = []
    with db.session() as s:
        for ats_name, tok in found:
            display = name if "/" not in name else tok
            s.add(Company(name=display, ats=ats_name, token=tok))
            try:
                s.commit()
                added.append(f"{ats_name}:{tok}")
            except IntegrityError:
                s.rollback()
    return go("/companies", f"Added {', '.join(added)}" if added else "Already watching that company")


@app.post("/companies/{cid}/toggle")
def toggle_company(cid: int):
    with db.session() as s:
        company = s.get(Company, cid)
        if company:
            company.active = not company.active
            s.add(company)
            s.commit()
    return go("/companies")


@app.post("/companies/{cid}/delete")
def delete_company(cid: int):
    with db.session() as s:
        company = s.get(Company, cid)
        if company:
            s.delete(company)
            s.commit()
    return go("/companies", "Removed")


# ---------- setup ----------

def _profile_fields(profile: Profile) -> list[dict]:
    fields = []
    for name, info in Profile.model_fields.items():
        if name == "eeo":
            continue
        value = getattr(profile, name)
        ann = str(info.annotation)
        kind = "bool" if "bool" in ann else "list" if "list" in ann else "int" if "int" in ann else "text"
        fields.append({"name": name, "label": name.replace("_", " ").capitalize(), "kind": kind, "value": value})
    return fields


@app.get("/setup", response_class=HTMLResponse)
def setup(request: Request):
    profile = store.load_profile()
    resume_yaml = yaml.safe_dump(store.load_resume().model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=100) if store.has_resume() else ""
    return render(
        request,
        "setup.html",
        profile=profile,
        profile_fields=_profile_fields(profile),
        eeo_fields=list(EEO.model_fields),
        resume_yaml=resume_yaml,
        has_secret={k: secrets.has_secret(k) for k in secrets.KNOWN},
        workplaces=get_args(Workplace),
        missing=profile.missing_essentials(),
        linkedin=li_session.status(),
        experience_levels=EXPERIENCE_LEVELS,
    )


EXPERIENCE_LEVELS = {1: "Internship", 2: "Entry level", 3: "Associate", 4: "Mid-Senior", 5: "Director", 6: "Executive"}


@app.post("/linkedin/login")
def linkedin_login():
    if not _start(li_session.login_interactively):
        return go("/setup#linkedin", "Busy: wait for the current run to finish", True)
    return go("/setup#linkedin", "A browser window opened: sign in to LinkedIn there (2FA is fine). This page shows the result once you're done.")


@app.post("/setup/resume")
async def upload_resume(file: UploadFile = File(...)):
    suffix = Path(file.filename or "resume.pdf").suffix.lower()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)
    settings = load_settings()
    try:
        llm = get_llm(settings.llm)
        resume = importer.import_resume(llm, tmp_path)
    except (LLMError, ValueError) as exc:
        return go("/setup", f"Import failed: {exc}", True)
    finally:
        tmp_path.unlink(missing_ok=True)
    store.save_resume(resume)
    store.save_profile(importer.prefill_profile(store.load_profile(), resume))
    msg = "Resume imported. Check the YAML below and fix anything the import got wrong."
    if not settings.search.titles_include and not settings.search.keywords:
        try:
            sug = ai.suggest_search(llm, resume)
            settings.search.titles_include, settings.search.keywords = sug.titles, sug.keywords
            save_settings(settings)
            msg += " Search titles and keywords were suggested from it; review them under Search."
        except LLMError:
            pass
    return go("/setup", msg)


@app.post("/setup/resume-yaml")
def save_resume_yaml(content: str = Form(...)):
    try:
        resume = Resume.model_validate(yaml.safe_load(content) or {})
    except (yaml.YAMLError, ValidationError) as exc:
        return go("/setup#resume", f"Resume not saved: {exc}", True)
    store.save_resume(resume)
    return go("/setup#resume", "Resume saved")


@app.post("/setup/suggest-search")
def suggest_search():
    settings = load_settings()
    try:
        sug = ai.suggest_search(get_llm(settings.llm), store.load_resume())
    except LLMError as exc:
        return go("/setup#search", str(exc), True)
    settings.search.titles_include = sug.titles
    settings.search.keywords = sug.keywords
    save_settings(settings)
    return go("/setup#search", "Titles and keywords suggested from your resume: edit as you like")


def _lines(text: str) -> list[str]:
    return [p.strip() for p in text.replace(",", "\n").splitlines() if p.strip()]


def _tri(value: str) -> bool | None:
    return {"yes": True, "no": False}.get(value)


@app.post("/setup/profile")
async def save_profile(request: Request):
    form = await request.form()
    data: dict = {}
    for f in _profile_fields(Profile()):
        raw = str(form.get(f["name"], "")).strip()
        if f["kind"] == "bool":
            data[f["name"]] = _tri(raw)
        elif f["kind"] == "list":
            data[f["name"]] = _lines(raw)
        elif f["kind"] == "int":
            data[f["name"]] = int(raw) if raw.isdigit() else None
        else:
            data[f["name"]] = raw
    data["eeo"] = {k: str(form.get(f"eeo_{k}", "")).strip() for k in EEO.model_fields}
    try:
        store.save_profile(Profile.model_validate(data))
    except ValidationError as exc:
        return go("/setup#profile", str(exc), True)
    return go("/setup#profile", "Profile saved")


@app.post("/setup/settings")
async def save_settings_form(request: Request):
    form = await request.form()
    s = load_settings()
    data = s.model_dump()
    if form.get("section") == "linkedin":
        li = data["linkedin"]
        li["enabled"] = form.get("li_enabled") == "on"
        li["queries"] = [line.strip() for line in str(form.get("li_queries", "")).splitlines() if line.strip()]
        li["locations"] = [line.strip() for line in str(form.get("li_locations", "")).splitlines() if line.strip()]
        li["posted_within"] = form.get("li_posted_within") or "r604800"
        li["experience_levels"] = [int(x) for x in form.getlist("li_experience")]
        li["pages_per_query"] = int(form.get("li_pages") or 2)
        li["max_details_per_run"] = int(form.get("li_max_details") or 60)
        li["min_delay_s"] = float(form.get("li_min_delay_s") or 60)
        li["max_delay_s"] = float(form.get("li_max_delay_s") or 180)
        try:
            save_settings(Settings.model_validate(data))
        except ValidationError as exc:
            return go("/setup#linkedin", str(exc), True)
        return go("/setup#linkedin", "LinkedIn settings saved")
    search = data["search"]
    for key in ("titles_include", "titles_exclude", "seniority_include", "seniority_exclude", "keywords", "locations"):
        search[key] = [line.strip() for line in str(form.get(key, "")).splitlines() if line.strip()]
    search["workplace"] = form.getlist("workplace")
    search["max_age_days"] = int(form.get("max_age_days") or 21)
    data["min_score"] = int(form.get("min_score") or 60)
    data["max_scored_per_run"] = int(form.get("max_scored_per_run") or 40)
    data["caps"] = {ats: int(form.get(f"cap_{ats}") or 0) for ats in data["caps"]}
    data["llm"].update(
        provider=form.get("llm_provider") or "anthropic",
        model=str(form.get("llm_model") or "").strip() or "claude-opus-5-5",
        effort=form.get("llm_effort") or "medium",
        base_url=str(form.get("llm_base_url") or "").strip() or None,
    )
    data["submit"].update(
        dry_run=form.get("dry_run") == "on",
        headless=form.get("headless") == "on",
        min_delay_s=float(form.get("min_delay_s") or 20),
        max_delay_s=float(form.get("max_delay_s") or 60),
    )
    data["schedule_time"] = str(form.get("schedule_time") or "08:00")
    data["browser_executable"] = str(form.get("browser_executable") or "").strip() or None
    try:
        save_settings(Settings.model_validate(data))
    except ValidationError as exc:
        return go("/setup#search", str(exc), True)
    return go("/setup#search", "Settings saved")


@app.post("/setup/secrets")
async def save_secrets(request: Request):
    form = await request.form()
    saved = []
    for name in secrets.KNOWN:
        value = str(form.get(name, "")).strip()
        if value:
            secrets.set_secret(name, value)
            saved.append(name.replace("_", " "))
    return go("/setup#secrets", f"Saved {', '.join(saved)} to Windows Credential Manager" if saved else "Nothing to save")
