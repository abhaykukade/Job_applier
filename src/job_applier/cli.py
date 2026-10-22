"""Command line: `jobapplier dashboard | run-daily | submit | import-resume | add-company | ...`."""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _setup_logging() -> None:
    from job_applier import paths

    handlers: list[logging.Handler] = [
        RotatingFileHandler(paths.logs_dir() / "jobapplier.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    ]
    if sys.stderr is not None:  # pythonw.exe has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)


def cmd_dashboard(args) -> None:
    import uvicorn

    from job_applier.settings import load_settings

    port = args.port or load_settings().dashboard_port
    url = f"http://127.0.0.1:{port}"
    if not args.no_browser:
        webbrowser.open(url)
    print(f"Dashboard at {url} (Ctrl+C to stop)")
    uvicorn.run("job_applier.web.app:app", host="127.0.0.1", port=port, log_level="warning")


def cmd_run_daily(args) -> None:
    from job_applier.pipeline.run import run_daily

    run_daily(submit=not args.no_submit)


def cmd_submit(args) -> None:
    from job_applier.pipeline.run import run_submit

    run_submit()


def cmd_import_resume(args) -> None:
    from job_applier.llm import get_llm
    from job_applier.profile import importer, store
    from job_applier.settings import load_settings

    resume = importer.import_resume(get_llm(load_settings().llm), Path(args.path))
    store.save_resume(resume)
    store.save_profile(importer.prefill_profile(store.load_profile(), resume))
    print(f"Imported {resume.basics.name}: {len(resume.experience)} roles. Review it in the dashboard (Setup).")


def cmd_add_company(args) -> None:
    from sqlalchemy.exc import IntegrityError

    from job_applier import db
    from job_applier.ats import detect_company

    for name in args.names:
        found = detect_company(name)
        if not found:
            print(f"✗ {name}: no Greenhouse/Lever/Ashby board found (try its careers-board URL)")
            continue
        with db.session() as s:
            for ats, token in found:
                s.add(db.Company(name=name if "/" not in name else token, ats=ats, token=token))
                try:
                    s.commit()
                    print(f"✓ {name}: {ats} ({token})")
                except IntegrityError:
                    s.rollback()
                    print(f"• {name}: already watching {ats} ({token})")


def cmd_linkedin_login(args) -> None:
    from job_applier.linkedin import session

    print("A browser window will open. Sign in to LinkedIn there (2FA is fine); it closes when you're in.")
    print("Logged in." if session.login_interactively() else "Sign-in didn't finish in time; try again.")


def cmd_set_secret(args) -> None:
    from job_applier import secrets

    if args.name not in secrets.KNOWN:
        sys.exit(f"Unknown secret; choose from: {', '.join(secrets.KNOWN)}")
    secrets.set_secret(args.name, getpass.getpass(f"{args.name}: "))
    print("Saved to the OS credential store.")


def cmd_register_task(args) -> None:
    from job_applier import scheduler
    from job_applier.settings import load_settings

    print(scheduler.register(args.time or load_settings().schedule_time))


def cmd_unregister_task(args) -> None:
    from job_applier import scheduler

    print(scheduler.unregister())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="jobapplier", description=__doc__)
    parser.add_argument("--data-dir", help="Where personal data lives (default: ./data)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("dashboard", help="Open the local dashboard")
    p.add_argument("--port", type=int)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("run-daily", help="Search, score and prepare jobs, then submit approved ones")
    p.add_argument("--no-submit", action="store_true", help="Skip submitting approved applications")
    p.set_defaults(func=cmd_run_daily)

    sub.add_parser("submit", help="Submit approved applications now").set_defaults(func=cmd_submit)

    p = sub.add_parser("import-resume", help="Import a DOCX/PDF resume")
    p.add_argument("path")
    p.set_defaults(func=cmd_import_resume)

    p = sub.add_parser("add-company", help="Watch companies (names or careers-board URLs)")
    p.add_argument("names", nargs="+")
    p.set_defaults(func=cmd_add_company)

    sub.add_parser("linkedin-login", help="Sign in to LinkedIn in the bot's browser").set_defaults(func=cmd_linkedin_login)

    p = sub.add_parser("set-secret", help="Store an API key or password in the OS credential store")
    p.add_argument("name")
    p.set_defaults(func=cmd_set_secret)

    p = sub.add_parser("register-task", help="Run daily with Windows Task Scheduler")
    p.add_argument("--time", help="HH:MM (default: schedule_time in settings)")
    p.set_defaults(func=cmd_register_task)

    sub.add_parser("unregister-task", help="Remove the scheduled task").set_defaults(func=cmd_unregister_task)

    args = parser.parse_args(argv)
    if args.data_dir:
        os.environ["JOBAPPLIER_DATA_DIR"] = args.data_dir
    _setup_logging()
    args.func(args)


if __name__ == "__main__":
    main()
