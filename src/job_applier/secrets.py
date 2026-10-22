"""Secrets live in the OS keychain (Windows Credential Manager), never in files.

Environment variables override the keychain, which is handy for testing.
"""

from __future__ import annotations

import os

import keyring
from keyring.errors import KeyringError

SERVICE = "job_applier"

ENV_OVERRIDES = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "ats_password": "JOBAPPLIER_ATS_PASSWORD",
}

KNOWN = tuple(ENV_OVERRIDES)


def get_secret(name: str) -> str | None:
    env = ENV_OVERRIDES.get(name)
    if env and os.environ.get(env):
        return os.environ[env]
    try:
        return keyring.get_password(SERVICE, name)
    except KeyringError:
        return None


def set_secret(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


def has_secret(name: str) -> bool:
    return bool(get_secret(name))
