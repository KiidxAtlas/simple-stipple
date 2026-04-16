"""Context-aware properties panel for drafting workflows."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class PropertiesPanel(QWidget):
    """Compact context-aware properties panel.

    The panel focuses on progressive disclosure:
    - always-visible context snapshot
    - current-tool guidance ("next step")
    - only relevant actions for the current selection/mode
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("surface", "panel")
        self._action_buttons: list[QPushButton] = []
        self._empty_actions_label: QLabel | None = None
        self._apply_geometry_callback: (
            Callable[[float | None, float | None, float | None], None] | None
        ) = None

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(10, 8, 10, 8)
        self._layout.setSpacing(6)

        self._title = QLabel("Properties")
        self._title.setProperty("role", "callout-title")
        self._layout.addWidget(self._title)

        self._summary = QLabel("Select an object to view editable properties")
        self._summary.setProperty("role", "callout-body")
        self._summary.setWordWrap(True)
        self._layout.addWidget(self._summary)

        self._next_step = QLabel("Next: Drag on empty canvas to create a shape")
        self._next_step.setStyleSheet("color: #8b949e; font-size: 11px;")
        self._next_step.setWordWrap(True)
        self._layout.addWidget(self._next_step)

        self._details = QLabel("")
        self._details.setStyleSheet("color: #6e7681; font-size: 11px;")
        self._details.setWordWrap(True)
        self._layout.addWidget(self._details)

        self._actions_header = QLabel("Actions")
        self._actions_header.setStyleSheet(
            "color: #8b949e; font-size: 10px; font-weight: 700;"
        )
        self._layout.addWidget(self._actions_header)

        self._actions_host = QWidget(self)
        self._actions_layout = QVBoxLayout(self._actions_host)
        self._actions_layout.setContentsMargins(0, 0, 0, 0)
        self._actions_layout.setSpacing(4)
        self._layout.addWidget(self._actions_host)

        self._editor_header = QLabel("Geometry")
        self._editor_header.setStyleSheet(
            "color: #8b949e; font-size: 10px; font-weight: 700;"
        )
        self._layout.addWidget(self._editor_header)

        self._editor_host = QWidget(self)
        self._editor_layout = QVBoxLayout(self._editor_host)
        self._editor_layout.setContentsMargins(0, 0, 0, 0)
        self._editor_layout.setSpacing(4)

        self._width_edit = QLineEdit(self)
        self._height_edit = QLineEdit(self)
        self._length_edit = QLineEdit(self)
        for edit, placeholder in (
            (self._width_edit, "Width (mm)"),
            (self._height_edit, "Height (mm)"),
            (self._length_edit, "Length (mm)"),
        ):
            edit.setPlaceholderText(placeholder)
            edit.setAlignment(Qt.AlignmentFlag.AlignRight)
            edit.returnPressed.connect(self._apply_geometry_editor)

        wh_row = QHBoxLayout()
        wh_row.setContentsMargins(0, 0, 0, 0)
        wh_row.setSpacing(4)
        wh_row.addWidget(self._width_edit)
        wh_row.addWidget(self._height_edit)
        self._editor_layout.addLayout(wh_row)
        self._editor_layout.addWidget(self._length_edit)

        self._apply_geometry_btn = QPushButton("Apply geometry")
        self._apply_geometry_btn.setMinimumHeight(26)
        self._apply_geometry_btn.clicked.connect(self._apply_geometry_editor)
        self._editor_layout.addWidget(self._apply_geometry_btn)
        self._layout.addWidget(self._editor_host)

        self.set_geometry_editor(None, None, None, None, enabled=False)

        self._layout.addStretch()

    def set_context(
        self,
        *,
        mode: str,
        selected_count: int,
        object_count: int,
        summary: str,
        next_step: str,
        details: str = "",
    ) -> None:
        self._title.setText(f"Properties · {mode.title()}")
        self._summary.setText(summary)
        self._next_step.setText(f"Next: {next_step}")
        self._details.setText(details)
        self._actions_header.setText(
            f"Actions ({selected_count} selected · {object_count} objects)"
        )

    def set_actions(
        self,
        actions: list[tuple[str, str, Callable[[], None], bool]],
    ) -> None:
        """Replace contextual action buttons.

        Each action tuple is: (label, tooltip, callback, enabled)
        """
        for button in self._action_buttons:
            button.setParent(None)
            button.deleteLater()
        self._action_buttons = []
        if self._empty_actions_label is not None:
            self._empty_actions_label.setParent(None)
            self._empty_actions_label.deleteLater()
            self._empty_actions_label = None

        if not actions:
            empty = QLabel("No contextual actions available")
            empty.setStyleSheet("color: #6e7681; font-size: 11px;")
            self._actions_layout.addWidget(empty)
            self._empty_actions_label = empty
            return

        for label, tooltip, callback, enabled in actions:
            button = QPushButton(label)
            button.setMinimumHeight(26)
            button.setToolTip(tooltip)
            button.setEnabled(enabled)
            button.clicked.connect(callback)
            self._actions_layout.addWidget(button)
            self._action_buttons.append(button)

    def set_geometry_editor(
        self,
        width_mm: float | None,
        height_mm: float | None,
        length_mm: float | None,
        on_apply: Callable[[float | None, float | None, float | None], None] | None,
        *,
        enabled: bool,
    ) -> None:
        self._apply_geometry_callback = on_apply
        self._editor_host.setVisible(enabled)
        self._editor_header.setVisible(enabled)

        def _set(edit: QLineEdit, value: float | None) -> None:
            if value is None:
                edit.clear()
            else:
                edit.setText(f"{value:.3f}")

        _set(self._width_edit, width_mm)
        _set(self._height_edit, height_mm)
        _set(self._length_edit, length_mm)

        self._width_edit.setEnabled(enabled and width_mm is not None)
        self._height_edit.setEnabled(enabled and height_mm is not None)
        self._length_edit.setEnabled(enabled and length_mm is not None)
        self._apply_geometry_btn.setEnabled(enabled and on_apply is not None)

    def _apply_geometry_editor(self) -> None:
        if self._apply_geometry_callback is None:
            return

        def _parse(edit: QLineEdit) -> float | None:
            if not edit.isEnabled():
                return None
            text = edit.text().strip()
            if not text:
                return None
            try:
                value = float(text)
            except ValueError:
                return None
            if value <= 0:
                return None
            return value

        self._apply_geometry_callback(
            _parse(self._width_edit),
            _parse(self._height_edit),
            _parse(self._length_edit),
        )


__all__ = ["PropertiesPanel"]
