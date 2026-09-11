"""A visible, machine-independent review step before vector export."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QListWidget,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.core.cad.preflight import GeometryPreflight
from simple_stipple.core.cad.production import JobEstimate, MachineProfile, fits_machine_bed
from simple_stipple.ui.components.focus import install_dialog_focus_lifecycle
from simple_stipple.ui.components.layout import surface_frame
from simple_stipple.ui.style import SPACE_LG, SPACE_MD


def _length(value: float | None, unit: str) -> str:
    return "—" if value is None else f"{value:.3f} {unit}"


def _fact(form: QFormLayout, label: str, value: str) -> None:
    """Add a typed form row; PySide's stubs do not accept bare strings."""
    value_label = QLabel(value)
    value_label.setWordWrap(True)
    form.addRow(QLabel(label), value_label)


class ExportReviewDialog(QDialog):
    """Show geometry, bounds, operations, and estimates before writing a file.

    It deliberately does not offer repair actions.  The dialog says what is
    about to be written; the caller remains responsible for returning focus to
    editable geometry when the operator declines the export.
    """

    def __init__(
        self,
        *,
        action: str,
        report: GeometryPreflight,
        estimate: JobEstimate,
        unit: str = "mm",
        profile: MachineProfile | None = None,
        operations: Sequence[str] = (),
        allow_open_paths: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{action} Review")
        self.setModal(True)
        self.resize(620, 560)
        profile = profile or MachineProfile()

        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        root.setSpacing(SPACE_MD)
        title = QLabel(f"Review before {action.lower()}")
        title.setProperty("role", "page-title")
        root.addWidget(title)
        subtitle = QLabel(
            "Confirm the geometry and machine assumptions here, then inspect the exported file in your machine software before running a job."
        )
        subtitle.setProperty("role", "page-subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        findings = self._findings(report, allow_open_paths)
        health = QLabel(findings)
        health.setWordWrap(True)
        health.setProperty("role", "status-ok" if report.ready else "status-warn")
        root.addWidget(health)

        facts = surface_frame("panel")
        form = QFormLayout(facts)
        _fact(form, "Paths / segments", f"{estimate.paths} / {estimate.segments}")
        _fact(form, "Closed / open", f"{report.closed} / {report.open}")
        _fact(form, "Cut length", _length(estimate.cut_length_mm, unit))
        _fact(form, "Travel in current order", _length(estimate.travel_length_mm, unit))
        if estimate.bounds_mm is None:
            bounds_text = "No drawable bounds"
        else:
            min_x, min_y, max_x, max_y = estimate.bounds_mm
            bounds_text = (
                f"X {min_x:.3f}–{max_x:.3f}; Y {min_y:.3f}–{max_y:.3f} {unit} "
                f"({estimate.width_mm:.3f} × {estimate.height_mm:.3f} {unit})"
            )
        _fact(form, "Bounds", bounds_text)
        if estimate.estimated_cut_seconds is None:
            time_text = "Add a feed rate to a machine profile to estimate"
        else:
            time_text = f"{estimate.estimated_cut_seconds:.1f} s at {profile.feed_rate_mm_s:g} mm/s"
        _fact(form, "Estimated cut time", time_text)
        bed_state = fits_machine_bed(estimate.bounds_mm, profile)
        if bed_state is None:
            bed_text = "No bed configured"
        elif bed_state:
            bed_text = (
                f"Fits {profile.name} ({profile.bed_width_mm:g} × {profile.bed_height_mm:g} mm)"
            )
        else:
            bed_text = f"Outside {profile.name} bed ({profile.bed_width_mm:g} × {profile.bed_height_mm:g} mm)"
        _fact(form, "Machine bed", bed_text)
        root.addWidget(facts)

        operation_title = QLabel("Operations / layers")
        operation_title.setProperty("role", "callout-title")
        root.addWidget(operation_title)
        operation_list = QListWidget()
        operation_list.setAccessibleName("Operations and layers to export")
        operation_list.setMaximumHeight(110)
        for operation in operations or ("Geometry in current document order",):
            operation_list.addItem(operation)
        root.addWidget(operation_list)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._export_button = buttons.addButton(f"{action}", QDialogButtonBox.ButtonRole.AcceptRole)
        self._export_button.setProperty("role", "primary")
        buttons.rejected.connect(self.reject)
        self._export_button.clicked.connect(self.accept)
        root.addWidget(buttons)
        install_dialog_focus_lifecycle(self, self._export_button)

    @staticmethod
    def _findings(report: GeometryPreflight, allow_open_paths: bool) -> str:
        findings: list[str] = []
        if report.open and not allow_open_paths:
            findings.append(f"{report.open} open path(s) need attention")
        if report.invalid:
            findings.append(f"{report.invalid} invalid path(s)")
        if report.duplicates:
            findings.append(f"{report.duplicates} duplicate path(s)")
        if report.zero_segments:
            findings.append(f"{report.zero_segments} zero-length segment(s)")
        if report.tiny_paths:
            findings.append(f"{report.tiny_paths} tiny path(s)")
        if report.near_closed:
            findings.append(f"{report.near_closed} nearly closed path(s)")
        if report.self_intersections:
            findings.append(f"{report.self_intersections} self-intersecting path(s)")
        return "Geometry ready for review." if not findings else " · ".join(findings)


__all__ = ["ExportReviewDialog"]
