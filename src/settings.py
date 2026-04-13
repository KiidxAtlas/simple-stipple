"""Settings persistence — load/save a JSON config file in the user's home dir."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.core.persistence import read_json_file, write_json_file_atomic

_SETTINGS_FILE = Path.home() / ".simple_stipple_settings.json"
_LOG = logging.getLogger(__name__)


def load_settings() -> dict:
    if not _SETTINGS_FILE.exists():
        return {}
    try:
        data = read_json_file(_SETTINGS_FILE, default={})
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("Failed to load settings from %s: %s", _SETTINGS_FILE, exc)
        return {}


def save_settings(d: dict) -> None:
    try:
        write_json_file_atomic(_SETTINGS_FILE, d)
    except (OSError, TypeError, ValueError) as exc:
        _LOG.warning("Failed to save settings to %s: %s", _SETTINGS_FILE, exc)
