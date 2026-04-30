"""Settings persistence — load/save a JSON config file in the user's home dir."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.backend.io import read_json_file, write_json_file_atomic

_SETTINGS_FILE = Path.home() / ".simple_stipple_settings.json"
_LOG = logging.getLogger(__name__)


DEFAULT_KEYBINDINGS: dict[str, str] = {
    "workspace.new": "Ctrl+N",
    "workspace.open": "Ctrl+O",
    "workspace.save": "Ctrl+S",
    "workspace.save_as": "Ctrl+Shift+S",
    "app.settings": "Ctrl+,",
    "app.command_palette": "Meta+K",
    "canvas.select_mode": "S",
    "canvas.draw_mode": "D",
    "canvas.edit_mode": "E",
    "canvas.measure": "M",
    "canvas.fit": "F",
    "tab.draft": "Alt+1",
    "tab.pattern": "Alt+2",
    "tab.trace": "Alt+3",
    "tab.convert": "Alt+4",
    "tab.repo": "Alt+5",
}


def _migrate_settings(data: dict) -> dict:
    """Upgrade legacy settings keys to current names."""
    keybindings = data.get("keybindings")
    if not isinstance(keybindings, dict):
        data["keybindings"] = dict(DEFAULT_KEYBINDINGS)
    else:
        merged = dict(DEFAULT_KEYBINDINGS)
        for key, value in keybindings.items():
            if isinstance(value, str) and value.strip():
                merged[key] = value.strip()
        data["keybindings"] = merged
    return data


def load_settings() -> dict:
    """Load settings from disk with automatic migration of legacy keys."""
    if not _SETTINGS_FILE.exists():
        return {}
    try:
        data = read_json_file(_SETTINGS_FILE, default={})
        if not isinstance(data, dict):
            _LOG.warning(
                "Settings file %s did not contain a JSON object; resetting.",
                _SETTINGS_FILE,
            )
            _backup_corrupt_settings()
            return {}
        data = _migrate_settings(data)
        return data
    except (OSError, json.JSONDecodeError) as exc:
        # Corrupt file: back it up so the user can recover, but don't keep
        # crashing on every launch.
        _LOG.warning(
            "Failed to load settings from %s (%s); backing up and starting fresh.",
            _SETTINGS_FILE,
            exc,
        )
        _backup_corrupt_settings()
        return {}


def _backup_corrupt_settings() -> None:
    try:
        if not _SETTINGS_FILE.exists():
            return
        backup = _SETTINGS_FILE.with_suffix(_SETTINGS_FILE.suffix + ".corrupt")
        # Overwrite any prior backup so we don't accumulate cruft.
        _SETTINGS_FILE.replace(backup)
        _LOG.info("Backed up corrupt settings to %s", backup)
    except OSError as exc:
        _LOG.debug("Could not back up corrupt settings: %s", exc)


def save_settings(d: dict) -> None:
    """Save settings to disk."""
    try:
        write_json_file_atomic(_SETTINGS_FILE, d)
    except (OSError, TypeError, ValueError) as exc:
        _LOG.warning("Failed to save settings to %s: %s", _SETTINGS_FILE, exc)
