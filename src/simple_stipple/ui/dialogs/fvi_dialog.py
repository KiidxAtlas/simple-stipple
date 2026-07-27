"""Configurable StarFX/FiberStar FVI export dialog."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.engine.formats.service import FVI_UNIT_MM, DxfService, FviExportOptions


class FviExportDialog(QDialog):
    """Collect export choices and show the resulting program statistics."""

    def __init__(
        self,
        records: Sequence[dict[str, Any]],
        parent: QWidget | None = None,
        *,
        initial: FviExportOptions | None = None,
    ) -> None:
        super().__init__(parent)
        self._records = records
        initial = initial or FviExportOptions()
        self.setWindowTitle("Export StarFX FVI")
        self.setMinimumWidth(470)

        root = QVBoxLayout(self)
        intro = QLabel(
            "FVI is a relative vector program for StarFX. Geometry is converted to "
            f"fixed {FVI_UNIT_MM:g} mm program units. Review the red trace in StarFX before marking."
        )
        intro.setWordWrap(True)
        intro.setProperty("role", "muted")
        root.addWidget(intro)

        geometry = QGroupBox("Geometry and coordinates")
        form = QFormLayout(geometry)
        self._origin = QComboBox()
        self._origin.addItem("Lower-left at margin", "lower_left")
        self._origin.addItem("Preserve canvas coordinates", "preserve")
        self._origin.addItem("Center on program origin", "center")
        index = self._origin.findData(initial.origin)
        self._origin.setCurrentIndex(max(0, index))
        form.addRow("Program origin", self._origin)

        self._margin = QDoubleSpinBox()
        self._margin.setRange(0.0, 1000.0)
        self._margin.setDecimals(3)
        self._margin.setSuffix(" mm")
        self._margin.setValue(initial.margin_mm)
        form.addRow("Origin margin", self._margin)

        self._precision = QSpinBox()
        self._precision.setRange(0, 9)
        self._precision.setValue(initial.precision)
        self._precision.setSuffix(" decimals")
        form.addRow("Coordinate precision", self._precision)

        self._flip_y = QCheckBox("Flip the vertical axis")
        self._flip_y.setChecked(initial.flip_y)
        self._flip_y.setToolTip(
            "Use only when your StarFX setup displays imported geometry inverted."
        )
        form.addRow("Orientation", self._flip_y)
        root.addWidget(geometry)

        output = QGroupBox("Program generation")
        output_layout = QVBoxLayout(output)
        self._optimize = QCheckBox("Reduce non-marking travel between paths")
        self._optimize.setChecked(initial.optimize_travel)
        self._reverse = QCheckBox("Allow reversing open paths for shorter travel")
        self._reverse.setChecked(initial.reverse_open_paths)
        self._arcs = QCheckBox("Keep native arcs and circles when possible")
        self._arcs.setChecked(initial.preserve_arcs)
        self._comments = QCheckBox("Include readable comments")
        self._comments.setChecked(initial.include_comments)
        for widget in (self._optimize, self._reverse, self._arcs, self._comments):
            output_layout.addWidget(widget)
        root.addWidget(output)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setProperty("role", "status")
        root.addWidget(self._summary)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._origin.currentIndexChanged.connect(self._refresh_summary)
        self._margin.valueChanged.connect(self._refresh_summary)
        self._precision.valueChanged.connect(self._refresh_summary)
        for checkbox in (
            self._flip_y,
            self._optimize,
            self._reverse,
            self._arcs,
            self._comments,
        ):
            checkbox.toggled.connect(self._refresh_summary)
        self._optimize.toggled.connect(self._reverse.setEnabled)
        self._reverse.setEnabled(self._optimize.isChecked())
        self._origin.currentIndexChanged.connect(
            lambda: self._margin.setEnabled(self._origin.currentData() == "lower_left")
        )
        self._margin.setEnabled(self._origin.currentData() == "lower_left")
        self._refresh_summary()

    def options(self) -> FviExportOptions:
        return FviExportOptions(
            origin=str(self._origin.currentData()),  # type: ignore[arg-type]
            margin_mm=self._margin.value(),
            precision=self._precision.value(),
            optimize_travel=self._optimize.isChecked(),
            reverse_open_paths=self._reverse.isChecked(),
            preserve_arcs=self._arcs.isChecked(),
            include_comments=self._comments.isChecked(),
            flip_y=self._flip_y.isChecked(),
        )

    def _refresh_summary(self, *_args) -> None:
        _text, report = DxfService.render_fvi(self._records, self.options())
        if report.bounds_mm is None:
            self._summary.setText("No drawable geometry will be exported.")
            return
        min_x, min_y, max_x, max_y = report.bounds_mm
        summary = (
            f"{report.path_count} paths · {report.draw_line_count} lines · "
            f"{report.draw_arc_count} arcs · {max_x - min_x:.3f} × {max_y - min_y:.3f} mm · "
            f"estimated travel {report.travel_mm:.2f} mm"
        )
        if report.warnings:
            summary += "\n" + " ".join(report.warnings)
        self._summary.setText(summary)


__all__ = ["FviExportDialog"]
