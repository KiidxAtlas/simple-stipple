"""DXF import preview and selective-layer decision dialog."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.engine.formats.service import DxfImportReport, summarize_dxf_import_report
from simple_stipple.ui.components.focus import install_dialog_focus_lifecycle
from simple_stipple.ui.components.layout import (
    section_label,
    surface_frame,
)
from simple_stipple.ui.components.tokens import SPACE_MD
from simple_stipple.ui.dialogs.base import BaseDialog


class DxfImportPreviewDialog(BaseDialog):
    """Preview import scale/content and choose layers plus replace/append."""

    def __init__(
        self,
        path: str,
        by_layer: dict[str, list[list[tuple[float, float]]]],
        report: DxfImportReport,
        *,
        has_existing_geometry: bool,
        default_append: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        self._path = path
        self._by_layer = by_layer
        self._report = report
        self._has_existing_geometry = has_existing_geometry
        self._default_append = default_append
        super().__init__(parent, title="Import DXF")
        self.setMinimumSize(640, 520)
        self.resize(720, 600)

    def create_content(self, layout: QVBoxLayout) -> None:
        root = layout
        root.setSpacing(SPACE_MD)

        section_label(root, Path(self._path).name)
        points = [point for polys in self._by_layer.values() for poly in polys for point in poly]
        if points:
            xs, ys = zip(*points)
            bounds_text = f"{max(xs) - min(xs):.4g} × {max(ys) - min(ys):.4g} drawing units"
        else:
            bounds_text = "No usable bounds"
        summary = QLabel(
            f"Units: {self._report.units}   ·   Size: {bounds_text}\n"
            f"{self._report.supported_polylines:,} paths across {len(self._by_layer):,} layer(s)"
        )
        summary.setWordWrap(True)
        root.addWidget(summary)

        issue_text = summarize_dxf_import_report(self._report) if self._report.has_issues else None
        if issue_text:
            notice = QLabel(f"Import notes: {issue_text}")
            notice.setProperty("role", "status-warn")
            notice.setWordWrap(True)
            root.addWidget(notice)

        layers_frame = surface_frame("panel")
        layers_layout = QVBoxLayout(layers_frame)
        layers_layout.addWidget(QLabel("Layers to import"))
        self._layers = QListWidget()
        for name, polys in self._by_layer.items():
            item = QListWidgetItem(f"{name}  ({len(polys):,} paths)")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self._layers.addItem(item)
        layers_layout.addWidget(self._layers)
        root.addWidget(layers_frame)

        mode_row = QHBoxLayout()
        self._replace = QRadioButton("Replace drawing")
        self._append = QRadioButton("Add to drawing")
        self._append.setEnabled(self._has_existing_geometry)
        # "Add" only makes sense when there's an existing drawing to add to —
        # otherwise there is nothing to append and the radio is disabled, so
        # it must not also be checked.
        choose_append = self._has_existing_geometry and self._default_append
        self._append.setChecked(choose_append)
        self._replace.setChecked(not choose_append)
        mode_row.addWidget(self._replace)
        mode_row.addWidget(self._append)
        mode_row.addStretch(1)
        root.addLayout(mode_row)
        if self._has_existing_geometry:
            safety = QLabel(
                "Add preserves the current drawing. Replace removes existing objects; "
                "you can undo the replacement immediately after import."
            )
            safety.setProperty("role", "hint")
            safety.setWordWrap(True)
            root.addWidget(safety)

        install_dialog_focus_lifecycle(self, self._layers)

    def validate(self) -> str | None:
        if not self.selected_layers():
            return "Select at least one layer to import."
        return None

    def selected_layers(self) -> list[str]:
        return [
            str(item.data(Qt.ItemDataRole.UserRole))
            for row in range(self._layers.count())
            if (item := self._layers.item(row)).checkState() == Qt.CheckState.Checked
        ]

    def append_mode(self) -> bool:
        return self._append.isChecked()


__all__ = ["DxfImportPreviewDialog"]
