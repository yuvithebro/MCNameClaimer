"""Where the app keeps its settings, encrypted credentials and logs."""

from __future__ import annotations

import os
from pathlib import Path

from . import APP_NAME


def app_data_dir() -> Path:
    """%LOCALAPPDATA%\\MCNameClaimer on Windows. MCCLAIMER_HOME overrides it (used by tests)."""
    override = os.environ.get("MCCLAIMER_HOME")
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def credentials_path() -> Path:
    return app_data_dir() / "credentials.bin"


def log_dir() -> Path:
    return app_data_dir() / "logs"
