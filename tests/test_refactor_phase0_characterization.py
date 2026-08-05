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
from PySide6.QtWidgets import QApplication

import simple_stipple.canvas.view.main as canvas_view_main
import simple_stipple.features.trace.page as trace_page_module
from simple_stipple.canvas.rendering import overlays, scene
from simple_stipple.canvas.view.main import CanvasView
from simple_stipple.canvas.widget import DxfCanvas
from simple_stipple.features.pattern import export_jobs
from simple_stipple.features.pattern import workers as pattern_workers
from simple_stipple.features.pattern.workers import run_generate
from simple_stipple.features.trace.page import TracePage


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
    scene.paint_document_scene(renderer, object(), 400, 300, object())
    overlays.paint_selection_overlay(renderer, object(), object())
    overlays.paint_chrome_rulers(renderer, object())

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
        "_paint_select_handles",
        "_paint_rulers",
    ]


def test_pattern_export_job_builds_then_writes_and_reports_complete_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pattern export keeps its pure-worker protocol during extraction."""
    calls: list[tuple[str, object]] = []
    completed: list[tuple] = []
    failures: list[tuple] = []

    class PatternService:
        def build_pattern_polys(self, *_args, **_kwargs):
            calls.append(("build", _kwargs["pattern"]))
            _kwargs["fill_polys_out"].append([(0.0, 1.0), (1.0, 1.0)])
            return [[(0.0, 0.0), (1.0, 0.0)]]

        def should_close_pattern(self, _pattern: str) -> bool:
            return True

    def write(polys, path, **kwargs) -> None:
        calls.append(("write", (polys, path, kwargs)))

    monkeypatch.setattr(pattern_workers, "prepare_output", lambda polys, _options: polys)
    monkeypatch.setattr(pattern_workers.DxfService, "write_polylines_dxf", write)

    run_generate(
        [[(0.0, 0.0), (1.0, 0.0)]],
        "/tmp/characterized-pattern.dxf",
        "lines",
        {},
        (1.0, 1.0),
        [[(0.0, 0.0), (1.0, 0.0)]],
        generation_token=23,
        pattern_service=PatternService(),
        orig_w=1.0,
        orig_h=1.0,
        on_done=completed.append,
        on_error=failures.append,
    )

    assert [name for name, _payload in calls] == ["build", "write"]
    assert completed == [
        (
            23,
            2,
            "characterized-pattern.dxf",
            "/tmp/characterized-pattern.dxf",
            [[(0.0, 0.0), (1.0, 0.0)], [(0.0, 1.0), (1.0, 1.0)]],
        )
    ]
    assert failures == []


def test_pattern_export_jobs_keep_engraving_and_laserstar_payloads_together(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Feature export jobs own payload construction; the page only owns UI state."""
    job = export_jobs.EngravingJob(
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
        lambda source, output, spec, mask: calls.append((source, output, spec, mask))
        or (positioned, {}, None),
    )
    monkeypatch.setattr(
        export_jobs,
        "export_laserstar_package",
        lambda *args, **kwargs: calls.append((args, kwargs)) or package,
    )

    assert export_jobs.export_positioned_engraving("source.png", "out.png", job, [[(0.0, 0.0)]]) == positioned
    assert export_jobs.export_laserstar_job(
        "exports", "job", [[(0.0, 0.0)]], engraving_source="source.png", engraving_job=job
    ) == package
    raster_spec = calls[0][2]
    assert (raster_spec.x_mm, raster_spec.width_mm, raster_spec.invert) == (1.0, 30.0, True)
    assert calls[1][1]["raster_spec"].rotation_deg == 15.0


def test_trace_job_emits_success_or_cancellation_without_touching_page_widgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trace extraction can move out of the page without changing its signals."""
    done: list[tuple] = []
    errors: list[tuple] = []
    cancelled: list[int] = []
    page = SimpleNamespace(
        _trace_done=SimpleNamespace(emit=done.append),
        _trace_error=SimpleNamespace(emit=errors.append),
        _trace_cancelled=SimpleNamespace(emit=cancelled.append),
    )
    image_result = ("display", [[(0.0, 0.0), (1.0, 1.0)]], 200, 100)
    monkeypatch.setattr(trace_page_module, "image_to_outlines", lambda *_args, **_kwargs: image_result)

    TracePage._run_trace(page, "source.png", {"width_mm": 40.0}, 31, threading.Event())
    TracePage._run_trace(page, "source.png", {"width_mm": 40.0}, 32, _set_event())

    assert done == [(31, *image_result, 40.0)]
    assert errors == []
    assert cancelled == [32]


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
