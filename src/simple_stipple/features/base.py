"""Base page class for all top-level workspace pages."""

from __future__ import annotations

from typing import Any

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
    _MODEL_STATE_FIELDS: dict[str, str] = {}

    def __getattr__(self, name: str) -> Any:
        field = type(self)._MODEL_STATE_FIELDS.get(name)
        model = self.__dict__.get("_model")
        if field is not None and model is not None:
            return getattr(model, field)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        field = type(self)._MODEL_STATE_FIELDS.get(name)
        model = self.__dict__.get("_model")
        if field is not None and model is not None:
            setattr(model, field, value)
            return
        super().__setattr__(name, value)

    def run(self) -> None:
        """Start the page's primary operation."""
        self._run()

    def _run(self) -> None:
        raise NotImplementedError

    def _shutdown_thread(
        self,
        thread,
        cancel=None,
        *,
        timeout: float = 2.0,
    ) -> None:
        """Cancel and join one feature worker before widget teardown."""
        self._shutting_down = True
        if cancel is not None:
            cancel()
        self.blockSignals(True)
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def _shutdown_threads(
        self,
        threads,
        cancel=None,
        *,
        timeout: float = 2.0,
    ) -> None:
        """Cancel and join a group of feature workers before teardown."""
        self._shutting_down = True
        if cancel is not None:
            cancel()
        self.blockSignals(True)
        for thread in threads:
            if thread is not None and thread.is_alive():
                thread.join(timeout=timeout)

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
