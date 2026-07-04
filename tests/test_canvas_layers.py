"""Behavior of the per-entity layer model (Draft page's layer system)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from tests.test_canvas_behavior import (  # noqa: E402
    click_world,
    drag_world,
    square,
)


def make_rig(qapp):
    """DxfCanvas + CanvasRuntime with two layers, one square on each."""
    from src.ui.canvas.dxf_canvas import DxfCanvas
    from src.ui.canvas.runtime import CanvasRuntime

    canvas = DxfCanvas()
    canvas.resize(800, 600)
    canvas.set_rulers_visible(False)  # keep edge clicks out of the rulers
    rt = CanvasRuntime(canvas=canvas, default_layer="Layer 1")
    rt.load_polys_by_layer(
        {"Layer 1": [square(0, 0)], "Layer 2": [square(30, 0)]}, fit=True
    )
    return canvas, rt


def test_load_by_layer_sets_model(qapp):
    canvas, rt = make_rig(qapp)
    assert canvas.layer_names() == ["Layer 1", "Layer 2"]
    assert canvas.active_layer == "Layer 1"
    assert [e.layer for e in canvas._entities] == ["Layer 1", "Layer 2"]


def test_inactive_layer_not_selectable(qapp):
    canvas, rt = make_rig(qapp)
    click_world(canvas, 35.0, 0.0)  # square on Layer 2 (inactive)
    assert canvas.get_selection_indices() == []
    canvas.select_all()
    assert canvas.get_selection_indices() == [0]  # only Layer 1's square


def test_click_inactive_shape_activates_its_layer(qapp):
    """DxfCanvas routes clicks on inactive-layer shapes to the ghost-click
    callback with the entity index."""
    from src.ui.canvas.dxf_canvas import DxfCanvas
    from src.ui.canvas.runtime import CanvasRuntime

    clicked = []
    canvas = DxfCanvas(on_ghost_click=lambda idx: clicked.append(idx))
    canvas.resize(800, 600)
    canvas.set_rulers_visible(False)
    rt = CanvasRuntime(canvas=canvas, default_layer="Layer 1")
    rt.load_polys_by_layer(
        {"Layer 1": [square(0, 0)], "Layer 2": [square(30, 0)]}, fit=True
    )
    click_world(canvas, 35.0, 0.0)
    assert clicked == [1]
    # the draft page handler then switches layer + selects:
    canvas.set_active_layer(canvas._entities[1].layer)
    canvas.set_selection([1])
    assert canvas.active_layer == "Layer 2"
    assert canvas.get_selection_indices() == [1]


def test_switch_active_layer_keeps_entities_and_drops_selection(qapp):
    canvas, rt = make_rig(qapp)
    canvas.set_selection([0])
    assert rt.switch_active_layer("Layer 2")
    assert canvas.poly_count == 2  # nothing reloaded or lost
    assert canvas.get_selection_indices() == []  # selection was on Layer 1
    click_world(canvas, 35.0, 0.0)
    assert canvas.get_selection_indices() == [1]


def test_move_selection_between_layers(qapp):
    canvas, rt = make_rig(qapp)
    canvas.set_selection([0])
    assert rt.move_selected_to_layer("Layer 2")
    assert canvas._entities[0].layer == "Layer 2"
    # moved shape left the active layer, so it left the selection too
    assert canvas.get_selection_indices() == []
    assert canvas.undo()
    assert canvas._entities[0].layer == "Layer 1"


def test_new_drawing_lands_on_active_layer(qapp):
    canvas, rt = make_rig(qapp)
    rt.switch_active_layer("Layer 2")
    canvas.set_mode("draw")
    canvas._set_draw_primitive("rectangle")
    click_world(canvas, 100.0, 100.0)
    click_world(canvas, 120.0, 115.0)
    assert canvas.poly_count == 3
    assert canvas._entities[2].layer == "Layer 2"


def test_rename_and_delete_layer(qapp):
    canvas, rt = make_rig(qapp)
    rt.layer_renamed("Layer 2", "Engrave")
    assert canvas.layer_names() == ["Layer 1", "Engrave"]
    assert canvas._entities[1].layer == "Engrave"

    rt.layer_deleted("Engrave")
    assert canvas.layer_names() == ["Layer 1"]
    assert canvas.poly_count == 1
    assert canvas.undo()  # entity deletion is undoable
    assert canvas.poly_count == 2


def test_layer_visibility_entity_native(qapp):
    canvas, rt = make_rig(qapp)
    rt.set_layer_hidden("Layer 1", True)
    assert canvas._entities[0].hidden
    click_world(canvas, 5.0, 0.0)
    assert canvas.get_selection_indices() == []
    rt.solo_layer("Layer 1")
    assert not canvas._entities[0].hidden
    assert canvas._entities[1].hidden
    rt.set_all_hidden(False)
    assert not any(e.hidden for e in canvas._entities)


def test_layer_tree_rows_use_entity_indices(qapp):
    canvas, rt = make_rig(qapp)
    rows = rt.build_layer_tree_rows()
    assert [r["name"] for r in rows] == ["Layer 1", "Layer 2"]
    assert rows[0]["active"] and not rows[1]["active"]
    assert [s["key"] for s in rows[0]["shapes"]] == [0]
    assert [s["key"] for s in rows[1]["shapes"]] == [1]


def test_shape_label_persists_in_meta(qapp):
    canvas, rt = make_rig(qapp)
    rt.rename_shape("Layer 1", 0, "Base plate")
    assert canvas._entities[0].meta["label"] == "Base plate"
    rows = rt.build_layer_tree_rows()
    assert "Base plate" in rows[0]["shapes"][0]["label"]


def test_records_round_trip_with_layers(qapp):
    canvas, rt = make_rig(qapp)
    canvas._entities[1].hidden = True
    records = canvas.get_entity_records()

    from src.ui.canvas.dxf_canvas import DxfCanvas
    from src.ui.canvas.runtime import CanvasRuntime

    c2 = DxfCanvas()
    c2.resize(800, 600)
    CanvasRuntime(canvas=c2, default_layer="Layer 1")
    c2.set_entity_records(records)
    c2.set_layer_model(["Layer 1", "Layer 2"], "Layer 1")
    assert [e.layer for e in c2._entities] == ["Layer 1", "Layer 2"]
    assert c2._entities[1].hidden


def test_legacy_document_graph_migration(qapp):
    """Old workspace snapshots (DocumentGraph + local-index buckets) load
    into the per-entity model."""
    from src.ui.canvas.dxf_canvas import DxfCanvas
    from src.ui.canvas.runtime import CanvasRuntime

    canvas = DxfCanvas()
    canvas.resize(800, 600)
    rt = CanvasRuntime(canvas=canvas, default_layer="Layer 1")
    legacy = {
        "layers": {
            "geometry": {"id": 1, "polylines": [], "entity_refs": [], "dirty": False, "records": None},
            "Layer 1": {
                "id": 2,
                "polylines": [square(0, 0)],
                "entity_refs": [],
                "dirty": False,
                "records": None,
            },
            "Cut": {
                "id": 3,
                "polylines": [square(30, 0), square(60, 0)],
                "entity_refs": [],
                "dirty": False,
                "records": None,
            },
        },
        "layer_order": ["geometry", "Layer 1", "Cut"],
        "active_layer": "Cut",
    }
    rt.restore_graph_state(legacy)
    assert canvas.poly_count == 3
    assert canvas.active_layer == "Cut"
    assert sorted(canvas.layer_names()) == ["Cut", "Layer 1"]
    assert [e.layer for e in canvas._entities] == ["Layer 1", "Cut", "Cut"]


def test_export_records_carry_layer(qapp):
    canvas, rt = make_rig(qapp)
    records = canvas.get_export_dxf_state()
    assert [r["layer"] for r in records] == ["Layer 1", "Layer 2"]


def test_drag_move_ignores_inactive_entities(qapp):
    canvas, rt = make_rig(qapp)
    before = [tuple(p) for p in canvas._entities[1].points]
    drag_world(canvas, 35.0, 0.0, 50.0, 20.0)  # try to drag Layer 2's square
    assert [tuple(p) for p in canvas._entities[1].points] == before


# ── layer tree regressions (post-overhaul report) ────────────────────────────


def test_undo_restores_layer_model(qapp):
    """Undoing past a layer rename/delete must restore the layer list too —
    previously the order desynced, leaving ghost empty layers."""
    canvas, rt = make_rig(qapp)
    rt.layer_renamed("Layer 2", "Engrave")
    assert canvas.layer_names() == ["Layer 1", "Engrave"]
    assert canvas.undo()
    assert canvas.layer_names() == ["Layer 1", "Layer 2"]
    assert canvas._entities[1].layer == "Layer 2"
    assert canvas.redo()
    assert canvas.layer_names() == ["Layer 1", "Engrave"]

    rt.layer_deleted("Engrave")
    assert canvas.layer_names() == ["Layer 1"]
    assert canvas.undo()
    assert canvas.layer_names() == ["Layer 1", "Engrave"]
    assert canvas.poly_count == 2


def test_undo_restores_layer_order(qapp):
    canvas, rt = make_rig(qapp)
    rt.layer_moved("Layer 2", 0)
    assert canvas.layer_names() == ["Layer 2", "Layer 1"]
    assert canvas.undo()
    assert canvas.layer_names() == ["Layer 1", "Layer 2"]


def test_add_layer_is_undoable(qapp):
    canvas, rt = make_rig(qapp)
    rt.add_layer_and_activate("Score")
    assert "Score" in canvas.layer_names()
    assert canvas.undo()
    assert "Score" not in canvas.layer_names()


def test_tree_selection_activates_shape_layer(qapp):
    canvas, rt = make_rig(qapp)
    assert canvas.active_layer == "Layer 1"
    rt.on_tree_selection_requested([1])  # shape on Layer 2
    assert canvas.active_layer == "Layer 2"
    assert canvas.get_selection_indices() == [1]


def test_tree_rows_number_per_layer(qapp):
    canvas, rt = make_rig(qapp)
    canvas.set_active_layer("Layer 2")
    canvas._push_undo()
    canvas._append_entity(square(60, 0))
    rows = rt.build_layer_tree_rows()
    labels = {r["name"]: [s["label"][:2] for s in r["shapes"]] for r in rows}
    assert labels["Layer 1"] == ["01"]
    assert labels["Layer 2"] == ["01", "02"]  # per-layer, not global indices


def test_delete_indices_works_across_layers(qapp):
    canvas, rt = make_rig(qapp)
    assert canvas.delete_indices([1]) == 1  # Layer 2's shape, Layer 1 active
    assert canvas.poly_count == 1
    assert canvas.undo()
    assert canvas.poly_count == 2


# ── per-layer color ──────────────────────────────────────────────────────────


def test_set_and_clear_layer_color(qapp):
    canvas, rt = make_rig(qapp)
    assert canvas.layer_color("Layer 1") is None
    canvas.set_layer_color("Layer 1", "#f0883e")
    assert canvas.layer_color("Layer 1") == "#f0883e"
    canvas.set_layer_color("Layer 1", None)
    assert canvas.layer_color("Layer 1") is None


def test_layer_color_persists_through_view_state(qapp):
    canvas, rt = make_rig(qapp)
    canvas.set_layer_color("Cut", "#3fb950")
    state = canvas.get_view_state()
    from src.ui.canvas.dxf_canvas import DxfCanvas
    from src.ui.canvas.runtime import CanvasRuntime

    c2 = DxfCanvas()
    c2.resize(800, 600)
    CanvasRuntime(canvas=c2, default_layer="Layer 1")
    c2.set_layer_model(["Layer 1", "Cut"], "Layer 1")
    c2.set_view_state(state)
    assert c2.layer_color("Cut") == "#3fb950"


def test_layer_color_carries_across_rename_and_clears_on_delete(qapp):
    canvas, rt = make_rig(qapp)
    canvas.set_layer_color("Cut", "#3fb950")
    rt.layer_renamed("Cut", "Engrave")
    assert canvas.layer_color("Engrave") == "#3fb950"
    assert canvas.layer_color("Cut") is None
    rt.layer_deleted("Engrave")
    assert canvas.layer_color("Engrave") is None


def test_layer_tree_rows_include_color(qapp):
    canvas, rt = make_rig(qapp)
    canvas.set_layer_color("Layer 1", "#f0883e")
    rows = rt.build_layer_tree_rows()
    by_name = {r["name"]: r for r in rows}
    assert by_name["Layer 1"]["color"] == "#f0883e"
    assert by_name["Layer 2"]["color"] is None
