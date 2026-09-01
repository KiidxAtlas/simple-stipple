"""Regression tests for the 2026-09-01 UI follow-up batch.

Covers: empty-state overlay hiding mid-draw, drawer toggle tracking handle
drags, recovery-snapshot deletion sticking, the draw HUD appearing only on
request, and badge-anchored dimension editors.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from simple_stipple.app.tasks import AutoCommitController, AutosaveController
from simple_stipple.app.window import App
from simple_stipple.canvas.widget import DxfCanvas
from simple_stipple.core.cad.detection import _closed_vertices, detect_primitive
from simple_stipple.core.editing.corners import chamfered_corner_points, rounded_corner_points
from simple_stipple.features.draft import DraftPage
from simple_stipple.ui.components.layout import ResponsiveContentSplitter


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_empty_overlay_hides_for_every_draw_channel(app: QApplication) -> None:
    """'Start a drawing' must vanish mid-gesture, not only after commit."""
    draft = DraftPage(settings={})
    canvas = draft._canvas
    canvas.resize(600, 400)
    assert not canvas._has_geometry_or_gesture()
    assert canvas._empty_actions_bar is not None
    assert canvas._empty_actions_bar.isVisibleTo(canvas)

    channels = (
        "_draw_pts",
        "_draw_arc_pts",
        "_pen_pts",
    )
    for channel in channels:
        getattr(canvas, channel).append((5.0, 5.0))
        assert canvas._has_geometry_or_gesture(), channel
        canvas.sync_empty_actions()
        assert not canvas._empty_actions_bar.isVisibleTo(canvas), channel
        getattr(canvas, channel).clear()

    canvas._draw_shape_preview_active = True
    canvas._draw_shape_anchor_w = (0.0, 0.0)
    assert canvas._has_geometry_or_gesture(), "shape preview"
    canvas.sync_empty_actions()
    assert not canvas._empty_actions_bar.isVisibleTo(canvas)
    canvas._draw_shape_preview_active = False
    canvas._draw_shape_anchor_w = None

    canvas._shape_drag_active = True
    assert canvas._has_geometry_or_gesture(), "quick-shape drag"
    canvas._shape_drag_active = False
    assert not canvas._has_geometry_or_gesture()
    draft.close()


def test_drawer_toggle_tracks_handle_drags(app: QApplication) -> None:
    """Dragging the drawer shut must flip the toggle to 'Show …' and keep it
    on the visible canvas edge instead of clipped beneath the panel."""
    splitter = ResponsiveContentSplitter()
    splitter.addWidget(QWidget())
    splitter.addWidget(QWidget())
    splitter.resize(1000, 600)  # below COMPACT_WIDTH → compact drawer mode
    splitter.set_responsive_secondary(1, "Layers")
    splitter.show()
    app.processEvents()

    toggle = splitter._drawer_toggle
    assert toggle.isVisible()
    # Compact mode starts with the drawer closed.
    assert toggle.text() == "Show Layers"
    toggle.click()
    app.processEvents()
    assert toggle.text() == "Hide Layers"

    # Simulate a handle drag to fully collapsed (splitterMoved is emitted by
    # real drags only, never by setSizes).
    splitter.setSizes([1000, 0])
    splitter.splitterMoved.emit(0, 1)
    app.processEvents()
    assert toggle.text() == "Show Layers"
    assert toggle.accessibleDescription() == "Show Layers"
    assert toggle.x() + toggle.width() <= splitter.widget(0).width()

    splitter.setSizes([700, 300])
    splitter.splitterMoved.emit(700, 1)
    app.processEvents()
    assert toggle.text() == "Hide Layers"
    splitter.close()


def test_deleted_recovery_snapshot_is_not_rewritten(
    app: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Deleting snapshots in the library must not be undone by the 90 s timer."""
    monkeypatch.setattr(App, "_confirm_discard_if_dirty", lambda _s, **_kw: True)
    monkeypatch.setattr(AutosaveController, "offer_startup_autosave_recovery", lambda _s: None)
    monkeypatch.setattr("simple_stipple.platform.settings.save_settings", lambda _s: None)
    monkeypatch.setattr("simple_stipple.app.window.user_data_dir", lambda: tmp_path)

    window = App()
    ctrl = window._autosave_controller
    # Stop the timers directly; TaskController.shutdown() would also set the
    # shutting-down flags that make _autosave_workspace a no-op.
    ctrl._recovery_timer.stop()
    ctrl._regular_timer.stop()
    monkeypatch.setattr(window, "_has_workspace_content", lambda: True)
    window._workspace_dirty = True

    ctrl.dismiss_recovery_for_current_state()
    ctrl._autosave_workspace()
    assert not window._autosave_path().exists(), "deleted state resurrected"

    # A real edit re-arms crash protection.
    monkeypatch.setattr(window, "_collect_workspace_document", lambda: {"changed": True})
    ctrl._autosave_workspace()
    if ctrl._recovery_write_thread is not None:
        ctrl._recovery_write_thread.join(timeout=5)
    assert window._autosave_path().exists(), "new edits must still be protected"
    window.close()


def test_draw_hud_only_on_request_and_anchored_to_badge(app: QApplication) -> None:
    """No boxed Length/Angle while mouse-drawing; Tab summons them onto the
    painted badge spot (rubber-band segment midpoint)."""
    canvas = DxfCanvas()
    canvas.resize(600, 400)
    canvas.set_mode("draw")
    canvas._draw_primitive = "polyline"
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(100, 100))
    app.processEvents()
    assert canvas._draw_pts, "click did not start a polyline"
    assert canvas._dim_distance_edit is None, "boxed HUD must not auto-appear"

    QTest.keyClick(canvas, Qt.Key.Key_Tab)
    app.processEvents()
    edit = canvas._dim_distance_edit
    assert edit is not None, "Tab must summon the editor"

    canvas._update_dim_positions(300, 200)
    last_c = canvas._w2c(*canvas._draw_pts[-1])
    mx, my = (last_c[0] + 300) / 2, (last_c[1] + 200) / 2
    exp_x = max(8, min(int(mx - 46), 600 - 100))
    exp_y = max(canvas._chrome_top() + 8, min(int(my - 84), 400 - 92))
    assert (edit.x(), edit.y()) == (exp_x, exp_y + 16)  # label row sits above the field
    canvas.close()


def test_shape_dim_fields_sit_on_badge_anchors_and_track(app: QApplication) -> None:
    """Shape W/H editors replace the amber badges: W below the bbox, H right
    of it, tracking the preview as it grows."""
    canvas = DxfCanvas()
    canvas.resize(600, 400)
    canvas.set_mode("draw")
    canvas._draw_primitive = "rectangle"
    canvas._draw_shape_preview_active = True
    canvas._draw_shape_anchor_w = (0.0, 0.0)
    canvas._draw_shape_cursor_w = (30.0, 20.0)
    canvas._show_shape_dim_inputs()
    w_edit = canvas._draw_shape_w_edit
    h_edit = canvas._draw_shape_h_edit
    assert w_edit is not None and h_edit is not None

    bx0, by0 = canvas._w2c(0.0, 20.0)
    bx1, by1 = canvas._w2c(30.0, 0.0)
    bottom, right = max(by0, by1), max(bx0, bx1)
    mid_btm_x, mid_rgt_y = (bx0 + bx1) / 2, (by0 + by1) / 2
    top = canvas._chrome_top() + 8
    exp_wx = max(8, min(int(mid_btm_x - w_edit.width() / 2), 600 - w_edit.width() - 8))
    exp_wy = max(top, min(int(bottom + 6), 400 - w_edit.height() - 8))
    exp_hx = max(8, min(int(right + 6), 600 - h_edit.width() - 8))
    exp_hy = max(top, min(int(mid_rgt_y - h_edit.height() / 2), 400 - h_edit.height() - 8))
    assert (w_edit.x(), w_edit.y()) == (exp_wx, exp_wy)
    assert (h_edit.x(), h_edit.y()) == (exp_hx, exp_hy)

    # Growing the preview moves the anchors; the fields must follow.
    canvas._draw_shape_cursor_w = (45.0, 30.0)
    canvas._update_shape_size_fields_from_preview()
    nbx0, nby0 = canvas._w2c(0.0, 30.0)
    nbx1, nby1 = canvas._w2c(45.0, 0.0)
    new_bottom = max(nby0, nby1)
    new_right = max(nbx0, nbx1)
    exp_wy2 = max(top, min(int(new_bottom + 6), 400 - w_edit.height() - 8))
    exp_hx2 = max(8, min(int(new_right + 6), 600 - h_edit.width() - 8))
    assert w_edit.y() == exp_wy2
    assert h_edit.x() == exp_hx2
    canvas.close()


def test_select_mode_has_no_vertex_drag_but_gizmo_still_transforms(
    app: QApplication,
) -> None:
    canvas = DxfCanvas()
    canvas.resize(600, 400)
    canvas.add_polylines_state([[(0.0, 0.0), (40.0, 0.0), (40.0, 20.0), (0.0, 20.0), (0.0, 0.0)]])
    canvas.set_selection({canvas._entities[0].id})
    canvas.grab()  # paint once to populate gizmo rects
    app.processEvents()

    # Pressing exactly on a vertex must NOT start a point drag in select mode.
    vx, vy = canvas._w2c(0.0, 0.0)
    QTest.mousePress(
        canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(vx), int(vy))
    )
    assert not canvas._edit_dragging
    QTest.mouseRelease(
        canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(vx), int(vy))
    )

    # The gizmo still transforms: dragging the "s" handle grows the shape.
    # ("s" avoids the badge/handle overlap on the right edge; QTest's move
    # doesn't carry buttons, so the drag move is a manual event.)
    from PySide6.QtGui import QMouseEvent

    rects = {name: rect for name, rect in canvas._gizmo_handle_rects}
    assert "s" in rects
    center = rects["s"].center().toPoint()
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
    assert canvas._gizmo_drag_mode == "scale-s"
    target = center + QPoint(0, 30)
    move = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        target.toPointF(),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(canvas, move)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target)
    app.processEvents()
    entity = canvas._entities[0]
    ys = [p[1] for p in entity.points]
    assert max(ys) - min(ys) > 20.0, "gizmo scale drag did not resize"
    canvas.close()


def test_rotated_ellipse_gizmo_follows_shape_axes(app: QApplication) -> None:
    import math

    from simple_stipple.core.document.model import EntityRecord

    canvas = DxfCanvas()
    canvas.resize(800, 600)
    canvas.set_mode("select")
    canvas._canvas_service.create_entities(
        [
            EntityRecord(
                points=[(20.0, 50.0), (80.0, 50.0)],
                kind="ellipse",
                meta={"center": (50.0, 50.0), "rx": 30.0, "ry": 15.0, "rotation": 35.0},
            )
        ]
    )
    canvas.set_selection([canvas._entities[-1].id])
    canvas.grab()
    app.processEvents()
    rects = {name: rect for name, rect in canvas._gizmo_handle_rects}
    assert "e" in rects
    painted = rects["e"].center()
    angle = math.radians(35.0)
    exp = canvas._w2c(50.0 + 30.0 * math.cos(angle), 50.0 + 30.0 * math.sin(angle))
    assert abs(painted.x() - exp[0]) < 2 and abs(painted.y() - exp[1]) < 2
    canvas.close()


def test_distribute_evenly_over_total_distance(app: QApplication) -> None:
    canvas = DxfCanvas()
    canvas.add_polylines_state(
        [
            [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)],
            [(5.0, 20.0), (15.0, 20.0), (15.0, 30.0), (5.0, 30.0), (5.0, 20.0)],
            [(30.0, 40.0), (40.0, 40.0), (40.0, 50.0), (30.0, 50.0), (30.0, 40.0)],
        ]
    )
    canvas.set_selection([e.id for e in canvas._entities])
    assert canvas._distribute_selected("horizontal", 50.0, mode="even")
    edges = sorted(min(p[0] for p in e.points) for e in canvas._entities)
    # Total span 50, three 10-wide shapes → equal 10 mm gaps: 0, 20, 40.
    assert edges == pytest.approx([0.0, 20.0, 40.0])
    canvas.close()


def test_align_to_axis_flattens_dominant_edge(app: QApplication) -> None:
    import math

    canvas = DxfCanvas()
    canvas.add_polylines_state(
        [[(0.0, 0.0), (10.0 * math.cos(math.radians(30)), 10.0 * math.sin(math.radians(30)))]]
    )
    canvas.set_selection([canvas._entities[0].id])
    assert canvas.align_selected_to_axis("horizontal")
    (_ax, ay), (_bx, by) = canvas._entities[0].points
    assert ay == pytest.approx(by, abs=1e-6)
    canvas.close()


def test_corner_kernels_round_and_chamfer() -> None:
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    rounded = rounded_corner_points(square, 1, 2.0, closed=True)
    assert rounded is not None
    assert len(rounded) > len(square)  # arc inserts multiple points
    assert rounded[0] == rounded[-1]  # stays closed

    chamfered = chamfered_corner_points(square, 1, 1.0, closed=True)
    assert chamfered is not None
    assert len(chamfered) == len(square) + 1
    assert (9.0, 0.0) in chamfered and (10.0, 1.0) in chamfered

    open_path = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    assert rounded_corner_points(open_path, 0, 1.0, closed=False) is None  # endpoint
    assert chamfered_corner_points(open_path, 1, 1.0, closed=False) is not None


def test_round_corner_arms_picker_in_select_mode(app: QApplication) -> None:
    from simple_stipple.canvas import commands as canvas_commands

    canvas = DxfCanvas()
    canvas.resize(600, 400)
    canvas.add_polylines_state(
        [[(0.0, 0.0), (40.0, 0.0), (40.0, 20.0), (0.0, 20.0), (0.0, 0.0)]], fit=True
    )
    canvas.set_selection([canvas._entities[0].id])
    assert canvas._hover_vert is None
    canvas_commands.run(canvas, "vertex.round")
    assert canvas._corner_pick_armed == "round"

    # Clicking a corner opens the prompt and live-previews as the value changes.
    cx, cy = canvas._w2c(40.0, 0.0)
    QTest.mouseClick(
        canvas,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(int(cx), int(cy)),
    )
    app.processEvents()
    edit = canvas._hud_prompt_edit
    assert edit is not None, "corner click should open the radius prompt"
    edit.setText("5")
    app.processEvents()
    assert canvas._operation_preview_polys, "typing should live-preview the round"
    canvas.close()


def test_selection_badge_editor_live_typing_single_undo(app: QApplication) -> None:
    from PySide6.QtCore import QRectF

    canvas = DxfCanvas()
    canvas.resize(600, 400)
    canvas.add_polylines_state([[(0.0, 0.0), (40.0, 0.0), (40.0, 20.0), (0.0, 20.0), (0.0, 0.0)]])
    canvas.set_selection([canvas._entities[0].id])

    def width() -> float:
        xs = [p[0] for p in canvas._entities[0].points]
        return max(xs) - min(xs)

    canvas._show_sel_dim_editor("w", QRectF(10, 10, 80, 30))
    edit = canvas._sel_dim_edit
    assert edit is not None
    # Real typing sets the text before textEdited fires; mirror that.
    edit.setText("60")
    edit.textEdited.emit("60")
    app.processEvents()
    assert width() == pytest.approx(60.0), "typing should resize live"
    edit.setText("60")  # textEdited already fired above; commit the same value
    canvas._apply_sel_dim_editor()
    assert canvas._sel_dim_edit is None
    canvas.undo()
    assert width() == pytest.approx(40.0), "one undo must revert the whole typing session"
    canvas.close()


def test_shape_hud_fields_resize_live_as_you_type(app: QApplication) -> None:
    canvas = DxfCanvas()
    canvas.resize(600, 400)
    canvas.set_mode("draw")
    canvas._draw_primitive = "rectangle"
    canvas._draw_shape_preview_active = True
    canvas._draw_shape_anchor_w = (0.0, 0.0)
    canvas._draw_shape_cursor_w = (30.0, 20.0)
    canvas._show_shape_dim_inputs()
    assert canvas._draw_shape_w_label is not None  # labeled, like the line HUD
    canvas._draw_shape_w_edit.setText("50")
    canvas._draw_shape_w_edit.textEdited.emit("50")
    app.processEvents()
    sx, _sy = canvas._draw_shape_anchor_w
    ex, _ey = canvas._draw_shape_cursor_w
    assert abs(ex - sx) == pytest.approx(50.0)
    assert canvas._draw_shape_w_edit.text() == "50", "typed text must not be overwritten"
    canvas.close()


def test_workspace_roundtrip_preserves_groups(app: QApplication) -> None:
    canvas = DxfCanvas()
    canvas.add_polylines_state(
        [
            [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0), (0.0, 0.0)],
            [(20.0, 0.0), (30.0, 0.0), (30.0, 10.0), (20.0, 10.0), (20.0, 0.0)],
        ]
    )
    ids = [e.id for e in canvas._entities]
    canvas.group_entities(ids)
    gid = next(iter(canvas._grouping_service.group_map().values()))
    canvas.set_group_label(gid, "panel")
    records = canvas.get_entity_records()
    view_state = canvas.get_view_state()
    assert "groups" not in view_state, "stale index-keyed write must stay gone"

    fresh = DxfCanvas()
    fresh.set_entity_records(records)
    fresh.set_view_state(view_state)  # session restore order: records THEN view
    assert fresh._grouping_service.group_map() == {eid: gid for eid in ids}
    assert fresh._group_labels.get(gid) == "panel"
    canvas.close()
    fresh.close()


def test_detection_handles_hand_drawn_closure_and_rotation() -> None:
    import math

    # Near-equilateral triangle, rotated 30°, 1 mm wobble, 0.8 mm closing gap.
    r = 30.0
    pts = []
    for i in range(3):
        a = math.radians(30 + i * 120)
        pts.append((r * math.cos(a), r * math.sin(a)))
    pts[1] = (pts[1][0] + 1.0, pts[1][1] - 0.5)
    pts.append((pts[0][0] + 0.8, pts[0][1]))  # imperfect closure
    assert _closed_vertices(pts) != [], "extent-relative closure should accept this"
    assert detect_primitive(pts, tolerance=0.05) is not None
    # And a clean rotated equilateral triangle passes even at the strict default.
    clean = [
        (30.0 * math.cos(math.radians(30 + i * 120)), 30.0 * math.sin(math.radians(30 + i * 120)))
        for i in range(3)
    ]
    clean.append(clean[0])
    assert detect_primitive(clean) is not None, "rotation alone must not defeat detection"


def test_merge_intersecting_open_lines_welds_the_junction(app: QApplication) -> None:
    """Merging crossing open shapes produces ONE polyline whose junction point
    appears once per arm — all copies drag together like a connected polyline."""
    canvas = DxfCanvas()
    canvas.resize(600, 400)
    canvas.add_polylines_state(
        [
            [(0.0, 10.0), (40.0, 10.0)],
            [(20.0, 0.0), (20.0, 30.0)],
        ]
    )
    canvas.set_selection([e.id for e in canvas._entities])
    assert canvas.merge_selected_segments_to_objects() == 1
    assert len(canvas._entities) == 1
    pts = canvas._entities[0].points
    junction_copies = [
        i for i, p in enumerate(pts) if abs(p[0] - 20.0) < 1e-6 and abs(p[1] - 10.0) < 1e-6
    ]
    assert len(junction_copies) == 3  # T junction visited once per arm
    linked = canvas._linked_vertices_by_id(canvas._entities[0].id, junction_copies[0])
    assert len(linked) == 3, "all junction copies must drag together"
    canvas.close()


def test_constraint_two_step_pick(app: QApplication) -> None:
    """Select one edge → Constrain → click the partner edge."""
    canvas = DxfCanvas()
    canvas.resize(600, 400)
    canvas.add_polylines_state(
        [
            [(0.0, 0.0), (30.0, 0.0)],
            [(0.0, 20.0), (30.0, 25.0)],
        ],
        fit=True,
    )
    # Select the first line and run Parallel with only one edge → picker arms.
    canvas.set_selection([canvas._entities[0].id])
    canvas._construction_service.add_geometric_constraint("parallel")
    assert canvas._constraint_pick_armed == "parallel"

    # Click the second line's edge → constraint lands on both.
    ex, ey = canvas._w2c(15.0, 22.5)
    QTest.mouseClick(
        canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(ex), int(ey))
    )
    app.processEvents()
    assert canvas._constraint_pick_armed is None
    kinds = [c.kind for c in canvas._constraints]
    assert "parallel" in kinds
    canvas.close()


def test_arrange_center_stacks_selection_centers(app: QApplication) -> None:
    canvas = DxfCanvas()
    canvas.add_polylines_state(
        [
            [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)],
            [(30.0, 30.0), (40.0, 30.0), (40.0, 40.0), (30.0, 40.0), (30.0, 30.0)],
        ]
    )
    canvas.set_selection([e.id for e in canvas._entities])
    assert canvas.align_selected("center")
    for e in canvas._entities:
        xs = [p[0] for p in e.points]
        ys = [p[1] for p in e.points]
        assert (min(xs) + max(xs)) / 2 == pytest.approx(20.0)
        assert (min(ys) + max(ys)) / 2 == pytest.approx(20.0)
    canvas.close()


def test_edit_mode_deleting_all_points_deletes_the_shape(app: QApplication) -> None:
    canvas = DxfCanvas()
    canvas.add_polylines_state([[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0), (0.0, 0.0)]])
    eid = canvas._entities[0].id
    all_verts = {(eid, i) for i in range(4)}  # closed: last is the closing dup
    canvas._selection_service._delete_edit_vertices(all_verts)
    assert not canvas._entities, "deleting every point should delete the shape"
    canvas.close()


def test_layer_tree_double_click_renames_without_collapse(app: QApplication) -> None:
    from simple_stipple.features.draft import DraftPage

    page = DraftPage(settings={})
    tree = page._layers_tree
    app.processEvents()
    assert tree._tree.expandsOnDoubleClick(), "plain clicks must not toggle expansion"
    item = tree._tree.topLevelItem(0)
    assert item is not None
    expanded = item.isExpanded()
    renamed: list[tuple[str, str]] = []
    tree.layerRenamed.connect(lambda old, new: renamed.append((old, new)))
    item.setText(0, "Panel A")
    tree._on_item_double_clicked(item, 0)
    app.processEvents()
    assert item.isExpanded() == expanded
    page.close()


def test_draw_sidebar_default_sections_are_minimal() -> None:
    from simple_stipple.platform.settings import DEFAULT_DRAW_SIDEBAR_SECTIONS

    assert list(DEFAULT_DRAW_SIDEBAR_SECTIONS) == ["path", "shapes", "text", "mode"]


def test_pattern_page_has_visibility_toggle(app: QApplication) -> None:
    from simple_stipple.features.pattern.page import PatternPage

    page = PatternPage(settings={})
    btn = page._pattern_visible_btn
    assert btn.isCheckable() and btn.isChecked()
    btn.setChecked(False)
    app.processEvents()
    assert page._canvas.result_visible() is False
    # The layer-tree eye path drives the same state and re-syncs the button.
    page._set_result_visible(True)
    app.processEvents()
    assert btn.isChecked()
    page.close()


class _StubApp(QObject):
    """QObject parent with the one attribute AutoCommitController reads."""

    def __init__(self, settings: dict) -> None:
        super().__init__()
        self._settings = settings


def test_auto_commit_controller_watches_only_when_enabled(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubApp({})
    ctrl = AutoCommitController(stub)
    ctrl.configure()
    assert not ctrl._poll_timer.isActive()

    stub._settings["auto_commit_push"] = True
    ctrl.configure()
    # Active only when a git repo is resolvable (true inside the dev checkout).
    assert ctrl._poll_timer.isActive() == (ctrl._repo() is not None)

    launched: list[tuple[str, list]] = []
    monkeypatch.setattr(
        ctrl, "_launch", lambda tag, cmds: launched.append((tag, cmds)) or True
    )
    ctrl._poll()
    assert launched == [("status", [["status", "--porcelain"]])]

    # Dirty status starts the quiet period; elapsing re-verifies, then commits.
    ctrl._on_git_done(("status", [(["status"], True, " M file.py")]))
    assert ctrl._quiet_timer.isActive()
    ctrl._quiet_timer.timeout.emit()
    assert launched[-1][0] == "verify"
    ctrl._on_git_done(("verify", [(["status"], True, " M file.py")]))
    tag, cmds = launched[-1]
    assert tag == "commit"
    assert cmds[0] == ["add", "-A"]
    assert cmds[1][:2] == ["commit", "-m"]
    assert cmds[2] == ["push"]

    # A clean verify (user committed by hand meanwhile) commits nothing.
    before = len(launched)
    ctrl._on_git_done(("verify", [(["status"], True, "")]))
    assert len(launched) == before

    ctrl.shutdown()
    assert not ctrl._poll_timer.isActive()
