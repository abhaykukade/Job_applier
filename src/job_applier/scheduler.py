"""Register the daily run with Windows Task Scheduler (or print a cron line elsewhere)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from job_applier import paths

TASK_NAME = "JobApplier Daily"


def _command() -> str:
    python = Path(sys.executable)
    # pythonw.exe avoids a console window popping up each morning.
    windowless = python.with_name("pythonw.exe")
    exe = windowless if windowless.exists() else python
    return f'"{exe}" -m job_applier --data-dir "{paths.data_dir()}" run-daily'


def register(time_hhmm: str) -> str:
    if not re.fullmatch(r"\d{2}:\d{2}", time_hhmm):
        raise ValueError("Time must be HH:MM, e.g. 08:00")
    if os.name != "nt":
        hh, mm = time_hhmm.split(":")
        return f"Not on Windows. Add this line with `crontab -e`:\n{int(mm)} {int(hh)} * * * cd {Path.cwd()} && {_command()}"
    subprocess.run(
        ["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", time_hhmm, "/TN", TASK_NAME, "/TR", _command()],
        check=True,
        capture_output=True,
        text=True,
    )
    return f"Scheduled '{TASK_NAME}' every day at {time_hhmm}. Your PC must be on and you must be signed in."


def unregister() -> str:
    if os.name != "nt":
        return "Not on Windows: remove the line from your crontab."
    subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME], check=True, capture_output=True, text=True)
    return f"Removed '{TASK_NAME}'."
