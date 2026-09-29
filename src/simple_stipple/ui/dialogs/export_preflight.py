"""Consistent geometry-readiness gate shared by vector export workflows."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence

from PySide6.QtWidgets import QMessageBox, QWidget

from simple_stipple.core.cad.preflight import GeometryPreflight, analyze_geometry
from simple_stipple.core.cad.production import MachineProfile, estimate_job
from simple_stipple.ui.dialogs.export_review import ExportReviewDialog

# Open endpoints are informational markers, not problems by themselves; they
# only count as issues when the export requires closed regions.
_OPEN_ENDPOINT_KINDS = frozenset({"open_start", "open_end"})

# (action, allow_open_paths, geometry fingerprint) the user already chose to
# export despite warnings in this app session. Any geometry edit changes the
# fingerprint, so the warning returns as soon as the drawing differs.
_ACKNOWLEDGED: set[tuple[str, bool, str]] = set()


def geometry_fingerprint(polylines: list[list[tuple[float, float]]]) -> str:
    """Stable digest of the exported coordinates, rounded to hide float noise."""
    digest = hashlib.sha256()
    for poly in polylines:
        for x, y in poly:
            digest.update(f"{round(float(x), 6)!r},{round(float(y), 6)!r};".encode())
        digest.update(b"|")
    return digest.hexdigest()


def _issue_path_indices(report: GeometryPreflight, *, open_blocks: bool) -> list[int]:
    return sorted(
        {
            issue.path_index
            for issue in report.issues
            if open_blocks or issue.kind not in _OPEN_ENDPOINT_KINDS
        }
    )


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
    on_show_issues: Callable[[list[int]], None] | None = None,
) -> tuple[bool, GeometryPreflight]:
    """Review production facts, then ask before continuing with known risks.

    ``on_show_issues`` receives the indices (into ``polylines``) of paths
    that need attention when the user picks "Show Issues"; the export is then
    cancelled so the page can select and frame them.
    """
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
    acknowledgment = (action, allow_open_paths, geometry_fingerprint(polylines))
    if acknowledgment in _ACKNOWLEDGED:
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
    issue_indices = _issue_path_indices(report, open_blocks=bool(open_blockers))
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(f"{action} Preflight")
    box.setText("Geometry needs attention before production.")
    box.setInformativeText(
        "\n".join(f"• {detail}" for detail in details)
        + "\n\nContinue only if these conditions are intentional. Choosing "
        f"“{action} Anyway” skips this warning for the same geometry until it changes."
    )
    continue_button = box.addButton(f"{action} Anyway", QMessageBox.ButtonRole.DestructiveRole)
    show_button = (
        box.addButton("Show Issues", QMessageBox.ButtonRole.ActionRole)
        if on_show_issues is not None and issue_indices
        else None
    )
    return_button = box.addButton("Return to Drawing", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(show_button if show_button is not None else return_button)
    box.exec()
    clicked = box.clickedButton()
    if on_show_issues is not None and show_button is not None and clicked is show_button:
        on_show_issues(issue_indices)
        return False, report
    if clicked is continue_button:
        _ACKNOWLEDGED.add(acknowledgment)
        return True, report
    return False, report
