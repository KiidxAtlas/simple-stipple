"""Base class for field-entry dialogs.

``BaseDialog`` owns the shared title, content area, and OK/Cancel button box;
subclasses provide ``create_content()`` and optionally validation hooks.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

from simple_stipple.ui.components.feedback import announce_accessible


class BaseDialog(QDialog):
    """Base for OK/Cancel field-entry dialogs.

    Subclasses override:

    * ``create_content(layout)`` — required. Build the dialog's form into
      the given ``QVBoxLayout`` (nest a ``QFormLayout`` or anything else).
    * ``validate() -> str | None`` — optional. Return an error message to
      block accept, or ``None`` (the default) to allow it. The message is
      shown inline in the dialog (no extra modal) and announced to
      assistive technology.
    * ``invalid_widget() -> QWidget | None`` — optional. The control that
      caused the ``validate()`` error; it receives keyboard focus so the
      user can correct it immediately.
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
        self._error_label = QLabel()
        self._error_label.setProperty("role", "status-err")
        self._error_label.setWordWrap(True)
        self._error_label.setAccessibleName("Validation error")
        self._error_label.hide()
        self._root_layout.addWidget(self._error_label)
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

    def invalid_widget(self) -> QWidget | None:
        """Return the control to focus after a failed ``validate()``."""
        return None

    def on_accepted(self) -> None:
        """Runs after validate() passes, before the dialog closes."""

    def show_error_message(self, message: str) -> None:
        """Show *message* inline, announce it, and focus the offending control."""
        self._error_label.setText(message)
        self._error_label.setAccessibleDescription(message)
        self._error_label.show()
        announce_accessible(self._error_label, urgent=True)
        target = self.invalid_widget()
        if target is not None and target.isEnabled():
            target.setFocus(Qt.FocusReason.OtherFocusReason)

    def clear_error_message(self) -> None:
        self._error_label.clear()
        self._error_label.hide()

    def _handle_accept(self) -> None:
        error = self.validate()
        if error:
            self.show_error_message(error)
            return
        self.clear_error_message()
        self.on_accepted()
        self.accept()


__all__ = ["BaseDialog"]
