"""File dialogs with persistence-backed directory memory."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QFileDialog

from simple_stipple.platform.config import save_settings
from simple_stipple.ui.recent import record_recent

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

def _settings_key(slot: str) -> str:
    """Return the canonical settings key used to remember a dialog directory."""
    return f"dialog_dir.{slot}"


def remembered_dir(settings: dict, slot: str, *, fallback: str = "") -> str:
    """Return the last-remembered directory for *slot*, or *fallback*."""
    value = settings.get(_settings_key(slot))
    if isinstance(value, str) and value:
        return value
    if isinstance(fallback, str) and fallback:
        return fallback
    return ""


def remember_dir(settings: dict, slot: str, path: str) -> None:
    """Persist the parent directory of *path* under *slot*."""
    if not path:
        return
    parent = str(Path(path).expanduser().parent)
    if not parent:
        return
    if settings.get(_settings_key(slot)) == parent:
        return
    settings[_settings_key(slot)] = parent
    save_settings(settings)


def pick_open_file(
    parent: QWidget | None,
    settings: dict,
    slot: str,
    caption: str,
    file_filter: str,
    *,
    fallback_dir: str = "",
    recent_kind: str | None = None,
) -> str:
    """Show an *open file* dialog seeded with the remembered directory.

    When *recent_kind* is provided, a successful pick is also pushed onto the
    matching recent-files MRU (see the recent-files functions above).
    """
    start = remembered_dir(settings, slot, fallback=fallback_dir)
    path, _ = QFileDialog.getOpenFileName(parent, caption, start, file_filter)
    if path:
        remember_dir(settings, slot, path)
        if recent_kind:
            record_recent(settings, recent_kind, path)
    return path


def pick_save_file(
    parent: QWidget | None,
    settings: dict,
    slot: str,
    caption: str,
    default_name: str,
    file_filter: str,
    *,
    fallback_dir: str = "",
) -> str:
    """Show a *save file* dialog seeded with the remembered directory.

    *default_name* is appended to the directory to pre-fill the filename field.
    """
    start_dir = remembered_dir(settings, slot, fallback=fallback_dir)
    if start_dir:
        seed = str(Path(start_dir) / default_name)
    else:
        seed = default_name
    path, _ = QFileDialog.getSaveFileName(parent, caption, seed, file_filter)
    if path:
        remember_dir(settings, slot, path)
    return path


def pick_directory(
    parent: QWidget | None,
    settings: dict,
    slot: str,
    caption: str,
    *,
    fallback_dir: str = "",
) -> str:
    """Show a *choose folder* dialog seeded with the remembered directory."""
    start = remembered_dir(settings, slot, fallback=fallback_dir)
    path = QFileDialog.getExistingDirectory(parent, caption, start)
    if path:
        # Folder picks remember themselves, not the parent.
        if settings.get(_settings_key(slot)) != path:
            settings[_settings_key(slot)] = path
            save_settings(settings)
    return path

