"""Locations of local, personal data. Everything here stays out of git."""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    path = Path(os.environ.get("JOBAPPLIER_DATA_DIR", "data")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def sub(name: str) -> Path:
    path = data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_file() -> Path:
    return data_dir() / "settings.yaml"


def profile_file() -> Path:
    return data_dir() / "profile.yaml"


def resume_file() -> Path:
    return data_dir() / "resume.yaml"


def db_file() -> Path:
    return data_dir() / "jobapplier.db"


def browser_profile_dir() -> Path:
    return sub("browser-profile")


def output_dir(job_id: int) -> Path:
    return sub("output") / str(job_id)


def screenshots_dir() -> Path:
    return sub("screenshots")


def logs_dir() -> Path:
    return sub("logs")
