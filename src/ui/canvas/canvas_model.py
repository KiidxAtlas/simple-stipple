"""Reactive Qt adapter around the backend canvas document."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from src.backend.model.document import CanvasDocument


class CanvasModel(QObject):
    """Owns canvas document state and publishes coarse UI invalidations."""

    document_replaced = Signal()
    geometry_changed = Signal()
    selection_changed = Signal(int)

    def __init__(
        self, document: CanvasDocument | None = None, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._document = document or CanvasDocument()

    @property
    def document(self) -> CanvasDocument:
        return self._document

    def replace_document(self, document: CanvasDocument) -> None:
        self._document = document
        self.document_replaced.emit()
        self.geometry_changed.emit()
        self.selection_changed.emit(len(document.selection))

    def notify_geometry_changed(self) -> None:
        self.geometry_changed.emit()

    def notify_selection_changed(self) -> None:
        self.selection_changed.emit(len(self._document.selection))
