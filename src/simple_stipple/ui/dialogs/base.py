"""Template pattern for simple field-entry dialogs (plan.md Section 8.4 /
Phase 3.2): a title, a content area, and an OK/Cancel button box are common
boilerplate that every dialog in ``src/simple_stipple/ui/widgets/dialogs/`` currently
rebuilds by hand. ``BaseDialog`` owns that boilerplate once; subclasses
implement ``create_content()`` (required) and optionally ``validate()``/
``on_accepted()``.
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QWidget


class BaseDialog(QDialog):
    """Base for OK/Cancel field-entry dialogs.

    Subclasses override:

    * ``create_content(layout)`` — required. Build the dialog's form into
      the given ``QVBoxLayout`` (nest a ``QFormLayout`` or anything else).
    * ``validate() -> str | None`` — optional. Return an error message to
      block accept, or ``None`` (the default) to allow it.
    * ``on_accepted()`` — optional. Runs after ``validate()`` passes, before
      the dialog closes — for side effects that should only happen on a
      real accept (persisting a setting, emitting a signal, etc).
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "",
        ok_text: str = "OK",
        cancel_text: str = "Cancel",
    ) -> None:
        super().__init__(parent)
        if title:
            self.setWindowTitle(title)
        self._root_layout = QVBoxLayout(self)
        self.create_content(self._root_layout)
        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._button_box.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text)
        self._button_box.button(QDialogButtonBox.StandardButton.Cancel).setText(cancel_text)
        self._button_box.accepted.connect(self._handle_accept)
        self._button_box.rejected.connect(self.reject)
        self._root_layout.addWidget(self._button_box)

    def create_content(self, layout: QVBoxLayout) -> None:
        """Build the dialog's form fields into ``layout``. Required override."""
        raise NotImplementedError

    def validate(self) -> str | None:
        """Return an error message to block accept, or None to allow it."""
        return None

    def on_accepted(self) -> None:
        """Runs after validate() passes, before the dialog closes."""

    def _handle_accept(self) -> None:
        error = self.validate()
        if error:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self, self.windowTitle() or "Invalid input", error)
            return
        self.on_accepted()
        self.accept()


__all__ = ["BaseDialog"]
