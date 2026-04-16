"""Reusable lightweight form components for sidebar-driven tabs."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.constants import DIM
from src.ui.components.helpers import CollapsibleSection


class TextField(QWidget):
    """Compact labeled text field with required/optional affordance."""

    def __init__(
        self,
        label: str,
        *,
        entry: QLineEdit | None = None,
        default: str = "",
        required: bool = True,
        width: int = 80,
        placeholder: str = "",
        tooltip: str = "",
    ) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        meta = "required" if required else "optional"
        marker = QLabel(f"{label}  ·  {meta}")
        marker.setStyleSheet(f"color: {DIM}; font-size: 10px;")
        self.entry = entry or QLineEdit(default)
        if entry is None:
            self.entry.setText(default)
        self.entry.setFixedWidth(width)
        self.entry.setPlaceholderText(placeholder)
        if tooltip:
            self.entry.setToolTip(tooltip)
        lay.addWidget(marker, stretch=1)
        lay.addWidget(self.entry)


class PathField(QWidget):
    """Path input + browse action for file-based workflows."""

    def __init__(
        self,
        placeholder: str,
        browse_label: str,
        on_browse: Callable[[], None],
        *,
        tooltip: str = "",
    ) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText(placeholder)
        if tooltip:
            self.entry.setToolTip(tooltip)
        browse_btn = QPushButton(browse_label)
        browse_btn.setFixedWidth(64)
        browse_btn.clicked.connect(on_browse)
        lay.addWidget(self.entry, stretch=1)
        lay.addWidget(browse_btn)


def build_lazy_section(
    title: str,
    build_content: Callable[[QVBoxLayout], None],
    *,
    expanded: bool,
) -> CollapsibleSection:
    """Create a collapsible section whose content is instantiated on first expand."""

    content = QWidget()
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(0, 0, 0, 0)
    content_layout.setSpacing(6)
    section = CollapsibleSection(title, content, expanded=expanded)

    built = {"done": False}

    def _ensure_built(checked: bool) -> None:
        if checked and not built["done"]:
            build_content(content_layout)
            built["done"] = True

    section._toggle.toggled.connect(_ensure_built)
    _ensure_built(expanded)
    return section
