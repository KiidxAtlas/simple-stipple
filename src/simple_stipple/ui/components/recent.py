"""Persistence-backed recent-file lists."""

from __future__ import annotations

import platform as _platform
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import (
    QPoint,
    Signal,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QMenu,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from simple_stipple.platform.settings import save_settings
from simple_stipple.ui.style import (
    icon_path,
)

DEFAULT_LIMIT = 12

KIND_DXF = "dxf"
KIND_FVI = "fvi"
KIND_IMAGE = "image"
KIND_VECTOR = "vector"


def _key(kind: str) -> str:
    return f"recent_files.{kind}"


def _normalize(path: str) -> str:
    """Return a stable absolute path for dedupe purposes."""
    try:
        return str(Path(path).expanduser().resolve(strict=False))
    except (OSError, RuntimeError):
        return str(path)


def record_recent(
    settings: dict,
    kind: str,
    path: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> None:
    """Insert *path* at the head of the MRU list for *kind* and save."""
    if not path:
        return
    norm = _normalize(path)
    key = _key(kind)
    current = settings.get(key)
    if not isinstance(current, list):
        current = []
    # Dedupe by normalized form while keeping the original-cased originals out.
    deduped: list[str] = [norm]
    seen = {norm}
    for entry in current:
        if not isinstance(entry, str) or not entry:
            continue
        n = _normalize(entry)
        if n in seen:
            continue
        seen.add(n)
        deduped.append(n)
        if len(deduped) >= max(1, limit):
            break
    if current == deduped:
        return
    settings[key] = deduped
    save_settings(settings)


def list_recent(
    settings: dict,
    kind: str,
    *,
    exists_only: bool = True,
    limit: int | None = None,
) -> list[str]:
    """Return the MRU list for *kind*, optionally filtering missing files."""
    raw = settings.get(_key(kind))
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry:
            continue
        if exists_only and not Path(entry).exists():
            continue
        out.append(entry)
        if limit is not None and len(out) >= limit:
            break
    return out


def clear_recent(settings: dict, kind: str) -> None:
    """Wipe the MRU list for *kind*."""
    key = _key(kind)
    if key in settings:
        settings[key] = []
        save_settings(settings)


def prune_missing(settings: dict, kinds: Iterable[str] = (KIND_DXF, KIND_IMAGE)) -> None:
    """Drop entries that no longer exist on disk for the given *kinds*."""
    changed = False
    for kind in kinds:
        key = _key(kind)
        raw = settings.get(key)
        if not isinstance(raw, list):
            continue
        kept = [p for p in raw if isinstance(p, str) and p and Path(p).exists()]
        if kept != raw:
            settings[key] = kept
            changed = True
    if changed:
        save_settings(settings)


_KBD_MOD = "Meta" if _platform.system() == "Darwin" else "Ctrl"


class RecentFilesButton(QPushButton):
    """Drop-down button exposing the recent-files MRU for one file kind."""

    fileSelected = Signal(str)

    def __init__(
        self,
        settings: dict,
        kind: str,
        *,
        empty_message: str = "No recent files.",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Recent", parent)
        self.setIcon(QIcon(str(icon_path("chevron_down.svg"))))
        self._settings = settings
        self._kind = kind
        self._empty_message = empty_message
        # Reserve room for both the word and the disclosure icon. At 76 px
        # Fusion elides "Recent" to "Recen", a particularly unhelpful label
        # in the already-dense file source row.
        self.setMinimumWidth(94)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Open recent files")
        self.setToolTip("Pick from recently opened files")
        self.clicked.connect(self._open_menu)

    def _open_menu(self) -> None:
        recent = list_recent(self._settings, self._kind)
        menu = QMenu(self)
        if not recent:
            disabled = menu.addAction(self._empty_message)
            disabled.setEnabled(False)
        else:
            for path in recent:
                item = Path(path)
                label = f"{item.name}    ‹{item.parent.name or item.parent.anchor}›"
                action = menu.addAction(label)
                action.setToolTip(str(item))
                action.triggered.connect(
                    lambda _checked=False, target=path: self.fileSelected.emit(target)
                )
            menu.addSeparator()
            menu.addAction("Clear history", self._clear)
        menu.popup(self.mapToGlobal(QPoint(0, self.height())))

    def _clear(self) -> None:
        clear_recent(self._settings, self._kind)


# ══════════════════════════════════════════════════════════════════════════
# Keyboard-focus policy (generic Qt utility, not page-specific)
# ══════════════════════════════════════════════════════════════════════════
