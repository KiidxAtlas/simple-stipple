"""DXF import preview and selective-layer decision dialog."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from src.backend.dxf.io import DxfImportReport, summarize_dxf_import_report
from src.ui.components import section_label, surface_frame


class DxfImportPreviewDialog(QDialog):
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
        super().__init__(parent)
        self.setWindowTitle("Import DXF")
        self.setMinimumWidth(460)
        root = QVBoxLayout(self)
        root.setSpacing(10)

        section_label(root, Path(path).name)
        points = [point for polys in by_layer.values() for poly in polys for point in poly]
        if points:
            xs, ys = zip(*points)
            bounds_text = f"{max(xs) - min(xs):.4g} × {max(ys) - min(ys):.4g} drawing units"
        else:
            bounds_text = "No usable bounds"
        summary = QLabel(
            f"Units: {report.units}   ·   Size: {bounds_text}\n"
            f"{report.supported_polylines:,} paths across {len(by_layer):,} layer(s)"
        )
        summary.setWordWrap(True)
        root.addWidget(summary)

        issue_text = summarize_dxf_import_report(report) if report.has_issues else None
        if issue_text:
            notice = QLabel(f"Import notes: {issue_text}")
            notice.setProperty("role", "warning")
            notice.setWordWrap(True)
            root.addWidget(notice)

        layers_frame = surface_frame("panel")
        layers_layout = QVBoxLayout(layers_frame)
        layers_layout.addWidget(QLabel("Layers to import"))
        self._layers = QListWidget()
        for name, polys in by_layer.items():
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
        self._append.setEnabled(has_existing_geometry)
        choose_append = has_existing_geometry or default_append
        self._append.setChecked(choose_append)
        self._replace.setChecked(not choose_append)
        mode_row.addWidget(self._replace)
        mode_row.addWidget(self._append)
        mode_row.addStretch(1)
        root.addLayout(mode_row)
        if has_existing_geometry:
            safety = QLabel(
                "Add preserves the current drawing. Replace removes existing objects; "
                "you can undo the replacement immediately after import."
            )
            safety.setProperty("role", "hint")
            safety.setWordWrap(True)
            root.addWidget(safety)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def selected_layers(self) -> list[str]:
        return [
            str(item.data(Qt.ItemDataRole.UserRole))
            for row in range(self._layers.count())
            if (item := self._layers.item(row)).checkState() == Qt.CheckState.Checked
        ]

    def append_mode(self) -> bool:
        return self._append.isChecked()
