"""Non-secret user settings, saved as JSON next to the encrypted credentials."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .logging_setup import LOGGER_NAME
from .paths import settings_path
from .ratelimit import DEFAULT_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS, MIN_SIMULATION_INTERVAL_SECONDS
from .simulation import DEFAULT_SCENARIO, SCENARIOS

logger = logging.getLogger(f"{LOGGER_NAME}.config")

CLIENT_ID_ENV_VAR = "MCCLAIMER_CLIENT_ID"


@dataclass(frozen=True)
class Settings:
    # The Azure app's client ID is public by design (no secret), so plain JSON is fine.
    client_id: str = ""
    target_username: str = ""
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    simulation_mode: bool = True
    simulation_scenario: str = DEFAULT_SCENARIO


def _coerce(raw: dict) -> Settings:
    defaults = Settings()
    try:
        interval = int(raw.get("interval_seconds", defaults.interval_seconds))
    except (TypeError, ValueError):
        interval = defaults.interval_seconds
    scenario = raw.get("simulation_scenario", defaults.simulation_scenario)
    return Settings(
        client_id=str(raw.get("client_id", "") or "").strip(),
        target_username=str(raw.get("target_username", "") or "").strip(),
        interval_seconds=min(max(interval, MIN_SIMULATION_INTERVAL_SECONDS), MAX_INTERVAL_SECONDS),
        simulation_mode=bool(raw.get("simulation_mode", defaults.simulation_mode)),
        simulation_scenario=scenario if scenario in SCENARIOS else DEFAULT_SCENARIO,
    )


def load_settings(path: Path | None = None) -> Settings:
    path = path or settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = {}
    except (OSError, ValueError):
        logger.warning("Settings file %s is unreadable; using defaults.", path)
        raw = {}
    settings = _coerce(raw if isinstance(raw, dict) else {})
    if not settings.client_id and os.environ.get(CLIENT_ID_ENV_VAR):
        settings = Settings(**{**asdict(settings), "client_id": os.environ[CLIENT_ID_ENV_VAR].strip()})
    return settings


def save_settings(settings: Settings, path: Path | None = None) -> None:
    path = path or settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not save settings (%s).", type(exc).__name__)
