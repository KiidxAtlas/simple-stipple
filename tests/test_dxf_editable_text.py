"""DXF keeps precise outlines and restores text identity in Simple Stipple."""

from copy import deepcopy

import ezdxf
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QFontInfo

from simple_stipple.canvas.dialogs.text_dialog import AddTextDialog
from simple_stipple.canvas.operations import drawing
from simple_stipple.core.document.model import EntityRecord
from simple_stipple.core.formats.dxf import load_dxf_polylines_by_layer_with_report
from simple_stipple.features.draft import page as draft


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("selected_only", [False, True])
def test_dxf_round_trip_restores_editable_text_and_exact_outlines(
    qtbot, monkeypatch, tmp_path, cached, selected_only
):
    source = draft.DraftPage(settings={})
    target = draft.DraftPage(settings={})
    qtbot.addWidget(source)
    qtbot.addWidget(target)

    def approve_export_warning(box):
        for button in box.buttons():
            if box.buttonRole(button) == draft.QMessageBox.ButtonRole.DestructiveRole:
                button.click()
                return 0
        pytest.fail("Unexpected message box during text round trip")

    monkeypatch.setattr(draft.QMessageBox, "exec", approve_export_warning)
    family = QFontInfo(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)).family()
    params = dict(text="FIRE", family=family, height_mm=4.0, bold=True, italic=True)
    canvas = source._canvas
    if cached:
        params["edit_notice"] = "Cached StarFX artwork"
        entities = [
            EntityRecord(
                points=points, group=50, layer="Layer 1", meta={"text_params": deepcopy(params)}
            )
            for points in [
                [(0, 0), (2, 0), (2, 4), (0, 0)],
                [(3, 0), (4, 0), (4, 4), (3, 0)],
            ]
        ]
        canvas._canvas_service.create_entities(entities)
    else:
        canvas.add_text_at(0, 0, **params)
    first_ids = set(canvas.get_entity_ids())
    canvas.add_text_at(25, 0, text="SAFE", family=family, height_mm=5)
    records = canvas.get_export_dxf_state()
    if selected_only:
        records = [record for record in records if record["entity_id"] in first_ids]
    before = deepcopy(canvas.get_entity_records())
    output = tmp_path / "text.dxf"
    source._write_dxf(records, str(output), include_dimensions=False)
    assert not ezdxf.readfile(output).audit().has_errors
    assert canvas.get_entity_records() == before
    monkeypatch.setattr(
        target, "_review_dxf_import", lambda path, layers, report, **k: (layers, False)
    )
    monkeypatch.setattr(target, "_offer_shape_detection", lambda: None)
    target._load_dxf(str(output))
    restored = target._canvas
    imported = restored.get_export_dxf_state()
    assert len(imported) == len(records)
    for expected, actual in zip(records, imported):
        assert [v for point in actual["polyline"] for v in point] == pytest.approx(
            [v for point in expected["polyline"] for v in point]
        )
    fire = [
        entity
        for entity in restored._entities
        if restored.text_params_at(entity.id)["text"] == "FIRE"
    ]
    assert fire and all(restored.text_params_at(entity.id) == params for entity in fire)
    other_ids = {entity.id for entity in restored._entities if entity not in fire}
    assert bool(other_ids) != selected_only
    original = deepcopy(restored.get_entity_records())
    opened = []

    def edit(dialog):
        opened.append(dialog.values())
        dialog._text_edit.setPlainText("EDITED")
        return AddTextDialog.DialogCode.Accepted

    monkeypatch.setattr(drawing, "confirm", lambda *a: True)
    monkeypatch.setattr(AddTextDialog, "exec", edit)
    restored.set_selection([fire[0].id])
    qtbot.keyClick(restored, Qt.Key.Key_T)
    assert opened[0]["text"] == "FIRE"
    assert opened[0]["family"] == family
    assert {entity.id for entity in restored._entities if entity.id in other_ids} == other_ids
    assert {
        restored.text_params_at(entity.id)["text"]
        for entity in restored._entities
        if entity.id not in other_ids
    } == {"EDITED"}
    restored.undo()
    assert restored.get_entity_records() == original
    restored.redo()
    assert {
        restored.text_params_at(entity.id)["text"]
        for entity in restored._entities
        if entity.id not in other_ids
    } == {"EDITED"}


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        '{"text":"x"}',
        '{"text":"x","family":"Tahoma","height_mm":-1}',
        '{"text":"x","family":"Tahoma","height_mm":NaN}',
    ],
)
def test_invalid_text_metadata_keeps_outlines_without_enabling_editing(tmp_path, payload):
    output = tmp_path / "invalid.dxf"
    document = ezdxf.new("R2010")
    document.appids.add("SSTPT")
    entity = document.modelspace().add_lwpolyline([(0, 0), (1, 1)])
    entity.set_xdata("SSTPT", [(1000, payload)])
    document.saveas(output)
    layers, report = load_dxf_polylines_by_layer_with_report(str(output))
    assert len(layers["0"]) == 1
    assert not report.texts


@pytest.mark.parametrize("extra_layer", [False, True])
def test_single_contour_unicode_text_metadata_round_trip(qtbot, monkeypatch, tmp_path, extra_layer):
    from simple_stipple.core.formats.dxf import write_polylines_dxf

    params = {
        "text": "火線\nSAFE " * 80,
        "family": "Tahoma",
        "height_mm": 4.0,
        "bold": False,
        "italic": False,
        "edit_notice": "Keep cached contours",
    }
    points = [(0.0, 0.0), (2.0, 0.0), (2.0, 4.0), (0.0, 0.0)]
    output = tmp_path / "single.dxf"
    if extra_layer:
        write_polylines_dxf(
            [],
            str(output),
            extra_layer_records={"Text": [{"polyline": points, "meta": {"text_params": params}}]},
        )
    else:
        write_polylines_dxf([points], str(output), entity_meta=[{"text_params": params}])
    assert not ezdxf.readfile(output).audit().has_errors
    page = draft.DraftPage(settings={})
    qtbot.addWidget(page)
    monkeypatch.setattr(
        page, "_review_dxf_import", lambda path, layers, report, **k: (layers, False)
    )
    monkeypatch.setattr(page, "_offer_shape_detection", lambda: None)
    page._load_dxf(str(output))
    entities = page._canvas._entities
    assert len(entities) == 1
    assert entities[0].group is None
    assert page._canvas.text_params_at(entities[0].id) == params
