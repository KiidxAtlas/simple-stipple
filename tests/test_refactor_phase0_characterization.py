"""Characterization coverage for the seams scheduled for structural extraction.

These tests deliberately describe observable collaboration between the current
modules.  They are a safety net for the later moves; they do not prescribe a
new module layout.
"""

from __future__ import annotations

import os
import threading
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

import simple_stipple.canvas.view.main as canvas_view_main
from simple_stipple.canvas.renderer import CanvasRenderer
from simple_stipple.canvas.tools import radial_menu
from simple_stipple.canvas.tools import tools as canvas_tools_module
from simple_stipple.canvas.tools.dimension_backend import DimensionBackend
from simple_stipple.canvas.tools.dimension_tool import DimensionTool as SketchDimensionTool
from simple_stipple.canvas.tools.tools import RadialMenuService
from simple_stipple.canvas.view.main import CanvasView
from simple_stipple.canvas.widget import DxfCanvas
from simple_stipple.features.draft.session import build_dxf_export_plan
from simple_stipple.features.pattern import export as export_jobs
from simple_stipple.features.pattern.outline_state import read_outline_vector
from simple_stipple.features.pattern.workers import PreviewRequest, solve_preview
from simple_stipple.features.trace.page import TracePage
from simple_stipple.features.trace.session import run_trace_job
from simple_stipple.platform.jobs import JobCancelled


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_context_actions_follow_canvas_mode_and_selection_state(app: QApplication) -> None:
    """Context commands stay owned by the canvas as interaction moves out."""
    canvas = DxfCanvas()
    canvas.add_polylines_state(
        [
            [(0.0, 0.0), (5.0, 0.0)],
            [(0.0, 2.0), (5.0, 2.0)],
        ]
    )
    canvas.select_all()

    assert [item[0] for item in canvas.get_context_actions()] == [
        "delete-selection",
        "group-selection",
    ]
    assert canvas.trigger_context_action("group-selection")
    assert len({entity.group for entity in canvas._entities}) == 1
    assert canvas._entities[0].group is not None

    canvas.set_mode("draw")
    canvas._draw_pts = [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0)]
    assert [item[0] for item in canvas.get_context_actions()] == [
        "undo-point",
        "finish-path",
        "close-path",
        "cancel-draw",
    ]
    canvas.close()


def test_quick_and_procedural_shapes_share_drag_commit_contract(app: QApplication) -> None:
    """Future quick-shape extraction must retain the shared drag lifecycle."""
    canvas = DxfCanvas()
    canvas.resize(640, 480)

    canvas.set_quick_shape_mode("rectangle", flash=False)
    assert canvas.quick_shape_enabled
    canvas._start_shape_drag("rectangle", QPointF(100.0, 100.0))
    canvas._finish_shape_drag(QPoint(220, 180))
    assert len(canvas._entities) == 1
    # Basic quick shapes are normal polylines; procedural shapes preserve
    # their kind metadata.  Both must still use the same drag commit path.
    assert canvas._entities[0].kind == "polyline"

    canvas._draw_split_enabled = False
    canvas.set_quick_shape_mode("ring", flash=False)
    canvas._start_shape_drag("ring", QPointF(280.0, 100.0))
    canvas._finish_shape_drag(QPoint(400, 180))
    assert [entity.kind for entity in canvas._entities[-2:]] == ["ring", "ring"]
    canvas.close()


def test_canvas_paint_pipeline_keeps_scene_then_tool_then_chrome_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tool affordances and rulers must remain above the rendered scene."""
    events: list[str] = []

    class Painter:
        class RenderHint:
            Antialiasing = object()

        def __init__(self, _parent) -> None:
            events.append("painter")

        def setRenderHint(self, _hint) -> None:
            events.append("antialias")

        def end(self) -> None:
            events.append("end")

    class Renderer:
        def paintEvent(self, _event) -> None:
            events.append("scene")

        def _paint_chrome_rulers(self, _painter) -> None:
            events.append("chrome")

    class Tool:
        def paint_overlay(self, _painter) -> None:
            events.append("tool")

    host = SimpleNamespace(
        _renderer=Renderer(),
        _measure_mode=False,
        _dimension_mode=False,
        _tools={"select": Tool()},
        _mode="select",
    )
    monkeypatch.setattr(canvas_view_main, "QPainter", Painter)

    CanvasView.paintEvent(host, None)

    assert events == ["scene", "painter", "antialias", "tool", "chrome", "end"]


def test_renderer_scene_and_selection_passes_keep_internal_layer_order() -> None:
    """The renderer split must not put selection chrome beneath document geometry."""
    events: list[str] = []

    class Renderer:
        _host = SimpleNamespace(_mode="select", _sel={"one"}, width=lambda: 400, height=lambda: 300)

        def __getattr__(self, name):
            if name.startswith("_paint_"):
                return lambda *_args: events.append(name)
            raise AttributeError(name)

    renderer = Renderer()
    frame = SimpleNamespace(
        selection=frozenset({"one"}),
        interaction=SimpleNamespace(mode="select"),
    )
    CanvasRenderer._paint_document_scene(renderer, object(), 400, 300, object())
    CanvasRenderer._paint_selection_overlay(renderer, object(), object(), frame)
    CanvasRenderer._paint_chrome_rulers(renderer, object())

    assert events == [
        "_paint_guides",
        "_paint_dimensions",
        "_paint_ghost_polys",
        # Solved pattern sits under the editable outlines: the user edits
        # geometry, never output.
        "_paint_result_polys",
        "_paint_main_polys",
        "_paint_operation_preview",
        # Preflight findings ride on top: a problem you cannot see is one you
        # only meet at the machine.
        "_paint_issue_markers",
        "_paint_selection_bbox",
        "_paint_selection_readout",
        "_paint_rulers",
    ]


def test_radial_menu_filters_hits_dispatches_and_paints(app: QApplication, monkeypatch) -> None:
    """The radial service has a stable standalone host contract before its move."""
    assert RadialMenuService is radial_menu.RadialMenuService
    assert canvas_tools_module.RadialMenuService is radial_menu.RadialMenuService
    redraws: list[None] = []
    host = SimpleNamespace(
        _radial_active=False,
        _radial_hover_index=None,
        _radial_tools=[],
        _radial_center_c=QPoint(200, 200),
        _cursor_wx=None,
        _cursor_wy=None,
        width=lambda: 400,
        height=lambda: 400,
        _redraw=lambda: redraws.append(None),
    )
    service = RadialMenuService(host)
    service.set_radial_menu_tools(
        ["invalid.command", "mode.draw", "canvas.rectangle", "canvas.rectangle", "canvas.circle"]
    )

    assert host._radial_tools == ["mode.draw", "canvas.rectangle", "canvas.circle"]
    assert service._radial_index_at(260.0, 200.0) == 0
    assert service._radial_index_at(200.0, 140.0) == 1
    assert service._radial_index_at(200.0, 200.0) is None

    dispatched: list[tuple[object, str]] = []
    monkeypatch.setattr(
        "simple_stipple.canvas.tools.tools.canvas_commands.run",
        lambda target, command: dispatched.append((target, command)),
    )
    service._execute_radial_action(1)
    service._execute_radial_action(9)
    assert dispatched == [(host, "canvas.rectangle")]

    image = QImage(400, 400, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    service._paint_radial_menu(painter)
    painter.end()
    assert image.pixelColor(200, 200).alpha() > 0


def test_dimension_backend_keeps_geometry_and_driver_contract() -> None:
    """The sketch workflow inherits its associative-dimension backend intact."""
    backend = DimensionBackend(SimpleNamespace())

    assert issubclass(SketchDimensionTool, DimensionBackend)
    assert DimensionBackend._inclusive_intersection(
        {"p1": (0.0, 0.0), "p2": (10.0, 0.0)},
        {"p1": (5.0, -5.0), "p2": (5.0, 5.0)},
    ) == (5.0, 0.0)
    assert backend.value({"p1": (0.0, 0.0), "p2": (3.0, 4.0)}) == 5.0


def test_pattern_export_jobs_keep_engraving_and_laserstar_payloads_together(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Feature export jobs own payload construction; the page only owns UI state."""
    job = export_jobs.build_engraving_job(
        x_mm=1.0,
        y_mm=2.0,
        width_mm=30.0,
        height_mm=20.0,
        line_interval_mm=0.1,
        min_power_percent=10.0,
        max_power_percent=80.0,
        speed_mm_s=250.0,
        gamma=1.2,
        invert=True,
        passes=2,
        rotation_deg=15.0,
    )
    calls: list[tuple] = []
    positioned = tmp_path / "engraving.png"
    package = tmp_path / "laserstar"

    monkeypatch.setattr(
        export_jobs,
        "export_raster_job",
        lambda source, output, spec, mask: (
            calls.append((source, output, spec, mask)) or (positioned, {}, None)
        ),
    )
    monkeypatch.setattr(
        export_jobs,
        "export_laserstar_package",
        lambda *args, **kwargs: calls.append((args, kwargs)) or package,
    )

    assert (
        export_jobs.export_positioned_engraving("source.png", "out.png", job, [[(0.0, 0.0)]])
        == positioned
    )
    assert (
        export_jobs.export_laserstar_job(
            "exports", "job", [[(0.0, 0.0)]], engraving_source="source.png", engraving_job=job
        )
        == package
    )
    raster_spec = calls[0][2]
    assert (raster_spec.x_mm, raster_spec.width_mm, raster_spec.invert) == (1.0, 30.0, True)
    assert calls[1][1]["raster_spec"].rotation_deg == 15.0


def test_trace_outcomes_route_to_success_error_or_cancellation_handlers() -> None:
    """The page routes each finished trace job to exactly one handler."""
    done: list[tuple] = []
    errors: list[tuple] = []
    cancelled: list[int] = []
    page = SimpleNamespace(
        _handle_trace_done=done.append,
        _handle_trace_error=errors.append,
        _handle_trace_cancelled=cancelled.append,
    )
    image_result = ("display", [[(0.0, 0.0), (1.0, 1.0)]], 200, 100)

    def pipeline(*_args, **_kwargs):
        return image_result

    for token, event in ((31, threading.Event()), (32, _set_event())):
        outcome = run_trace_job("source.png", {}, token, event, trace_pipeline=pipeline)
        TracePage._apply_trace_outcome(page, outcome, 40.0)
    TracePage._apply_trace_outcome(page, run_trace_job(None, {}, 33), 40.0)

    assert done == [(31, *image_result, 40.0)]
    assert errors == [(33, "No image selected.")]
    assert cancelled == [32]


def test_trace_force_reload_restarts_stalled_worker(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = TracePage(settings={})
    page._img_path = "source.png"
    page._running = True
    page._active_trace_token = 7
    old_event = page._cancel_event
    starts: list[bool] = []
    monkeypatch.setattr(page, "_start_trace", lambda: starts.append(True))

    page._force_reload_trace()

    assert old_event.is_set()
    assert starts == [True]
    assert page._trace_pending is False
    page._active_trace_token = 9
    page._running = True
    page._handle_trace_cancelled(7)
    assert page._running is True
    page.shutdown()
    page.close()


def test_trace_job_classifies_success_cancellation_and_failures() -> None:
    image_result = ("display", [[(0.0, 0.0), (1.0, 1.0)]], 200, 100)

    outcome = run_trace_job(
        "source.png",
        {"width_mm": 40.0},
        31,
        trace_pipeline=lambda *_args, **_kwargs: image_result,
    )
    assert outcome.result == image_result
    assert not outcome.cancelled
    assert outcome.error is None

    cancelled = _set_event()
    assert run_trace_job("source.png", {}, 32, cancelled).cancelled

    failed = run_trace_job("source.png", {}, 33, trace_pipeline=lambda *_args, **_kwargs: 1 / 0)
    assert failed.error == "division by zero"


def test_draft_dxf_export_plan_preserves_layers_and_adds_dimensions() -> None:
    plan = build_dxf_export_plan(
        [{"polyline": [(0.0, 0.0), (1.0, 1.0)], "kind": "line", "layer": "Cut"}],
        [{"p1": (1.0, 0.0), "p2": (1.0, 2.0), "layer": "Notes", "text": "2 mm"}],
        active_layer_name="Cut",
        layer_names=["Notes", "Cut"],
    )

    assert plan.first_layer_name == "Notes"
    assert plan.first_layer_records[0]["kind"] == "dimension"
    assert plan.extra_layer_records == {
        "Cut": [{"polyline": [(0.0, 0.0), (1.0, 1.0)], "kind": "line", "layer": "Cut"}]
    }


def test_pattern_fvi_outline_reader_is_ui_independent() -> None:
    document = SimpleNamespace(paths=[[(0.0, 0.0), (1.0, 1.0)]])

    imported = read_outline_vector("outline.fvi", read_fvi_file=lambda _path: document)

    assert imported.polylines == [[(0.0, 0.0), (1.0, 1.0)]]
    assert imported.images == []


def test_pattern_preview_dispatch_snapshots_the_page_into_one_request(
    app: QApplication, monkeypatch
) -> None:
    """The page hands the solve a frozen snapshot, never itself."""
    from simple_stipple.features.pattern.page import PatternPage

    page = PatternPage(settings={})
    page.load_outline_polys([[(0.0, 0.0), (20.0, 0.0), (20.0, 10.0), (0.0, 10.0), (0.0, 0.0)]])
    page._preview_timer.stop()
    captured: list = []
    monkeypatch.setattr(
        page._jobs, "submit", lambda key, work, **_kwargs: captured.append((key, work))
    )

    page._start_preview()

    key, work = captured[-1]
    request, service = work.args
    assert key == "preview" and work.func is solve_preview
    assert service is page._pattern_service
    assert request.all_polys == page._generation_polys()
    assert request.params["quality"] == page._preview_quality_combo.currentData()
    assert (request.orig_w, request.orig_h) == (page._orig_w, page._orig_h)
    assert request.zones == ()
    page.shutdown()
    page.close()


def test_pattern_preview_solve_selects_zone_or_outline_service_and_honours_cancel() -> None:
    calls: list[tuple[str, dict]] = []

    class Service:
        def build_preview_polys(self, _polys, **kwargs):
            calls.append(("outline", kwargs))
            return {"display": [], "count": 0}

        def build_preview_zone_polys(self, zones, _polys, **kwargs):
            calls.append(("zones", {"zones": zones, **kwargs}))
            return {"display": [], "count": 0}

    base = PreviewRequest(
        all_polys=[[(0.0, 0.0)]],
        pattern="Grid",
        params={},
        scale=(1.0, 1.0),
        border_fade=0.2,
        orig_w=10.0,
        orig_h=5.0,
        fill_options={"spacing": 1.0},
    )
    zoned = PreviewRequest(**{**base.__dict__, "zones": ({"region_id": "outer"},)})

    solve_preview(base, Service(), threading.Event())
    solve_preview(zoned, Service(), threading.Event())

    assert [name for name, _kwargs in calls] == ["outline", "zones"]
    assert calls[0][1]["border_fade"] == 0.2
    assert calls[1][1]["zones"] == [{"region_id": "outer"}]
    assert calls[1][1]["fill_options"] == {"spacing": 1.0}
    with pytest.raises(JobCancelled):
        solve_preview(base, Service(), _set_event())


def _set_event() -> threading.Event:
    event = threading.Event()
    event.set()
    return event


def test_parametric_gizmo_handle_drag_updates_the_live_entity(app: QApplication) -> None:
    """Dragging a parametric shape's scale handle must not call a phantom method.

    The parametric branch committed through ``_update_entity_in_storage``,
    which has never existed on the canvas, so every such drag raised
    AttributeError mid-gesture. It mutates the live entity in place like the
    uniform-scale branch and commits on release.
    """
    from PySide6.QtCore import QPoint, QPointF, Qt

    canvas = DxfCanvas(selectable=True)
    canvas.resize(800, 600)
    canvas.set_quick_shape_mode("ring", flash=False)
    canvas._start_shape_drag("ring", QPointF(200.0, 200.0))
    canvas._finish_shape_drag(QPoint(320, 300))
    entity_id = canvas._entities[0].id
    canvas._sel = {entity_id}

    wx, wy = canvas._c2w(320, 300)
    assert canvas._start_gizmo_drag("scale-e", wx, wy)
    for offset in (10, 20, 30):
        moved_x, moved_y = canvas._c2w(320 + offset, 300)
        canvas._apply_gizmo_drag(moved_x, moved_y, Qt.KeyboardModifier.NoModifier)
    assert canvas._gizmo_drag_moved
    canvas.close()
