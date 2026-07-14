"""DXF import preview exposes layer and replace/append decisions."""

from PySide6.QtCore import Qt

from src.backend.dxf.io import DxfImportReport
from src.ui.widgets.import_dialog import DxfImportPreviewDialog


def test_import_preview_defaults_and_layer_filter(qapp):
    report = DxfImportReport(
        supported_polylines=3,
        flattened_entities={"SPLINE": 1},
        unsupported_entities={},
        invalid_polylines=0,
        layer_counts={"CUT": 2, "GUIDES": 1},
        units="Millimeters",
    )
    dialog = DxfImportPreviewDialog(
        "/tmp/example.dxf",
        {
            "CUT": [[(0.0, 0.0), (10.0, 0.0)]],
            "GUIDES": [[(0.0, 0.0), (0.0, 5.0)]],
        },
        report,
        has_existing_geometry=True,
        default_append=True,
    )

    assert dialog.append_mode()
    assert dialog.selected_layers() == ["CUT", "GUIDES"]
    dialog._layers.item(1).setCheckState(Qt.CheckState.Unchecked)
    assert dialog.selected_layers() == ["CUT"]
