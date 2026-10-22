"""Load and save the YAML files holding the resume and profile."""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel

from job_applier import paths
from job_applier.profile.models import Profile, Resume

M = TypeVar("M", bound=BaseModel)


def _load(path: Path, model: type[M]) -> M:
    if not path.exists():
        return model()
    return model.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def _save(path: Path, obj: BaseModel) -> None:
    path.write_text(
        yaml.safe_dump(obj.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )


def load_resume() -> Resume:
    return _load(paths.resume_file(), Resume)


def save_resume(resume: Resume) -> None:
    _save(paths.resume_file(), resume)


def has_resume() -> bool:
    return paths.resume_file().exists()


def load_profile() -> Profile:
    return _load(paths.profile_file(), Profile)


def save_profile(profile: Profile) -> None:
    _save(paths.profile_file(), profile)
