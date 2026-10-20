"""Base page class for all top-level workspace pages."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget


class ModelField:
    """Expose one page-model attribute as a page attribute.

    Pages kept their historical ``self._name`` spelling when their state
    moved into a model object; declaring each alias as a ``ModelField`` keeps
    that spelling explicit and visible to tools, instead of routing every
    attribute lookup through ``__getattr__``/``__setattr__``.
    """

    def __init__(self, field: str) -> None:
        self.field = field

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def __get__(self, page: Any, owner: type | None = None) -> Any:
        if page is None:
            return self
        return getattr(page._model, self.field)

    def __set__(self, page: Any, value: Any) -> None:
        setattr(page._model, self.field, value)


class BasePage(QWidget):
    """Common base for all top-level workspace pages.

    Provides:
    - ``stateChanged`` signal (workspace persistence hook)
    - ``_settings`` dict initialisation
    - ``_suspend_state`` flag + ``_emit_state_changed()``
    - Default no-op implementations of the workspace/preset state protocol
    """

    stateChanged = Signal()

    def run(self) -> None:
        """Start the page's primary operation."""
        self._run()

    def _run(self) -> None:
        raise NotImplementedError

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


__all__ = ["BasePage", "ModelField"]
