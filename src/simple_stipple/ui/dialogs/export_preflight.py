"""Consistent geometry-readiness gate shared by vector export workflows."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtWidgets import QMessageBox, QWidget

from simple_stipple.core.cad.preflight import GeometryPreflight, analyze_geometry
from simple_stipple.core.cad.production import MachineProfile, estimate_job
from simple_stipple.ui.dialogs.export_review import ExportReviewDialog


def export_preflight(
    parent: QWidget,
    polylines: list[list[tuple[float, float]]],
    *,
    action: str,
    allow_open_paths: bool,
    unit: str = "mm",
    profile: MachineProfile | None = None,
    operations: Sequence[str] = (),
    show_review: bool = False,
) -> tuple[bool, GeometryPreflight]:
    """Review production facts, then ask before continuing with known risks."""
    report = analyze_geometry(polylines)
    estimate = estimate_job(
        polylines, feed_rate_mm_s=profile.feed_rate_mm_s if profile is not None else None
    )
    if show_review:
        review = ExportReviewDialog(
            action=action,
            report=report,
            estimate=estimate,
            unit=unit,
            profile=profile,
            operations=operations,
            allow_open_paths=allow_open_paths,
            parent=parent,
        )
        if review.exec() != review.DialogCode.Accepted:
            return False, report
    open_blockers = report.open if not allow_open_paths else 0
    if report.ready and not open_blockers:
        return True, report

    counts = (
        (open_blockers, "open path(s) will not form closed regions"),
        (report.invalid, "invalid path(s)"),
        (report.duplicates, "duplicate path(s)"),
        (report.zero_segments, "zero-length segment(s)"),
        (report.tiny_paths, "path(s) below the geometry tolerance"),
        (report.near_closed, "nearly closed path(s)"),
        (report.self_intersections, "self-intersecting path(s)"),
    )
    details = [f"{count} {label}" for count, label in counts if count]
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(f"{action} Preflight")
    box.setText("Geometry needs attention before production.")
    box.setInformativeText(
        "\n".join(f"• {detail}" for detail in details)
        + "\n\nReview the highlighted geometry when possible. "
        "Continue only if these conditions are intentional."
    )
    continue_button = box.addButton(f"{action} Anyway", QMessageBox.ButtonRole.DestructiveRole)
    return_button = box.addButton("Return to Drawing", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(return_button)
    box.exec()
    return box.clickedButton() is continue_button, report
