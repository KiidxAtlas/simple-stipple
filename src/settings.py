"""Settings persistence — load/save a JSON config file in the user's home dir."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.core.persistence import read_json_file, write_json_file_atomic

_SETTINGS_FILE = Path.home() / ".simple_stipple_settings.json"
_LOG = logging.getLogger(__name__)


def _migrate_settings(data: dict) -> dict:
    """Upgrade legacy settings keys to current names."""
    # Migrate shape_output_dir → draft_output_dir
    if "shape_output_dir" in data and "draft_output_dir" not in data:
        data["draft_output_dir"] = data.pop("shape_output_dir")
    # Remove legacy unused setting shape_input_dxf_dir
    data.pop("shape_input_dxf_dir", None)
    return data


def load_settings() -> dict:
    """Load settings from disk with automatic migration of legacy keys."""
    if not _SETTINGS_FILE.exists():
        return {}
    try:
        data = read_json_file(_SETTINGS_FILE, default={})
        if not isinstance(data, dict):
            return {}
        data = _migrate_settings(data)
        return data
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("Failed to load settings from %s: %s", _SETTINGS_FILE, exc)
        return {}


def save_settings(d: dict) -> None:
    """Save settings to disk."""
    try:
        write_json_file_atomic(_SETTINGS_FILE, d)
    except (OSError, TypeError, ValueError) as exc:
        _LOG.warning("Failed to save settings to %s: %s", _SETTINGS_FILE, exc)

