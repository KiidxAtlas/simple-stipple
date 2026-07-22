from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QDialog

from src.ui.widgets.dialogs.laserstar_export_dialog import LaserStarExportDialog


def test_laserstar_sheet_collects_all_transaction_fields(qapp, tmp_path):
    dialog = LaserStarExportDialog(
        job_name="sample-job",
        destination=str(tmp_path),
        has_engraving=True,
    )

    assert dialog.machine.currentText() == "LaserStar 3602XL"
    assert "positioned engraving" in dialog.contents.text()
    dialog._accept_if_valid()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.values() == {
        "job_name": "sample-job",
        "destination": str(tmp_path),
        "machine": "laserstar-3602xl",
        "material": "Unspecified — operator verifies",
    }


def test_laserstar_sheet_keeps_invalid_transaction_open(qapp, tmp_path):
    dialog = LaserStarExportDialog(
        job_name="",
        destination=str(tmp_path / "missing-parent" / "output"),
        has_engraving=False,
    )

    dialog._accept_if_valid()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "Enter a job name" in dialog.validation.text()
