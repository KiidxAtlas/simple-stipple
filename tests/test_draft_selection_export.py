"""Draft vector export has independent modal selection and a selection context action."""

from copy import deepcopy

import ezdxf
import pytest
from PySide6.QtWidgets import QMenu

from simple_stipple.canvas.operations.interactions import handle_right_click
from simple_stipple.core.document.model import EntityRecord
from simple_stipple.core.formats.fvi import read_fvi
from simple_stipple.core.formats.service import DxfService
from simple_stipple.core.formats.svg import svg_to_dxf
from simple_stipple.features.draft import page as draft
from simple_stipple.ui.dialogs.vector_export import VectorExportDialog


@pytest.fixture
def page(qtbot):
    page = draft.DraftPage(settings={})
    qtbot.addWidget(page)
    entities = [
        EntityRecord(points=[(x, 0), (x, 5)], layer=layer, group=group)
        for x, layer, group in [
            (1, "Cut", 4),
            (2, "Cut", 4),
            (3, "Cut", None),
            (10, "Engrave", None),
            (20, "Cut", None),
            (30, "Cut", None),
        ]
    ]
    entities[4].hidden = True
    entities[5].construction = True

    def populate(document):
        document.entities = entities
        document.layer_order = ["Cut", "Engrave"]
        document.active_layer = "Cut"
        document.group_labels = {4: "Word"}

    page._canvas._canvas_service.update_document(populate)
    page._refresh_status()
    return page


def select_rows(dialog, scope):
    tree = dialog._tree
    cut = tree.topLevelItem(0)
    engrave = tree.topLevelItem(1)
    assert cut is not None and engrave is not None
    if scope == "none":
        tree.clearSelection()
        return
    rows = {
        "layer": [cut],
        "group": [cut.child(0)],
        "shape": [cut.child(1)],
        "multi": [cut.child(1), engrave.child(0)],
    }[scope]
    with_signals = tree.blockSignals(True)
    tree.clearSelection()
    for row in rows:
        assert row is not None
        row.setSelected(True)
    tree.blockSignals(with_signals)


@pytest.mark.parametrize("format_name", ["dxf", "svg", "fvi"])
@pytest.mark.parametrize(
    "scope,expected_x",
    [
        ("layer", {1, 2, 3}),
        ("group", {1, 2}),
        ("shape", {3}),
        ("multi", {3, 10}),
        ("none", {1, 2, 3, 10}),
    ],
)
def test_exported_file_contains_only_selected_rows(
    page, monkeypatch, tmp_path, format_name, scope, expected_x
):
    # Ordinary export ignores the existing canvas/sidebar selection.
    page._canvas.set_selection([page._canvas._entities[3].id])

    def choose(dialog):
        assert not dialog.has_selection()
        select_rows(dialog, scope)
        dialog._format.setCurrentIndex(dialog._format.findData(format_name))
        return 1

    monkeypatch.setattr(VectorExportDialog, "exec", choose)
    original = deepcopy(page._canvas.get_entity_records())
    selection = page._canvas.get_selected_ids()
    depth = page._canvas.undo_depth()
    output = tmp_path / f"selected.{format_name}"
    monkeypatch.setattr(draft, "pick_save_file", lambda *args, **kwargs: str(output))
    # Only accept the real options dialog; the FVI writer/reader stay real.
    monkeypatch.setattr(draft.FviExportDialog, "exec", lambda self: 1)
    real_options = draft.FviExportDialog.options

    def options(dialog):
        dialog._origin.setCurrentIndex(dialog._origin.findData("preserve"))
        return real_options(dialog)

    monkeypatch.setattr(draft.FviExportDialog, "options", options)
    errors = []
    monkeypatch.setattr(draft, "show_error", lambda *args: errors.append(args[-1]))
    page._export()
    assert not errors
    assert output.is_file()
    if format_name == "fvi":
        paths = read_fvi(output).paths
    else:
        dxf_path = output
        if format_name == "svg":
            dxf_path = tmp_path / "roundtrip.dxf"
            svg_to_dxf(output, dxf_path)
        paths = DxfService.load_dxf_polylines(str(dxf_path))
        if format_name == "dxf":
            layers = {entity.dxf.layer for entity in ezdxf.readfile(output).modelspace()}
            assert layers == {"Artwork"}
    assert len(paths) == len(expected_x)
    assert {round(point[0], 5) for path in paths for point in path} == expected_x
    assert page._canvas.get_entity_records() == original
    assert page._canvas.get_selected_ids() == selection
    assert page._canvas.undo_depth() == depth


@pytest.mark.parametrize("method", ["_export", "_export_svg", "_export_fvi"])
def test_cancel_export_dialog_does_not_write(page, monkeypatch, method):
    monkeypatch.setattr(VectorExportDialog, "exec", lambda self: 0)

    def unexpected_save(*args, **kwargs):
        pytest.fail("Cancelled selection must not open a save dialog")

    monkeypatch.setattr(draft, "pick_save_file", unexpected_save)
    getattr(page, method)()
    assert page._last_out_path is None


def test_selected_dxf_does_not_include_unselected_dimensions(page, monkeypatch, tmp_path):
    def choose(dialog):
        select_rows(dialog, "shape")
        return 1

    monkeypatch.setattr(VectorExportDialog, "exec", choose)
    page._canvas._dimensions.append(
        {
            "p1": (100, 0),
            "p2": (110, 0),
            "layer": "Engrave",
            "text": "10 mm",
        }
    )
    output = tmp_path / "selected.dxf"
    monkeypatch.setattr(draft, "pick_save_file", lambda *args, **kwargs: str(output))
    page._export()
    entities = list(ezdxf.readfile(output).modelspace())
    assert len(entities) == 1
    assert entities[0].dxftype() != "DIMENSION"


def test_clear_dialog_selection_exports_all(page, qtbot):
    records = page._canvas.get_export_dxf_state()
    dialog = VectorExportDialog(records, selected_ids={records[0]["entity_id"]})
    qtbot.addWidget(dialog)
    assert len(dialog.selected_records()) == 1
    dialog._tree.clearSelection()
    assert not dialog.has_selection()
    assert dialog.selected_records() == records


@pytest.mark.parametrize("overflow", [False, True])
def test_real_context_menu_opens_export_with_selected_shapes(page, monkeypatch, overflow):
    canvas = page._canvas
    selected_id = canvas._entities[2].id
    canvas.set_selection([selected_id])
    canvas.set_context_menu_profiles({})
    if overflow:
        canvas._context_menu_overflow_items.add("context.selection.export")
    captured = []

    def choose(dialog):
        captured.append([r["entity_id"] for r in dialog.selected_records()])
        return 0

    monkeypatch.setattr(VectorExportDialog, "exec", choose)
    assert handle_right_click(canvas, 450, 325)
    menu, action = next(
        (menu, action)
        for menu in canvas.findChildren(QMenu)
        for action in menu.actions()
        if action.text() == "Export selected shapes…"
    )
    assert action.isEnabled()
    assert (menu.title() == "More actions…") == overflow
    menu.hide()
    action.trigger()
    assert captured == [[selected_id]]


@pytest.mark.parametrize("entity_index", [4, 5])
def test_context_export_nonexportable_selection_does_not_export_all(
    page, monkeypatch, entity_index
):
    messages = []
    monkeypatch.setattr(draft.QMessageBox, "information", lambda *args: messages.append(args))

    def unexpected_dialog(self):
        pytest.fail("Nonexportable selection must not become export-all")

    monkeypatch.setattr(VectorExportDialog, "exec", unexpected_dialog)
    page._export_vector(selected_ids={page._canvas._entities[entity_index].id})
    assert len(messages) == 1


@pytest.mark.parametrize("preselect", [False, True])
def test_real_modal_preserves_initial_scope(page, qtbot, preselect):
    from PySide6.QtCore import QTimer

    records = page._canvas.get_export_dxf_state()
    selected_ids = {records[0]["entity_id"]} if preselect else None
    dialog = VectorExportDialog(records, page, selected_ids=selected_ids)
    qtbot.addWidget(dialog)

    def inspect():
        try:
            assert dialog.has_selection() == preselect
            assert len(dialog.selected_records()) == (1 if preselect else len(records))
        finally:
            dialog.reject()

    QTimer.singleShot(50, inspect)
    assert not dialog.exec()


def test_named_multiline_group_and_shape_export_on_one_layer(page, monkeypatch, tmp_path):
    from simple_stipple.core.formats.dxf import load_dxf_polylines_by_layer_with_report

    page._canvas.set_group_label(4, "FIRE\nSAFE")
    output = tmp_path / "artwork.dxf"
    monkeypatch.setattr(draft, "pick_save_file", lambda *args, **kwargs: str(output))
    monkeypatch.setattr(VectorExportDialog, "exec", lambda self: 1)
    page._export()
    document = ezdxf.readfile(output)
    assert not document.audit().has_errors
    assert {entity.dxf.layer for entity in document.modelspace()} == {"Artwork"}
    layers, report = load_dxf_polylines_by_layer_with_report(str(output))
    assert len(layers["Artwork"]) == 4
    assert report.group_labels == {4: "FIRE\nSAFE"}


def test_drawing_and_moving_into_grouped_layer_keeps_shapes_independent(page):
    canvas = page._canvas
    existing_members = {entity.id for entity in canvas._entities if entity.group == 4}
    canvas.set_selection(list(existing_members))
    canvas.set_active_layer("Cut")
    canvas._append_draw_polyline([(50, 0), (50, 5)])
    new_id = canvas._entities[-1].id
    moved_id = canvas._entities[3].id
    assert canvas.move_indices_to_layer([moved_id], "Cut") == 1
    assert canvas._entity_for_id(new_id).group is None
    assert canvas._entity_for_id(moved_id).group is None
    assert {entity.id for entity in canvas._entities if entity.group == 4} == existing_members
    canvas.undo()
    assert canvas._entity_for_id(moved_id).layer == "Engrave"
    canvas.undo()
    assert canvas._entity_for_id(new_id) is None
    canvas.redo()
    canvas.redo()
    assert canvas._entity_for_id(new_id).group is None
    assert canvas._entity_for_id(moved_id).group is None
