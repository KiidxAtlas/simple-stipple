"""Base page class for all top-level workspace pages."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget


class BasePage(QWidget):
    """Common base for all top-level workspace pages.

    Provides:
    - ``stateChanged`` signal (workspace persistence hook)
    - ``_settings`` dict initialisation
    - ``_suspend_state`` flag + ``_emit_state_changed()``
    - Default no-op implementations of the workspace/preset state protocol
    """

    stateChanged = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        settings: dict | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._suspend_state: bool = False

    # ── State-change protocol ─────────────────────────────────────────────

    def _emit_state_changed(self) -> None:
        if not self._suspend_state:
            self.stateChanged.emit()

    # ── Workspace / preset protocol (override in subclasses) ──────────────

    def get_workspace_state(self) -> dict:
        return {}

    def apply_workspace_state(self, state: dict | None) -> None:
        pass

    def clear_workspace_state(self) -> None:
        pass

    def get_preset_state(self) -> dict:
        return {}

    def apply_preset_state(self, state: dict | None) -> None:
        pass


__all__ = ["BasePage"]
