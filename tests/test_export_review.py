from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from simple_stipple.core.cad.preflight import analyze_geometry
from simple_stipple.core.cad.production import MachineProfile, estimate_job
from simple_stipple.ui.dialogs.export_review import ExportReviewDialog


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_export_review_shows_machine_bounds_and_operations(app: QApplication) -> None:
    square = [(0.0, 0.0), (20.0, 0.0), (20.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    dialog = ExportReviewDialog(
        action="Export DXF",
        report=analyze_geometry([square]),
        estimate=estimate_job([square], feed_rate_mm_s=10),
        profile=MachineProfile("Bench", 100, 100, 10),
        operations=("Cut · Layer 1",),
        allow_open_paths=True,
    )

    labels = "\n".join(label.text() for label in dialog.findChildren(QLabel))
    assert dialog.windowTitle() == "Export DXF Review"
    assert "Fits Bench" in labels
    assert "20.000 × 10.000 mm" in labels
    assert dialog._export_button.text() == "Export DXF"
    dialog.close()
