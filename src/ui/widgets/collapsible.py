"""Expandable/collapsible sidebar section widget."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget


class CollapsibleSection(QFrame):
    """Expandable/collapsible content section for dense sidebars.

    Optional ``subtitle`` displays a one-line state summary under the title
    (e.g. "Honeycomb · 1.2 mm") so users can see the active config without
    expanding the section. Update via :meth:`set_subtitle`.
    """

    def __init__(
        self,
        title: str,
        content: QWidget,
        *,
        expanded: bool = True,
        subtitle: str = "",
    ):
        super().__init__()
        self._title = title
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("surface", "panel")
        self.setProperty("role", "collapsible")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(2)

        self._toggle = QToolButton()
        self._toggle.setProperty("role", "collapsible-toggle")
        self._toggle.setText(f"{'▾' if expanded else '▸'}  {title}")
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._toggle.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._toggle.clicked.connect(self._on_toggled)
        layout.addWidget(self._toggle)

        self._subtitle = QLabel(subtitle)
        self._subtitle.setProperty("role", "section-subtitle")
        self._subtitle.setVisible(bool(subtitle))
        layout.addWidget(self._subtitle)

        self._content = content
        self._content.setVisible(expanded)
        layout.addWidget(self._content)

    def _on_toggled(self, checked: bool) -> None:
        self._toggle.setText(f"{'▾' if checked else '▸'}  {self._title}")
        self._content.setVisible(checked)
        self.adjustSize()

    def set_subtitle(self, text: str, *, dim: bool = False) -> None:
        """Update the one-line state summary shown under the title.

        Pass ``dim=True`` to render the subtitle in a more muted color
        (used to indicate the section's feature is currently disabled).
        """
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))
        self._subtitle.setProperty("dim", "true" if dim else "")
        self._subtitle.style().unpolish(self._subtitle)
        self._subtitle.style().polish(self._subtitle)


__all__ = ["CollapsibleSection"]
