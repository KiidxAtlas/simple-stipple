"""CanvasView — interactive pan/zoom canvas widget with polyline selection, measure, draw, and edit tools."""

from __future__ import annotations

import math
import time
from copy import deepcopy
from typing import Any, cast

from PIL import Image as PILImage
from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QMenu, QSpinBox, QWidget

from src.app.services.canvas_service import CanvasService
from src.backend.cad.constraints import GeometricConstraint
from src.backend.cad.editor_geometry import (
    CanvasGeometry,
    entity_shows_point_handles,
    geometry_for_entity,
    shape_for_entity,
)
from src.backend.cad.shapes import ShapeFactory
from src.backend.cad.snapping import (
    polygon_centroid as _polygon_centroid,
)
from src.backend.model.commands import DocumentSnapshot
from src.backend.model.document import CanvasDocument, EntityRecord, OperationResult
from src.core.settings import (
    DEFAULT_CONTEXT_MENU_SECTIONS,
    DEFAULT_DRAW_SIDEBAR_ALWAYS_VISIBLE,
    DEFAULT_DRAW_SIDEBAR_PATH_TOOLS,
    DEFAULT_DRAW_SIDEBAR_SECTIONS,
    DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS,
    DEFAULT_DRAW_SIDEBAR_WIDTH,
    DEFAULT_SIMPLIFY_TOLERANCE,
    DEFAULT_SMOOTH_ITERATIONS,
    DEFAULT_SMOOTHING_METHOD,
)
from src.ui.canvas.canvas_model import CanvasModel
from src.ui.canvas.constants import DRAG_THRESH
from src.ui.canvas.constants import MIN_SCALE as _MIN_SCALE
from src.ui.canvas.interaction import commands as canvas_commands
from src.ui.canvas.interaction import tools as canvas_tools
from src.ui.canvas.interaction.select import SelectionService
from src.ui.canvas.rendering.renderer import CanvasRenderer
from src.ui.canvas.services.clipboard import ClipboardService
from src.ui.canvas.services.draw_ops import ConstructionService, DrawOpsService
from src.ui.canvas.services.editing import EditingService
from src.ui.canvas.services.gizmo import GizmoService
from src.ui.canvas.services.grouping import GroupingService
from src.ui.canvas.services.hit_test import HitTestService
from src.ui.canvas.services.hud_text import HudTextService, TextService
from src.ui.canvas.services.layer_service import LayerService
from src.ui.canvas.services.smoothing import SmoothingService
from src.ui.canvas.services.snap_service import SnapService
from src.ui.canvas.snap import SnapEngine
from src.ui.components import blur_focused_line_edit
from src.ui.util import DEFAULT_UNIT_SYSTEM
from src.ui.widgets.canvas.draw_sidebar import DrawSidebar

_MAX_SCALE = 20000.0  # px per mm — deep zoom for tiny features


def _rehydrate_meta(meta: dict) -> dict:
    """Convert JSON-round-tripped meta lists back to point tuples."""
    out = dict(meta)
    for key in ("start", "end", "center"):
        v = out.get(key)
        if isinstance(v, (list, tuple)) and len(v) >= 2:
            out[key] = (float(v[0]), float(v[1]))
    cps = out.get("control_points")
    if isinstance(cps, list):
        out["control_points"] = [
            (float(p[0]), float(p[1])) for p in cps if isinstance(p, (list, tuple)) and len(p) >= 2
        ]
    return out


class CanvasView(
    QWidget,
):
    """
    Displays polyline lists with Select / Draw / Edit modes.

    Modes:
    - ``select`` — click polylines to select/deselect, Shift+drag rubber-band
    - ``draw``   — click to place vertices, finish with dbl-click/Enter/right-click
    - ``edit``   — drag vertices, double-click edge to insert, right-click vertex to delete

    Set ``selectable=False`` for a display-only preview (no mode switching).
    """

    @property
    def _document(self) -> CanvasDocument:
        return self._model.document

    @_document.setter
    def _document(self, document: CanvasDocument) -> None:
        model = self.__dict__.get("_model")
        if model is None:
            self._model = CanvasModel(document, self)
        elif service := self.__dict__.get("_canvas_service"):
            service.replace_document(document)
        else:
            model.replace_document(document)

    @property
    def _entities(self) -> list[EntityRecord]:
        return self._document.entities

    @_entities.setter
    def _entities(self, entities: list[EntityRecord]) -> None:
        model = self.__dict__.get("_model")
        if model is None:
            self._document = CanvasDocument(list(entities))
        else:
            document = deepcopy(model.document)
            document.replace(entities)
            service = self.__dict__.get("_canvas_service")
            if service is None:
                model.replace_document(document)
            else:
                service.replace_document(document)

    @property
    def _sel(self) -> set[int]:
        return self._document.selection

    @_sel.setter
    def _sel(self, selection: set[int]) -> None:
        from src.backend.model.commands import SelectCommand

        entity_ids = tuple(
            self._entities[index].id
            for index in sorted(selection)
            if 0 <= index < len(self._entities)
        )
        service = self.__dict__.get("_canvas_service")
        if service is None:
            self._document.select_ids(entity_ids)
        else:
            service.execute(SelectCommand(entity_ids=entity_ids), record=False)

    @property
    def _layer_order(self) -> list[str]:
        return self._document.layer_order

    @_layer_order.setter
    def _layer_order(self, value: list[str]) -> None:
        self._document.layer_order = list(value)

    @property
    def _active_layer(self) -> str | None:
        return self._document.active_layer

    @_active_layer.setter
    def _active_layer(self, value: str | None) -> None:
        self._document.active_layer = value

    @property
    def _layer_colors(self) -> dict[str, str]:
        return self._document.layer_colors

    @_layer_colors.setter
    def _layer_colors(self, value: dict[str, str]) -> None:
        self._document.layer_colors = dict(value)

    @property
    def _group_labels(self) -> dict[int, str]:
        return self._document.group_labels

    @_group_labels.setter
    def _group_labels(self, value: dict[int, str]) -> None:
        self._document.group_labels = dict(value)

    @property
    def _next_group_id(self) -> int:
        return self._document.next_group_id

    @_next_group_id.setter
    def _next_group_id(self, value: int) -> None:
        self._document.next_group_id = int(value)

    @property
    def _constraints(self) -> list[GeometricConstraint]:
        return self._document.constraints

    @_constraints.setter
    def _constraints(self, value: list[GeometricConstraint]) -> None:
        self._document.constraints = list(value)

    selectionChanged = Signal(int)  # type: ignore[assignment]
    geometryChanged = Signal()
    modeChanged = Signal(str)
    drawSidebarWidthChanged = Signal(int)
    drawSidebarHeightChanged = Signal(int)
    smoothingMethodChanged = Signal(str)
    smoothIterationsChanged = Signal(int)
    simplifyToleranceChanged = Signal(float)

    def _flagged(self, attr: str) -> set[int]:
        """Indices of entities whose boolean ``attr`` is set."""
        return self._document.flagged_indices(attr)

    def _set_flagged(self, attr: str, indices) -> None:
        """Set boolean ``attr`` to exactly ``indices`` (wholesale assignment)."""
        self._document.set_flagged_indices(attr, indices)

    def _group_of(self, index: int) -> int | None:
        return self._grouping_service.group_of(index)

    def _group_map(self) -> dict[int, int]:
        return self._grouping_service.group_map()

    def _group_selected(self) -> None:
        self._grouping_service.group_selected()

    def set_group_label(self, group_id: int, label: str) -> None:
        self._grouping_service.set_label(group_id, label)

    def _ungroup_selected(self) -> None:
        self._grouping_service.ungroup_selected()

    def group_indices(self, indices: list[int]) -> int:
        return self._grouping_service.group_indices(indices)

    def ungroup_indices(self, indices: list[int]) -> int:
        return self._grouping_service.ungroup_indices(indices)

    @property
    def active_layer(self) -> str | None:
        return self._layer_service.active_layer

    def layer_names(self) -> list[str]:
        return self._layer_service.names()

    def set_layer_model(self, order: list[str], active: str | None) -> None:
        self._layer_service.set_model(order, active)

    def set_active_layer(self, name: str) -> None:
        self._layer_service.set_active(name)

    def add_layer(self, name: str, *, activate: bool = False) -> None:
        self._layer_service.add(name, activate=activate)

    def rename_layer(self, old: str, new: str) -> None:
        self._layer_service.rename(old, new)

    def delete_layer(self, name: str) -> None:
        self._layer_service.delete(name)

    def layer_color(self, name: str) -> str | None:
        return self._layer_service.color(name)

    def consolidate_layers(self, source_layers: list[str], target_layer: str) -> int:
        return self._layer_service.consolidate(source_layers, target_layer)

    def set_layer_color(self, name: str, color: str | None) -> None:
        self._layer_service.set_color(name, color)

    def move_layer(self, name: str, new_index: int) -> None:
        self._layer_service.move(name, new_index)

    def move_indices_to_layer(self, indices: list[int], layer: str) -> int:
        return self._layer_service.move_entities(indices, layer)

    def _on_active_layer(self, entity: EntityRecord) -> bool:
        return self._layer_service.on_active(entity)

    def _entity_selectable(self, index: int) -> bool:
        return self._layer_service.selectable(index)

    def _noninteractive_indices(self) -> set[int]:
        return self._layer_service.noninteractive_indices()

    def _drop_inactive_selection(self) -> None:
        self._layer_service.drop_inactive_selection()

    @property
    def _clipboard(self) -> list[dict[str, Any]]:
        return self._clipboard_service.records

    @_clipboard.setter
    def _clipboard(self, records: list[dict[str, Any]]) -> None:
        self._clipboard_service.records = records

    def _copy_selected(self) -> None:
        self._clipboard_service.copy_selected()

    def _paste_records(self, dx: float, dy: float | None = None) -> list[int]:
        return self._clipboard_service.paste_records(dx, dy)

    def _paste_clipboard(self) -> None:
        self._clipboard_service.paste()

    def _duplicate_selected(self) -> None:
        self._clipboard_service.duplicate()

    def _duplicate_selected_with_offset(self) -> None:
        self._clipboard_service.duplicate_with_offset()

    def _paste_clipboard_with_offset(self, offset: float) -> None:
        self._clipboard_service.paste_with_offset(offset)

    def _paste_clipboard_multiple(self) -> None:
        self._clipboard_service.prompt_multi_paste()

    def _array_duplicate_grid(self) -> None:
        self._clipboard_service.prompt_grid()

    def _apply_grid_array(self, columns: int, rows: int, spacing: float) -> bool:
        return self._clipboard_service.apply_grid(columns, rows, spacing)

    def _array_duplicate_radial(self) -> None:
        self._clipboard_service.prompt_radial()

    def _apply_radial_array(self, count: int, radius: float) -> bool:
        return self._clipboard_service.apply_radial(count, radius)

    def _array_duplicate_along_path(self) -> None:
        self._clipboard_service.prompt_along_path()

    def _cut_selected(self) -> None:
        self._clipboard_service.cut_selected()

    def _nudge_selected(self, dx: float, dy: float) -> None:
        self._clipboard_service.nudge_selected(dx, dy)

    @staticmethod
    def _segment_intersection_point(
        a1: tuple[float, float],
        a2: tuple[float, float],
        b1: tuple[float, float],
        b2: tuple[float, float],
    ) -> tuple[float, float] | None:
        return HitTestService.segment_intersection(a1, a2, b1, b2)

    def _find_nearest_endpoint(self, cx: float, cy: float) -> tuple[float, float] | None:
        return self._hit_test.nearest_endpoint(cx, cy)

    def _find_nearest_vertex(self, cx: float, cy: float) -> tuple[int, int] | None:
        return self._hit_test.nearest_vertex(cx, cy)

    @staticmethod
    def _poly_closed_n(poly: list[tuple[float, float]]) -> int:
        return HitTestService.segment_count(poly)

    def _closest_point_on_poly(
        self,
        poly: list[tuple[float, float]],
        wx: float,
        wy: float,
        cx: float,
        cy: float,
        *,
        return_segment: bool = False,
    ):
        return self._hit_test.closest_point(poly, wx, wy, cx, cy, return_segment=return_segment)

    def _find_nearest_edge(
        self, cx: float, cy: float
    ) -> tuple[int, int, tuple[float, float]] | None:
        return self._hit_test.nearest_edge(cx, cy)

    def _find_poly_at(self, cx: float, cy: float) -> int | None:
        return self._hit_test.entity_at(cx, cy)

    def _find_guide_at(self, cx: float, cy: float) -> int | None:
        return self._hit_test.guide_at(cx, cy)

    def _find_inactive_poly_at(self, cx: float, cy: float) -> int | None:
        return self._hit_test.inactive_entity_at(cx, cy)

    def _find_ghost_poly_at(self, cx: float, cy: float) -> int | None:
        return self._hit_test.ghost_at(cx, cy)

    def _object_snap_adjust(self, dx: float, dy: float):
        return self._snap_service._object_snap_adjust(dx, dy)

    def _resize_handle_snap_adjust(self, wx: float, wy: float) -> tuple[float, float, str] | None:
        return self._snap_service._resize_handle_snap_adjust(wx, wy)

    def _start_gizmo_drag(
        self, mode: str, wx: float, wy: float, *, from_center: bool = False
    ) -> bool:
        return self._gizmo_service._start_gizmo_drag(mode, wx, wy, from_center=from_center)

    def _apply_gizmo_drag(
        self, wx: float, wy: float, mods: Qt.KeyboardModifier | None = None
    ) -> None:
        self._gizmo_service._apply_gizmo_drag(wx, wy, mods)

    def _end_gizmo_drag(self) -> bool:
        return self._gizmo_service._end_gizmo_drag()

    def _show_flash(self, text: str, duration_ms: int = 1200) -> None:
        self._hud_service._show_flash(text, duration_ms)

    def _make_hud_edit(self, *args, **kwargs):
        return self._hud_service._make_hud_edit(*args, **kwargs)

    def _make_hud_spinbox(self, *args, **kwargs):
        return self._hud_service._make_hud_spinbox(*args, **kwargs)

    def _show_hud_prompt(self, *args, **kwargs) -> None:
        self._hud_service._show_hud_prompt(*args, **kwargs)

    def _show_text_hud_prompt(self, *args, **kwargs) -> None:
        self._hud_service._show_text_hud_prompt(*args, **kwargs)

    def _hud_position_near(self, *args, **kwargs) -> tuple[int, int]:
        return self._hud_service._hud_position_near(*args, **kwargs)

    def _dismiss_hud_prompt(self) -> None:
        self._hud_service._dismiss_hud_prompt()

    def _show_dim_inputs(self) -> None:
        self._hud_service._show_dim_inputs()

    def _dismiss_dim_inputs(self) -> None:
        self._hud_service._dismiss_dim_inputs()

    def _show_sel_dim_editor(self, *args, **kwargs) -> None:
        self._hud_service._show_sel_dim_editor(*args, **kwargs)

    def _apply_sel_dim_editor(self) -> None:
        self._hud_service._apply_sel_dim_editor()

    def _update_dim_positions(self, cx: float, cy: float) -> None:
        self._hud_service._update_dim_positions(cx, cy)

    def _update_dim_values(self, distance: float, angle: float) -> None:
        self._hud_service._update_dim_values(distance, angle)

    def _typed_draw_angle(self) -> float | None:
        return self._hud_service._typed_draw_angle()

    def _typed_draw_distance(self) -> float | None:
        return self._hud_service._typed_draw_distance()

    def _apply_dim_input(self) -> None:
        self._hud_service._apply_dim_input()

    def _show_measure_edit(self) -> None:
        self._hud_service._show_measure_edit()

    def _dismiss_measure_edit(self) -> None:
        self._hud_service._dismiss_measure_edit()

    def add_text_at(self, *args, **kwargs) -> int:
        return self._text_service.add_text_at(*args, **kwargs)

    def text_params_at(self, index: int) -> dict[str, Any] | None:
        return self._text_service.text_params_at(index)

    def rebuild_text(self, index: int, values: dict[str, Any]) -> bool:
        return self._text_service.rebuild_text(index, values)

    def attach_text_to_path(self, *args, **kwargs) -> bool:
        return self._text_service.attach_text_to_path(*args, **kwargs)

    def prompt_edit_text(self, index: int) -> None:
        self._text_service.prompt_edit_text(index)

    def prompt_add_text(self, wx: float, wy: float) -> None:
        self._text_service.prompt_add_text(wx, wy)

    def _build_draw_sidebar(self) -> None:
        self._draw_ops._build_draw_sidebar()

    def set_draw_sidebar_width(self, width: int) -> None:
        self._draw_ops.set_draw_sidebar_width(width)

    def set_draw_sidebar_height(self, height: int | None) -> None:
        self._draw_ops.set_draw_sidebar_height(height)

    def set_draw_sidebar_sections(self, sections: list[str]) -> None:
        self._draw_ops.set_draw_sidebar_sections(sections)

    def set_draw_sidebar_path_tools(self, tools: list[str]) -> None:
        self._draw_ops.set_draw_sidebar_path_tools(tools)

    def set_draw_sidebar_shape_tools(self, tools: list[str]) -> None:
        self._draw_ops.set_draw_sidebar_shape_tools(tools)

    def set_draw_sidebar_always_visible(self, enabled: bool) -> None:
        self._draw_ops.set_draw_sidebar_always_visible(enabled)

    def _layout_draw_sidebar(self) -> None:
        self._draw_ops._layout_draw_sidebar()

    def _set_draw_sidebar_visible(self, visible: bool, *, animate: bool = True) -> None:
        self._draw_ops._set_draw_sidebar_visible(visible, animate=animate)

    def _refresh_draw_sidebar_state(self) -> None:
        self._draw_ops._refresh_draw_sidebar_state()

    def _commit_shape_preview(self) -> bool:
        return self._draw_ops._commit_shape_preview()

    def _cancel_draw_points(self) -> None:
        self._draw_ops._cancel_draw_points()

    def _set_draw_primitive(self, tool: str) -> None:
        self._draw_ops._set_draw_primitive(tool)

    def _solve_geometric_constraints(self) -> int:
        return self._construction_service._solve_geometric_constraints()

    def add_geometric_constraint(self, kind: str) -> int:
        return self._construction_service.add_geometric_constraint(kind)

    def remove_constraints_for_selection(self) -> int:
        return self._construction_service.remove_constraints_for_selection()

    def construction_line_from_selection(self, *, ray: bool = False) -> int:
        return self._construction_service.construction_line_from_selection(ray=ray)

    def create_angle_bisector(self) -> int:
        return self._construction_service.create_angle_bisector()

    def create_centerline(self) -> int:
        return self._construction_service.create_centerline()

    def create_circle_through_three_points(self) -> int:
        return self._construction_service.create_circle_through_three_points()

    def create_tangents_from_point(self) -> int:
        return self._construction_service.create_tangents_from_point()

    def create_common_circle_tangents(self) -> int:
        return self._construction_service.create_common_circle_tangents()

    def _append_draw_polyline(
        self,
        poly: list[tuple[float, float]],
        *,
        enter_edit: bool = False,
        kind: str = "polyline",
        meta: dict[str, Any] | None = None,
    ) -> None:
        self._construction_service._append_draw_polyline(
            poly, enter_edit=enter_edit, kind=kind, meta=meta
        )

    def _transform_entity_meta(self, *args, **kwargs) -> None:
        self._selection_service._transform_entity_meta(*args, **kwargs)

    @staticmethod
    def _translated_entity_meta(
        kind: str, meta: dict[str, Any] | None, dx: float, dy: float
    ) -> dict[str, Any] | None:
        return SelectionService._translated_entity_meta(kind, meta, dx, dy)

    def _key_delete(self) -> None:
        self._selection_service._key_delete()

    def _key_backspace(self) -> None:
        self._selection_service._key_backspace()

    def _linked_vertices(self, poly_idx: int, vert_idx: int) -> set[tuple[int, int]]:
        return self._selection_service._linked_vertices(poly_idx, vert_idx)

    def _apply_edit_vertex_position(self, wx: float, wy: float) -> None:
        self._selection_service._apply_edit_vertex_position(wx, wy)

    def _bezier_handles(self, entity_index: int):
        return self._selection_service._bezier_handles(entity_index)

    def _find_bezier_handle(self, cx: float, cy: float):
        return self._selection_service._find_bezier_handle(cx, cy)

    def _set_bezier_handle(self, *args, **kwargs) -> bool:
        return self._selection_service._set_bezier_handle(*args, **kwargs)

    def set_bezier_node_type(self, *args, **kwargs) -> bool:
        return self._selection_service.set_bezier_node_type(*args, **kwargs)

    def _select_edit_vertices_in_rect(self, *args, **kwargs) -> None:
        self._selection_service._select_edit_vertices_in_rect(*args, **kwargs)

    def _shape_primitive_active(self) -> bool:
        return self._selection_service._shape_primitive_active()

    def _is_near_start(self) -> bool:
        return self._selection_service._is_near_start()

    def _finish_draw(self, *, close: bool = False) -> None:
        self._selection_service._finish_draw(close=close)

    def _commit_drawn_polyline(self, *args, **kwargs) -> None:
        self._selection_service._commit_drawn_polyline(*args, **kwargs)

    def _finish_pen(self) -> bool:
        return self._selection_service._finish_pen()

    def _close_selected_polylines(self, *, record_undo: bool = True) -> int:
        return self._selection_service._close_selected_polylines(record_undo=record_undo)

    def close_selection_as_path(self) -> None:
        self._selection_service.close_selection_as_path()

    def _open_selected_polylines(self) -> int:
        return self._selection_service._open_selected_polylines()

    def _toggle_selected_construction(self) -> None:
        self._selection_service._toggle_selected_construction()

    def _prompt_offset_selected(self) -> None:
        self._selection_service._prompt_offset_selected()

    def _chrome_left(self) -> int:
        return self._renderer._chrome_left()

    def _chrome_top(self) -> int:
        return self._renderer._chrome_top()

    def set_smoothing_method(self, method: str) -> None:
        self._smoothing_service.set_method(method)

    def _on_smoothing_method_changed(self, method: str) -> None:
        self._smoothing_service.method_changed(method)

    def set_smooth_iterations(self, iterations: int) -> None:
        self._smoothing_service.set_iterations(iterations)

    def _on_smooth_iterations_changed(self, iterations: int) -> None:
        self._smoothing_service.iterations_changed(iterations)

    def set_simplify_tolerance(self, tolerance: float) -> None:
        self._smoothing_service.set_tolerance(tolerance)

    def _on_simplify_tolerance_changed(self, tolerance: float) -> None:
        self._smoothing_service.tolerance_changed(tolerance)

    def smooth_selected(self, iterations: int = 2) -> int:
        return self._smoothing_service.smooth_selected(iterations)

    def simplify_selected(self, tolerance: float = 0.2) -> int:
        return self._smoothing_service.simplify_selected(tolerance)

    def fit_selected_to_curve(self, tolerance: float = 0.3, corner_angle_deg: float = 55.0) -> int:
        return self._smoothing_service.fit_selected_to_curve(tolerance, corner_angle_deg)

    def _is_locked(self, idx: int) -> bool:
        return 0 <= idx < len(self._entities) and self._entities[idx].locked

    @staticmethod
    def _clone_polys(
        polys: list[list[tuple[float, float]]],
    ) -> list[list[tuple[float, float]]]:
        return [list(poly) for poly in polys]

    def __init__(
        self,
        parent: QWidget | None = None,
        selectable: bool = True,
        on_change=None,
        on_mode_change=None,
        on_poly_change=None,
    ):
        super().__init__(parent)
        self._model = CanvasModel(parent=self)
        self._canvas_service = CanvasService(self._model)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._selectable = selectable
        self._empty_message = "No polylines loaded"
        self._show_selection_bbox: bool = False
        self._selection_follows_geometry: bool = False
        self._on_poly_change = on_poly_change
        if on_change:
            self.selectionChanged.connect(on_change)
        if on_mode_change:
            self.modeChanged.connect(on_mode_change)

        # Layer model. ``_active_layer is None`` = single-layer mode: every
        # entity is interactive and ``EntityRecord.layer`` is ignored.
        # Multi-layer pages (Draft) install an ordered layer list + active
        # layer; entities on non-active layers render dimmed and are not
        # selectable/editable until their layer is activated.
        # Optional per-layer color (hex string), shown in the layer tree
        # swatch and tinting that layer's canvas outlines.

        # Lazily-built Shape objects for the snap engine (invalidated on
        # any structural/geometry change).
        self._snap_shapes_cache: list | None = None

        # construction/hidden/locked/group flags live on EntityRecord.
        self._accent_polys: dict[int, str] = {}  # index → color hex for role overlays
        self._draw_construction_mode: bool = False
        self._draw_split_enabled: bool = True

        # Ghost polylines: a non-interactive secondary set rendered beneath the
        # main polys (faded, dashed). Used for showing context layers — e.g.
        # the source outline beneath a generated pattern preview — without
        # putting them into the editable poly list.
        self._ghost_polys: list[list[tuple[float, float]]] = []
        self._ghost_visible: bool = True

        self._scale = 1.0
        self._ox = 0.0
        self._oy = 0.0

        # LMB interaction state
        self._lmb_press: QPointF | None = None
        self._lmb_prev: QPointF | None = None
        self._lmb_target: int | None = None

        # MMB pan state
        self._mmb_prev: QPointF | None = None
        self._space_pan_active: bool = False
        self._space_pan_dragging: bool = False

        # Cursor world position
        self._cursor_wx: float | None = None
        self._cursor_wy: float | None = None

        # Rubber-band select
        self._shift_drag: bool = False
        self._band_start: QPointF | None = None
        self._band_additive: bool = False
        self._lasso_select_enabled: bool = False
        self._lasso_active: bool = False
        self._lasso_points: list[QPointF] = []
        self._lasso_additive: bool = False
        self._context_menu_sections: set[str] = set(DEFAULT_CONTEXT_MENU_SECTIONS)
        # Named reusable geometry snippets. Definitions live in view state so
        # a workspace carries its own small symbol library without introducing
        # a second document format or global asset database.
        self._symbol_library: dict[str, list[dict[str, Any]]] = {}

        # Knife tool: a transient screen gesture whose world-space line is
        # fed through the same robust splitter used by draw-time splitting.
        self._knife_start_w: tuple[float, float] | None = None
        self._knife_end_w: tuple[float, float] | None = None
        self._last_split_result_indices: set[int] = set()
        self._last_operation_result = OperationResult.unchanged("")
        self._last_repeat_action: tuple[str, Any] | None = None
        self._operation_preview_polys: list[list[tuple[float, float]]] = []

        # Undo / redo history (delta-based; see src/backend/model/editor_history.py)

        # Unified snap engine (src/ui/canvas/snap.py) and guide lines
        # (("h", y_world) or ("v", x_world)); guides participate in snapping.
        self._snap_engine = SnapEngine(self)
        self._guides: list[tuple[str, float]] = []
        self._guide_drag: int | None = None
        self._guide_drag_moved: bool = False
        self._selected_guide: int | None = None
        # mm rulers along the top/left edges; drag out of a ruler to create
        # a guide, drop a guide back onto a ruler to delete it.
        self._rulers_visible: bool = False
        # Display-only unit — all internal storage/geometry stays mm.
        self._unit_system: str = DEFAULT_UNIT_SYSTEM
        # Algorithm smooth_selected() runs: "chaikin" | "gaussian" | "catmull_rom".
        self._smoothing_method: str = DEFAULT_SMOOTHING_METHOD
        # Seed values for the Smooth/Simplify HUD prompts — remembers the
        # last value typed so the user doesn't retype it every time.
        self._smooth_iterations: int = DEFAULT_SMOOTH_ITERATIONS
        self._simplify_tolerance: float = DEFAULT_SIMPLIFY_TOLERANCE
        self._smoothing_service = SmoothingService(cast(Any, self))
        self._grouping_service = GroupingService(cast(Any, self))
        self._layer_service = LayerService(self)
        self._clipboard_service = ClipboardService(self)
        self._hit_test = HitTestService(self)
        self._snap_service = SnapService(self)
        self._gizmo_service = GizmoService(self)
        self._hud_service = HudTextService(self)
        self._text_service = TextService(self)
        self._draw_ops = DrawOpsService(self)
        self._construction_service = ConstructionService(self)
        self._selection_service = SelectionService(self)
        self._renderer = CanvasRenderer(self)
        self._editing = EditingService(self)

        # Fit scale for zoom-% display
        self._fit_scale: float = 1.0
        self._view_back: list[tuple[float, float, float]] = []
        self._view_forward: list[tuple[float, float, float]] = []
        self._last_view_record_time = 0.0
        self._restoring_view = False

        # Measure tool
        self._measure_mode: bool = False
        self._measure_anchor: tuple[float, float] | None = None
        self._measure_hover: tuple[float, float] | None = None
        self._measure_locked: bool = False
        self._measure_end: tuple[float, float] | None = None
        self._measure_snapped_a: bool = False
        self._measure_snapped_b: bool = False
        self._measure_edit: QLineEdit | None = None

        # Persistent dimension/annotation tool — reference-only overlay, like
        # ruler guides: view-state (saved/loaded with the workspace) but not
        # undo-tracked and never written to DXF, so a dimension line can
        # never accidentally get cut/engraved.
        self._dimension_mode: bool = False
        self._dimensions: list[dict] = []
        self._dim_pending_p1: tuple[float, float] | None = None
        self._dim_pending_p2: tuple[float, float] | None = None
        self._dim_pending_offset: float = 5.0
        self._selected_dimension: int | None = None
        self._dimension_drag: int | None = None

        # Mode: "select" | "draw" | "edit"
        self._mode: str = "select"

        # Interaction tools (src/ui/canvas/interaction/tools.py): per-mode strategy
        # objects dispatched by the mouse event handlers. All interaction
        # state stays on the view; tools are stateless.
        trim_tool = canvas_tools.TrimExtendTool(self)
        self._tools: dict[str, canvas_tools.CanvasTool] = {
            "select": canvas_tools.SelectTool(self),
            "draw": canvas_tools.DrawTool(self),
            "edit": canvas_tools.EditTool(self),
            "trim": trim_tool,
            "extend": trim_tool,
            "knife": canvas_tools.KnifeTool(self),
        }
        self._measure_tool = canvas_tools.MeasureTool(self)
        self._dimension_tool = canvas_tools.DimensionTool(self)

        # Draw mode state
        self._draw_pts: list[tuple[float, float]] = []
        self._draw_point_snap_types: list[str | None] = []
        self._draw_primitive: str = (
            "polyline"  # polyline|line|arc|rectangle|circle|ellipse|polygon|slot
        )
        self._draw_shape_preview_active: bool = False
        self._draw_shape_anchor_w: tuple[float, float] | None = None
        self._draw_shape_cursor_w: tuple[float, float] | None = None
        # Side count used the next time a polygon is drawn; adjustable via
        # a HUD prompt when the Shapes picker lands on "polygon".
        self._draw_polygon_sides: int = 6
        self._draw_star_points: int = 5
        self._draw_arc_pts: list[tuple[float, float]] = []
        self._draw_arc_mode: str = "3point"
        self._draw_constraint_lock: str | None = None

        # Pen tool (bezier curves): plain click = corner anchor; click-drag
        # = smooth anchor with a symmetric tangent handle sized by the drag.
        self._pen_pts: list[tuple[float, float]] = []
        self._pen_tangents: list[tuple[float, float]] = []
        self._pen_dragging: bool = False
        self._pen_press_screen: tuple[float, float] | None = None

        # Edit mode state
        self._edit_poly: int | None = None
        self._edit_vert: int | None = None
        self._edit_dragging: bool = False
        self._edit_linked_verts: set[tuple[int, int]] = set()
        self._edit_selected_verts: set[tuple[int, int]] = set()
        self._edit_drag_targets: set[tuple[int, int]] = set()
        self._edit_drag_anchor: tuple[float, float] | None = None
        self._edit_drag_moved: bool = False
        self._edit_undo_pushed: bool = False
        self._edit_command_snapshot: DocumentSnapshot | None = None
        self._hover_vert: tuple[int, int] | None = None
        self._hover_bezier_handle: tuple[int, int, str] | None = None
        self._bezier_handle_drag: tuple[int, int, str] | None = None
        self._bezier_handle_drag_moved: bool = False
        self._bezier_handle_undo_pushed: bool = False
        self._bezier_command_snapshot: DocumentSnapshot | None = None
        # Select-mode hover pre-highlight: which polyline a click would pick
        self._hover_poly: int | None = None
        # Last displayed cursor position (rounded), to skip redundant repaints
        self._prev_cursor_display: tuple[float, float] | None = None

        # Move state (select mode drag-to-move). Object snapping works on
        # absolute deltas from the drag anchor: the selection's own vertices
        # (sampled at drag start) snap against static vertices/edges/grid/
        # guides regardless of where the user grabbed the shape.
        self._move_dragging: bool = False
        self._move_origin: tuple[float, float] | None = None
        self._move_undo_pushed: bool = False
        self._move_command_snapshot: DocumentSnapshot | None = None
        self._move_anchor_w: tuple[float, float] | None = None
        self._move_applied_w: tuple[float, float] = (0.0, 0.0)
        self._move_start_pts: list[tuple[float, float]] = []
        self._move_snap_exclude_vertices: set[tuple[int, int]] = set()
        self._move_snap_exclude_segments: set[tuple[int, int]] = set()

        # Clipboard is process-wide (see _clipboard property below) — do not
        # reset it here, or opening a new window/tab mid-session would wipe
        # whatever the user just copied elsewhere.

        # Image bounds reference rectangle
        self._img_bounds: tuple[float, float] | None = None

        # Background image overlay
        self._bg_pil: PILImage.Image | None = None
        self._bg_w_mm: float = 0.0
        self._bg_h_mm: float = 0.0
        self._bg_pixmap: QPixmap | None = None
        self._bg_cached_scale: float = 0.0

        # Measure / Dimension button rects
        self._mbtn_rect: tuple[float, float, float, float] = (0, 0, 0, 0)
        self._dbtn_rect: tuple[float, float, float, float] = (0, 0, 0, 0)

        # Draw mode snap (world-space snap point under cursor)
        self._draw_snap: tuple[float, float] | None = None
        self._draw_snap_type: str | None = None
        # Cross-mode hover snap indicator (select/edit/move/measure)
        self._hover_snap: tuple[float, float] | None = None
        self._hover_snap_type: str | None = None
        # Independent X/Y axis snap indicators for whole-shape drag (up to
        # two entries — lets one axis align to a different feature than the
        # other, e.g. left edge to shape A while top edge aligns to shape B).
        # Each entry is (target_point, kind, dragged_point) so the renderer
        # can draw a dashed guide line connecting the two — without it, a
        # match that's only aligned on one axis can appear at a point that's
        # visually far from the shape, looking like a snapping glitch.
        self._hover_snap_multi: list[tuple[tuple[float, float], str, tuple[float, float]]] = []
        # Measure pre-anchor hover snap point
        self._measure_hover_pre: tuple[float, float] | None = None

        # Precision aids
        self._grid_visible: bool = False
        self._grid_snap: bool = False
        self._grid_spacing: float = 5.0
        self._geometry_health_visible: bool = False
        self._curvature_visible: bool = False
        self._constraints = []

        # Independent snap-category toggles (all default on, matching prior
        # unconditional behavior). Master is a hard kill-switch for every
        # snap source; vertex/edge gate the two SnapEngine candidate
        # families; angle gates the Shift-held 45-degree snap.
        self._snap_master_enabled: bool = True
        self._snap_vertex_enabled: bool = True
        self._snap_edge_enabled: bool = True
        self._snap_tangent_enabled: bool = True
        self._snap_extension_enabled: bool = True
        self._snap_angle_enabled: bool = True

        # Construction / reference lines: list of ("h", y_world) or ("v", x_world)

        # Auto-dimension HUD inputs (Fusion 360 style)
        self._dim_distance_edit: QLineEdit | None = None
        self._dim_angle_edit: QLineEdit | None = None
        self._dim_distance_dirty: bool = False
        self._dim_angle_dirty: bool = False

        # Selection dimension badge hit rects (for inline editing)
        self._sel_badge_w_rect: QRectF | None = None
        self._sel_badge_h_rect: QRectF | None = None
        # Single-line selection: length / angle badge hit rects
        self._sel_badge_l_rect: QRectF | None = None
        self._sel_badge_a_rect: QRectF | None = None
        # Inline single-dimension editor
        self._sel_dim_edit: QLineEdit | None = None
        self._sel_dim_axis: str | None = None  # "w" or "h"

        # Transform gizmo (select mode)
        self._gizmo_scale_rect: QRectF | None = None
        self._gizmo_rotate_rect: QRectF | None = None
        self._gizmo_move_rect: QRectF | None = None
        # 8-handle selection frame: [(handle name, hit rect), ...] where the
        # name is a compass direction ("nw", "n", …) in world orientation.
        self._gizmo_handle_rects: list[tuple[str, QRectF]] = []
        self._gizmo_anchor_w: tuple[float, float] | None = None
        self._gizmo_handle_w: tuple[float, float] | None = None
        self._gizmo_drag_mode: str | None = None  # "scale" | "rotate"
        self._gizmo_center_w: tuple[float, float] | None = None
        self._gizmo_start_vec: tuple[float, float] | None = None
        self._gizmo_snapshot: dict[int, list[tuple[float, float]]] = {}
        # Parallel snapshot of each entity's meta ("center" etc.) at drag
        # start — needed so scale/rotate can recompute e.g. a circle's true
        # center from its ORIGINAL (pre-drag) value every mouse-move event,
        # instead of compounding the transform onto an already-updated value.
        self._gizmo_meta_snapshot: dict[int, dict[str, Any] | None] = {}
        self._gizmo_local_shape: dict[str, Any] | None = None
        self._gizmo_drag_moved: bool = False
        self._gizmo_undo_pushed: bool = False
        self._gizmo_command_snapshot = None
        # Persistent aspect-ratio lock (properties panel toggle) — unlike
        # the existing Shift-to-constrain gizmo behavior, this stays on
        # across both gizmo drags and typed width/height edits until
        # explicitly turned off.
        self._aspect_ratio_locked: bool = False
        self._property_highlight: str | None = None

        # Auto-constraint detection (H/V)
        self._draw_constraint: str | None = None

        # Flash indicator for transient messages
        self._flash_text: str | None = None
        self._flash_timer: QTimer | None = None

        # Angle snap active flag (for ortho display)
        self._angle_snap_active: bool = False

        # Draw-mode slide-in sidebar
        self._draw_sidebar: DrawSidebar | None = None
        self._draw_sidebar_anim: QPropertyAnimation | None = None
        self._draw_sidebar_visible: bool = False
        self._draw_shape_w_edit: QLineEdit | None = None
        self._draw_shape_h_edit: QLineEdit | None = None
        self._draw_shape_sides_spin: QSpinBox | None = None
        self._draw_sidebar_width: int = DEFAULT_DRAW_SIDEBAR_WIDTH
        self._draw_sidebar_height: int | None = None  # None => auto-fit available space
        self._draw_sidebar_sections: list[str] = list(DEFAULT_DRAW_SIDEBAR_SECTIONS)
        self._draw_sidebar_path_tools: list[str] = list(DEFAULT_DRAW_SIDEBAR_PATH_TOOLS)
        self._draw_sidebar_shape_tools: list[str] = list(DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS)
        self._draw_sidebar_always_visible: bool = DEFAULT_DRAW_SIDEBAR_ALWAYS_VISIBLE

        self._needs_fit = True
        self.setMouseTracking(True)
        self._build_draw_sidebar()

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, polys: list[list[tuple[float, float]]], *, fit: bool = True) -> None:
        self._entities = [EntityRecord(points=list(p), layer=self._active_layer) for p in polys]
        self._sel = set()
        self._group_labels.clear()

        self._sync_shape_storage_from_entities()

        self._needs_fit = fit
        if fit:
            self._fit()
        else:
            self._redraw()
        self._notify()

    def set_accent_polys(self, accent: dict[int, str]) -> None:
        """Override render color for specific poly indices (e.g. cutout shapes).

        Pass an empty dict to clear all accents.
        """
        self._accent_polys = dict(accent)
        self._redraw()

    def set_selection_follows_geometry(self, enabled: bool) -> None:
        """Use path highlighting instead of a rectangular transform frame."""
        self._selection_follows_geometry = bool(enabled)
        self._redraw()

    def _flattened_points(self, idx: int) -> list[tuple[float, float]]:
        """This entity's points, ready to hand to code that only
        understands "a plain polygon" (another page, a fill pattern,
        DXF export): re-tessellates curve/parametric kinds instead of
        handing out their sparse control points or a stale fixed-resolution
        sampling, so a curve used as an outline elsewhere never comes out
        faceted/jagged. ``.points`` itself is left untouched — it stays the
        sparse, editable control-point representation used by Edit mode,
        undo, and workspace save/load.
        """
        if not (0 <= idx < len(self._entities)):
            return []
        return self._geometry_for_entity(idx).tessellate()

    def _geometry_for_entity(self, idx: int) -> CanvasGeometry:
        """Return the geometry behavior object for a document entity."""
        return geometry_for_entity(self._entities[idx])

    def _entity_shows_point_handles(self, idx: int) -> bool:
        return entity_shows_point_handles(self._entities[idx])

    def get_polylines_state(self) -> list[list[tuple[float, float]]]:
        return [self._flattened_points(i) for i in range(len(self._entities))]

    def set_polylines_state(
        self, polys: list[list[tuple[float, float]]], fit: bool = False
    ) -> None:
        self._entities = [EntityRecord(points=list(p), layer=self._active_layer) for p in polys]
        self._sel = set()
        self._group_labels.clear()

        self._sync_shape_storage_from_entities()

        if fit:
            self._needs_fit = True
            self._fit()
        else:
            self._redraw()
        self._notify()

    def add_polylines_state(
        self, polys: list[list[tuple[float, float]]], fit: bool = False
    ) -> None:
        """Append polylines as new entities alongside whatever is already on
        the canvas — unlike ``set_polylines_state``, which replaces
        everything. Used for cross-tab "send selection to Draft", where the
        existing draft content must be preserved. Newly added entities end
        up selected."""
        if not polys:
            return
        entities = [
            EntityRecord(points=list(points), layer=self._active_layer)
            for points in polys
            if len(points) >= 2
        ]
        self._canvas_service.create_entities(entities)
        self._sync_shape_storage_from_entities()

        if fit:
            self._needs_fit = True
            self._fit()
        else:
            self._redraw()
        self._notify()
        self._fire_poly_change()

    def get_view_state(self) -> dict[str, Any]:
        return {
            "scale": self._scale,
            "ox": self._ox,
            "oy": self._oy,
            "fit_scale": self._fit_scale,
            "mode": self._mode,
            "grid_visible": self._grid_visible,
            "grid_snap": self._grid_snap,
            "grid_spacing": self._grid_spacing,
            "guides": [[o, c] for o, c in self._guides],
            "dimensions": [dict(d) for d in self._dimensions],
            "rulers_visible": self._rulers_visible,
            "geometry_health_visible": self._geometry_health_visible,
            "curvature_visible": self._curvature_visible,
            "constraints": [constraint.to_dict() for constraint in self._constraints],
            "layer_colors": dict(self._layer_colors),
            "hidden_indices": sorted(self._flagged("hidden")),
            "locked_indices": sorted(self._flagged("locked")),
            "groups": {str(i): g for i, g in self._group_map().items()},
            "group_labels": {str(k): v for k, v in self._group_labels.items()},
            "symbols": deepcopy(self._symbol_library),
        }

    def set_view_state(self, state: dict[str, Any]) -> None:
        scale_state = state.get("scale", self._scale)
        ox_state = state.get("ox", self._ox)
        oy_state = state.get("oy", self._oy)
        fit_scale_state = state.get("fit_scale", self._fit_scale)
        grid_spacing_state = state.get("grid_spacing", self._grid_spacing)

        def _to_float(value, default: float) -> float:
            if isinstance(value, (int, float, str, bool)):
                try:
                    return float(value)
                except ValueError:
                    return default
            return default

        self._scale = max(_MIN_SCALE, _to_float(scale_state, self._scale))
        self._ox = _to_float(ox_state, self._ox)
        self._oy = _to_float(oy_state, self._oy)
        self._fit_scale = max(_MIN_SCALE, _to_float(fit_scale_state, self._fit_scale))
        mode = str(state.get("mode", self._mode))
        if mode in ("select", "draw", "edit"):
            self.set_mode(mode)
        self._grid_visible = bool(state.get("grid_visible", self._grid_visible))
        self._grid_snap = bool(state.get("grid_snap", self._grid_snap))
        self._grid_spacing = max(0.001, _to_float(grid_spacing_state, self._grid_spacing))
        hidden_state = state.get("hidden_indices", [])
        if not isinstance(hidden_state, list):
            hidden_state = []
        locked_state = state.get("locked_indices", [])
        if not isinstance(locked_state, list):
            locked_state = []
        raw_guides = state.get("guides", [])
        if isinstance(raw_guides, list):
            self._guides = [(str(o), float(c)) for o, c in raw_guides if str(o) in ("h", "v")]
        raw_dimensions = state.get("dimensions", [])
        if isinstance(raw_dimensions, list):
            restored: list[dict] = []
            for d in raw_dimensions:
                if not isinstance(d, dict):
                    continue
                try:
                    p1 = tuple(float(v) for v in d["p1"])
                    p2 = tuple(float(v) for v in d["p2"])
                    offset = float(d["offset"])
                except (KeyError, TypeError, ValueError):
                    continue
                if len(p1) == 2 and len(p2) == 2:
                    restored.append({"p1": p1, "p2": p2, "offset": offset})
            self._dimensions = restored
        if "rulers_visible" in state:
            self._rulers_visible = bool(state.get("rulers_visible"))
        if "geometry_health_visible" in state:
            self._geometry_health_visible = bool(state.get("geometry_health_visible"))
        if "curvature_visible" in state:
            self._curvature_visible = bool(state.get("curvature_visible"))
        raw_constraints = state.get("constraints", [])
        if isinstance(raw_constraints, list):
            self._constraints = [
                parsed
                for item in raw_constraints
                if isinstance(item, dict)
                and (parsed := GeometricConstraint.from_dict(item)) is not None
            ]
        raw_colors = state.get("layer_colors", {})
        if isinstance(raw_colors, dict):
            self._layer_colors = {str(k): str(v) for k, v in raw_colors.items() if v}
        self._set_flagged("hidden", hidden_state)
        self._set_flagged("locked", locked_state)
        raw_groups = state.get("groups", {})
        if isinstance(raw_groups, dict):
            parsed = {
                int(k): int(v)
                for k, v in raw_groups.items()
                if str(k).lstrip("-").isdigit()
                and str(v).lstrip("-").isdigit()
                and 0 <= int(k) < len(self._entities)
            }
            for i, e in enumerate(self._entities):
                e.group = parsed.get(i)
            self._next_group_id = max(parsed.values(), default=0) + 1
        raw_symbols = state.get("symbols", {})
        if isinstance(raw_symbols, dict):
            self._symbol_library = {
                str(name): deepcopy(records)
                for name, records in raw_symbols.items()
                if str(name).strip() and isinstance(records, list)
            }
        raw_labels = state.get("group_labels", {})
        if isinstance(raw_labels, dict):
            self._group_labels = {
                int(k): str(v)
                for k, v in raw_labels.items()
                if str(k).lstrip("-").isdigit() and str(v).strip()
            }
        self._sel -= self._flagged("hidden")
        self._redraw()

    def get_selected(self) -> list[list[tuple[float, float]]]:
        return [self._flattened_points(i) for i in sorted(self._sel) if i < len(self._entities)]

    def _append_entity(
        self,
        poly: list[tuple[float, float]],
        *,
        kind: str = "polyline",
        meta: dict[str, Any] | None = None,
    ) -> int:
        index = self._document.append(
            EntityRecord(
                points=list(poly),
                kind=kind,
                meta=deepcopy(meta) if meta is not None else None,
                layer=self._active_layer,
            )
        )
        self._sync_shape_storage_from_entities()
        return index

    def _reset_edit_interaction_state(self) -> None:
        self._hover_poly = None
        self._edit_poly = None
        self._edit_vert = None
        self._edit_dragging = False
        self._edit_linked_verts = set()
        self._edit_selected_verts = set()
        self._edit_drag_targets = set()
        self._hover_vert = None

    def _cancel_active_drag(self) -> bool:
        """Abort an in-progress move/gizmo/vertex drag (e.g. on Escape),
        restoring pre-drag geometry instead of leaving the shape stuck at
        its half-dragged position. Drag previews capture an immutable document
        snapshot lazily on first movement and restore it here when cancelled.
        Returns True if a drag was cancelled.
        """
        if self._lasso_active:
            self._lasso_active = False
            self._lasso_points.clear()
            self._lasso_additive = False
            self._redraw()
            return True
        if self._knife_start_w is not None:
            self._knife_start_w = None
            self._knife_end_w = None
            self.set_mode("select")
            return True
        if self._shift_drag and self._band_start is not None:
            self._shift_drag = False
            self._band_start = None
            self._band_additive = False
            self._redraw()
            return True
        if self._move_dragging:
            if self._move_undo_pushed:
                self._canvas_service.cancel_preview(self._move_command_snapshot)
            self._move_dragging = False
            self._move_origin = None
            self._move_undo_pushed = False
            self._move_command_snapshot = None
            self._move_anchor_w = None
            self._move_applied_w = (0.0, 0.0)
            self._move_start_pts = []
            self._move_snap_exclude_vertices = set()
            self._move_snap_exclude_segments = set()
            self._lmb_press = None
            self._lmb_prev = None
            self._lmb_target = None
            self._redraw()
            return True
        if self._gizmo_drag_mode is not None:
            if self._gizmo_undo_pushed:
                self._canvas_service.cancel_preview(self._gizmo_command_snapshot)
                self._gizmo_command_snapshot = None
            self._end_gizmo_drag()
            self._redraw()
            return True
        if self._bezier_handle_drag is not None:
            if self._bezier_handle_undo_pushed:
                self._canvas_service.cancel_preview(self._bezier_command_snapshot)
            self._bezier_handle_drag = None
            self._bezier_handle_drag_moved = False
            self._bezier_handle_undo_pushed = False
            self._bezier_command_snapshot = None
            self._redraw()
            return True
        if self._edit_dragging:
            if self._edit_undo_pushed:
                self._canvas_service.cancel_preview(self._edit_command_snapshot)
                self._edit_command_snapshot = None
            self._reset_edit_interaction_state()
            self._redraw()
            return True
        return False

    def _sync_shape_storage_from_entities(self) -> None:
        """Invalidate the lazily-built snap-shape cache."""
        self._snap_shapes_cache = None

    def _snap_shapes(self) -> list:
        """Shape objects for the snap engine, rebuilt from entities on demand."""
        if self._snap_shapes_cache is None:
            ShapeFactory.reset_id_counter(0)
            self._snap_shapes_cache = [shape_for_entity(entity) for entity in self._entities]
        return self._snap_shapes_cache

    def get_selection_indices(self) -> list[int]:
        return self._selected_indices()

    def set_selection(self, indices: list[int]) -> None:
        new_sel = {idx for idx in indices if self._entity_selectable(idx)}
        if new_sel == self._sel:
            return
        self._sel = new_sel
        self._redraw()
        self._notify()

    def set_hidden_indices(self, indices: list[int]) -> None:
        new_hidden = {idx for idx in indices if 0 <= idx < len(self._entities)}
        new_sel = self._sel - new_hidden
        if new_hidden == self._flagged("hidden") and new_sel == self._sel:
            return
        self._set_flagged("hidden", new_hidden)
        self._sel = new_sel
        self._redraw()
        self._notify()

    def set_locked_indices(self, indices: list[int]) -> None:
        new_locked = {idx for idx in indices if 0 <= idx < len(self._entities)}
        if new_locked == self._flagged("locked"):
            return
        self._set_flagged("locked", new_locked)
        self._redraw()

    def set_ghost_polylines(
        self,
        polys: list[list[tuple[float, float]]] | None,
        *,
        visible: bool | None = None,
    ) -> None:
        """Install (or clear) the ghost-overlay polylines.

        Pass ``None`` or an empty list to clear the overlay. ``visible`` may be
        used to control overlay visibility independently of the data; if
        omitted the current visibility flag is preserved.
        """
        new_ghosts: list[list[tuple[float, float]]]
        if not polys:
            new_ghosts = []
        else:
            new_ghosts = [list(p) for p in polys]
        changed = new_ghosts != self._ghost_polys
        self._ghost_polys = new_ghosts
        if visible is not None and bool(visible) != self._ghost_visible:
            self._ghost_visible = bool(visible)
            changed = True
        if changed:
            self._redraw()

    def get_status_summary(self) -> dict[str, object]:
        precision = []
        if self._grid_visible:
            precision.append(f"Grid {self._grid_spacing:g}mm")
        if self._grid_snap:
            precision.append("Snap")
        if self._measure_mode:
            precision.append("Measure")
        if self._draw_construction_mode:
            precision.append("Construction")
        topo = self.get_topology_summary()
        return {
            "mode": self._mode,
            "selected_count": len(self._sel),
            "object_count": len(self._entities),
            "precision": " · ".join(precision) if precision else "Free move",
            "topology": (f"{topo['closed']} closed · {topo['open']} open · {topo['points']} pts"),
        }

    def get_command_guidance(self) -> tuple[str, str]:
        """Persistent next-step guidance for the active canvas command."""
        if self._dimension_mode:
            step = 0 if self._dim_pending_p1 is None else 1
            return (("Pick first point" if step == 0 else "Pick second point"), "accent")
        if self._measure_mode:
            if self._measure_anchor is None:
                return "Measure: pick first point · Esc exits", "accent"
            if not self._measure_locked:
                return "Measure: pick second point · Shift snaps angle", "accent"
            return "Measurement locked · Enter edits scale · Esc exits", "success"
        if self._mode == "draw":
            if not self._draw_pts and not self._draw_shape_preview_active:
                return f"{self._draw_primitive.title()}: pick first point · Esc exits", "accent"
            return (
                f"{self._draw_primitive.title()}: pick next point · Enter finishes · Esc cancels",
                "accent",
            )
        if self._mode == "edit":
            return (
                "Edit vertices: drag points · double-click an edge to insert · Esc exits",
                "accent",
            )
        if self._mode == "trim":
            return "Trim: hover a segment to preview removal · click to apply · Esc exits", "accent"
        if self._mode == "extend":
            return "Extend: hover an open end to preview · click to apply · Esc exits", "accent"
        if self._sel:
            return (
                f"{len(self._sel)} selected · use contextual actions or drag the gizmo",
                "success",
            )
        return "Select geometry · drag empty space for a selection window", "neutral"

    def get_precision_state(self) -> dict[str, object]:
        """Public snapshot consumed by the shared precision bar."""
        return {
            "grid_visible": self._grid_visible,
            "grid_snap": self._grid_snap,
            "grid_spacing": self._grid_spacing,
            "construction_mode": self._draw_construction_mode,
            "measure_mode": self._measure_mode,
            "snap_master": self._snap_master_enabled,
            "snap_vertex": self._snap_vertex_enabled,
            "snap_edge": self._snap_edge_enabled,
            "snap_tangent": self._snap_tangent_enabled,
            "snap_extension": self._snap_extension_enabled,
            "snap_angle": self._snap_angle_enabled,
        }

    def get_topology_summary(self) -> dict[str, int]:
        closed = 0
        logical_points = 0
        for poly in (e.points for e in self._entities):
            if self._is_poly_closed(poly):
                closed += 1
                logical_points += max(0, len(poly) - 1)
            else:
                logical_points += len(poly)
        total = len(self._entities)
        return {
            "total": total,
            "closed": closed,
            "open": max(0, total - closed),
            "points": logical_points,
        }

    def _compact_entities(self, drop: set[int]) -> None:
        """Remove entities at ``drop`` indices.

        Kind/meta/flags live on the EntityRecord, so they travel with the
        surviving entities — no index remapping needed (previously ~45 lines
        of error-prone bookkeeping).
        """
        entity_ids = tuple(
            self._entities[index].id for index in sorted(drop) if 0 <= index < len(self._entities)
        )
        if entity_ids:
            self._canvas_service.delete_entities(entity_ids)

    def delete_indices(self, indices: list[int]) -> int:
        """Delete specific entities regardless of the active layer (used by
        the layer tree); locked entities survive."""
        drop = {i for i in indices if 0 <= i < len(self._entities) and not self._entities[i].locked}
        if not drop:
            return 0
        from src.backend.model.commands import DeleteCommand

        entity_ids = tuple(self._entities[index].id for index in sorted(drop))
        result = self._canvas_service.execute(DeleteCommand(entity_ids=entity_ids))
        if not result.changed:
            return 0
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return len(drop)

    def delete_selected(self) -> int:
        delete_set = {idx for idx in self._sel if not self._is_locked(idx)}
        n = len(delete_set)
        if not n:
            return 0
        from src.backend.model.commands import DeleteCommand

        entity_ids = tuple(self._entities[index].id for index in sorted(delete_set))
        result = self._canvas_service.execute(DeleteCommand(entity_ids=entity_ids))
        if not result.changed:
            return 0
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return n

    def undo(self) -> bool:
        command_result = self._canvas_service.undo()
        if command_result.changed:
            self._reset_edit_interaction_state()
            self._redraw()
            self._notify()
            self._fire_poly_change()
            return True
        return False

    # Temporary named compatibility API; each method delegates to the composed
    # editing adapter and is removed as its caller migrates to DocumentService.

    def _is_poly_closed(self, *args, **kwargs):
        return self._editing._is_poly_closed(*args, **kwargs)

    def _split_geometry_with_line(self, *args, **kwargs):
        return self._editing._split_geometry_with_line(*args, **kwargs)

    def _snap_to_polyline(self, *args, **kwargs):
        return self._editing._snap_to_polyline(*args, **kwargs)

    def _resolve_snap(self, *args, **kwargs):
        return self._editing._resolve_snap(*args, **kwargs)

    def _resolve_drag_snap(self, *args, **kwargs):
        return self._editing._resolve_drag_snap(*args, **kwargs)

    def _angle_snap(self, *args, **kwargs):
        return self._editing._angle_snap(*args, **kwargs)

    def _offset_selected(self, *args, **kwargs):
        return self._editing._offset_selected(*args, **kwargs)

    def _fire_poly_change(self, *args, **kwargs):
        return self._editing._fire_poly_change(*args, **kwargs)

    def _ctx_delete_poly(self, *args, **kwargs):
        return self._editing._ctx_delete_poly(*args, **kwargs)

    def _ctx_deselect(self, *args, **kwargs):
        return self._editing._ctx_deselect(*args, **kwargs)

    def _ctx_select(self, *args, **kwargs):
        return self._editing._ctx_select(*args, **kwargs)

    def _distribute_selected(self, *args, **kwargs):
        return self._editing._distribute_selected(*args, **kwargs)

    def _scale_single_line_extent(self, *args, **kwargs):
        return self._editing._scale_single_line_extent(*args, **kwargs)

    def _set_selected_height(self, *args, **kwargs):
        return self._editing._set_selected_height(*args, **kwargs)

    def _set_selected_width(self, *args, **kwargs):
        return self._editing._set_selected_width(*args, **kwargs)

    def _other_linework(self, *args, **kwargs):
        return self._editing._other_linework(*args, **kwargs)

    def trim_at(self, *args, **kwargs):
        return self._editing.trim_at(*args, **kwargs)

    def preview_trim_at(self, *args, **kwargs):
        return self._editing.preview_trim_at(*args, **kwargs)

    def preview_extend_at(self, *args, **kwargs):
        return self._editing.preview_extend_at(*args, **kwargs)

    def extend_at(self, *args, **kwargs):
        return self._editing.extend_at(*args, **kwargs)

    def boolean_selected(self, *args, **kwargs):
        return self._editing.boolean_selected(*args, **kwargs)

    def selection_geometry(self, *args, **kwargs):
        return self._editing.selection_geometry(*args, **kwargs)

    def move_selection_to(self, *args, **kwargs):
        return self._editing.move_selection_to(*args, **kwargs)

    def set_shape_param(self, *args, **kwargs):
        return self._editing.set_shape_param(*args, **kwargs)

    def align_selected(self, *args, **kwargs):
        return self._editing.align_selected(*args, **kwargs)

    def mirror_selected(self, *args, **kwargs):
        return self._editing.mirror_selected(*args, **kwargs)

    def rotate_selected(self, *args, **kwargs):
        return self._editing.rotate_selected(*args, **kwargs)

    def _scale_all(self, *args, **kwargs):
        return self._editing._scale_all(*args, **kwargs)

    def _apply_shape_size_inputs(self, *args, **kwargs):
        return self._editing._apply_shape_size_inputs(*args, **kwargs)

    def _immediate_segments_for_vertices(self, *args, **kwargs):
        return self._editing._immediate_segments_for_vertices(*args, **kwargs)

    def _offset_polyline(self, *args, **kwargs):
        return self._editing._offset_polyline(*args, **kwargs)

    def _points_equal(self, *args, **kwargs):
        return self._editing._points_equal(*args, **kwargs)

    def _segments_for_polylines(self, *args, **kwargs):
        return self._editing._segments_for_polylines(*args, **kwargs)

    def _update_shape_size_fields_from_preview(self, *args, **kwargs):
        return self._editing._update_shape_size_fields_from_preview(*args, **kwargs)

    def _vertices_for_polylines(self, *args, **kwargs):
        return self._editing._vertices_for_polylines(*args, **kwargs)

    def offset_selected(self, *args, **kwargs):
        return self._editing.offset_selected(*args, **kwargs)

    def _selected_single_line(self, *args, **kwargs):
        return self._editing._selected_single_line(*args, **kwargs)

    def _sel_badge_axes(self, *args, **kwargs):
        return self._editing._sel_badge_axes(*args, **kwargs)

    def _set_selected_line_length(self, *args, **kwargs):
        return self._editing._set_selected_line_length(*args, **kwargs)

    def _set_selected_line_angle(self, *args, **kwargs):
        return self._editing._set_selected_line_angle(*args, **kwargs)

    def _send_selected_to_draft(self, *args, **kwargs):
        return self._editing._send_selected_to_draft(*args, **kwargs)

    def _send_selected_to_pattern(self, *args, **kwargs):
        return self._editing._send_selected_to_pattern(*args, **kwargs)

    def _use_selected_as_custom_tile(self, *args, **kwargs):
        return self._editing._use_selected_as_custom_tile(*args, **kwargs)

    def _show_geometry_preflight(self, *args, **kwargs):
        return self._editing._show_geometry_preflight(*args, **kwargs)

    def recognize_selected_shapes(self, *args, **kwargs):
        return self._editing.recognize_selected_shapes(*args, **kwargs)

    def reverse_selected_paths(self, *args, **kwargs):
        return self._editing.reverse_selected_paths(*args, **kwargs)

    def set_selected_path_start(self, *args, **kwargs):
        return self._editing.set_selected_path_start(*args, **kwargs)

    def resample_selected_paths(self, *args, **kwargs):
        return self._editing.resample_selected_paths(*args, **kwargs)

    def prompt_resample_spacing(self, *args, **kwargs):
        return self._editing.prompt_resample_spacing(*args, **kwargs)

    def prompt_resample_count(self, *args, **kwargs):
        return self._editing.prompt_resample_count(*args, **kwargs)

    def fit_selected_to_primitive(self, *args, **kwargs):
        return self._editing.fit_selected_to_primitive(*args, **kwargs)

    def create_procedural_primitive(self, *args, **kwargs):
        return self._editing.create_procedural_primitive(*args, **kwargs)

    def create_polygon_from_selected_edge(self, *args, **kwargs):
        return self._editing.create_polygon_from_selected_edge(*args, **kwargs)

    def prompt_polygon_from_edge(self, *args, **kwargs):
        return self._editing.prompt_polygon_from_edge(*args, **kwargs)

    def explode_selected_to_segments(self, *args, **kwargs):
        return self._editing.explode_selected_to_segments(*args, **kwargs)

    def merge_selected_segments_to_objects(self, *args, **kwargs):
        return self._editing.merge_selected_segments_to_objects(*args, **kwargs)

    def create_symbol_from_selection(self, *args, **kwargs):
        return self._editing.create_symbol_from_selection(*args, **kwargs)

    def insert_symbol(self, *args, **kwargs):
        return self._editing.insert_symbol(*args, **kwargs)

    def insert_symbol_named(self, *args, **kwargs):
        return self._editing.insert_symbol_named(*args, **kwargs)

    def rename_symbol(self, *args, **kwargs):
        return self._editing.rename_symbol(*args, **kwargs)

    def prompt_rename_symbol(self, *args, **kwargs):
        return self._editing.prompt_rename_symbol(*args, **kwargs)

    def delete_symbol(self, *args, **kwargs):
        return self._editing.delete_symbol(*args, **kwargs)

    def knife_cut(self, *args, **kwargs):
        return self._editing.knife_cut(*args, **kwargs)

    def _apply_operation_result(self, *args, **kwargs):
        return self._editing._apply_operation_result(*args, **kwargs)

    def prompt_morph_selected_paths(self, *args, **kwargs):
        return self._editing.prompt_morph_selected_paths(*args, **kwargs)

    def _preview_morph_selected(self, *args, **kwargs):
        return self._editing._preview_morph_selected(*args, **kwargs)

    def _morph_selected_paths(self, *args, **kwargs):
        return self._editing._morph_selected_paths(*args, **kwargs)

    def _set_repeat_action(self, *args, **kwargs):
        return self._editing._set_repeat_action(*args, **kwargs)

    def _set_operation_preview(self, *args, **kwargs):
        return self._editing._set_operation_preview(*args, **kwargs)

    def _clear_operation_preview(self, *args, **kwargs):
        return self._editing._clear_operation_preview(*args, **kwargs)

    def redo(self) -> bool:
        command_result = self._canvas_service.redo()
        if command_result.changed:
            self._reset_edit_interaction_state()
            self._redraw()
            self._notify()
            self._fire_poly_change()
            return True
        return False

    def select_all(self) -> None:
        self._sel = set(range(len(self._entities))) - self._noninteractive_indices()
        self._redraw()
        self._notify()

    def select_open_paths(self) -> None:
        """Select every interactive path whose endpoints do not close."""
        blocked = self._noninteractive_indices()
        self._sel = {
            index
            for index, entity in enumerate(self._entities)
            if index not in blocked and not self._is_poly_closed(entity.points)
        }
        self._redraw()
        self._notify()

    def select_closed_paths(self) -> None:
        """Select every interactive closed path."""
        blocked = self._noninteractive_indices()
        self._sel = {
            index
            for index, entity in enumerate(self._entities)
            if index not in blocked and self._is_poly_closed(entity.points)
        }
        self._redraw()
        self._notify()

    def select_geometry_category(self, category: str) -> int:
        """Select an interactive semantic category without exposing kind switches to UI code."""
        blocked = self._noninteractive_indices()
        parametric = {
            "arc",
            "circle",
            "ellipse",
            "rectangle",
            "rounded_rectangle",
            "polygon",
            "star",
            "slot",
            "spline",
            "bezier",
        }

        def matches(entity) -> bool:
            if category == "construction":
                return entity.construction
            if category == "text":
                return entity.kind == "text"
            if category == "parametric":
                return entity.kind in parametric
            if category == "generic_paths":
                return entity.kind in {"polyline", "line"}
            return entity.kind == category

        self._sel = {
            index
            for index, entity in enumerate(self._entities)
            if index not in blocked and matches(entity)
        }
        self._redraw()
        self._notify()
        self._show_flash(f"Selected {len(self._sel)} {category.replace('_', ' ')}", 900)
        return len(self._sel)

    def deselect_all(self) -> None:
        self._sel = set()
        self._redraw()
        self._notify()

    def _invert_selection(self) -> None:
        """Invert selection: select all unselected, deselect all selected."""
        all_indices = set(range(len(self._entities))) - self._noninteractive_indices()
        self._sel = all_indices - self._sel
        self._redraw()
        self._notify()

    def toggle_measure(self) -> None:
        self._measure_mode = not self._measure_mode
        self._measure_anchor = None
        self._measure_hover = None
        self._measure_locked = False
        self._measure_end = None
        self._measure_snapped_a = False
        self._measure_snapped_b = False
        self._dismiss_measure_edit()
        self._update_cursor()
        self._redraw()

    def toggle_dimension_mode(self) -> None:
        self._dimension_mode = not self._dimension_mode
        self._dim_pending_p1 = None
        self._dim_pending_p2 = None
        self._update_cursor()
        self._redraw()

    def _dimension_line_points(
        self, dim: dict
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        """World-space endpoints of a dimension's offset line (parallel to
        p1-p2, shifted by ``offset`` mm along the segment's normal)."""
        ax, ay = dim["p1"]
        bx, by = dim["p2"]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return None
        nx, ny = -dy / length, dx / length  # world-space unit normal
        offset = dim["offset"]
        return (ax + nx * offset, ay + ny * offset), (bx + nx * offset, by + ny * offset)

    def _dimension_offset_at(self, dim: dict, wx: float, wy: float) -> float:
        """Signed perpendicular distance (mm) from (wx, wy) to the p1-p2
        line — used to compute a new offset while dragging."""
        ax, ay = dim["p1"]
        bx, by = dim["p2"]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return dim["offset"]
        nx, ny = -dy / length, dx / length
        return (wx - ax) * nx + (wy - ay) * ny

    def _find_dimension_at(self, cx: float, cy: float) -> int | None:
        """Placed-dimension index within grab distance of the cursor
        (screen px) — hit-tests the offset dimension line, not p1/p2."""
        best: int | None = None
        best_d = 6.0
        for i, dim in enumerate(self._dimensions):
            line = self._dimension_line_points(dim)
            if line is None:
                continue
            (lax_w, lay_w), (lbx_w, lby_w) = line
            lax, lay = self._w2c(lax_w, lay_w)
            lbx, lby = self._w2c(lbx_w, lby_w)
            ldx, ldy = lbx - lax, lby - lay
            llen = math.hypot(ldx, ldy)
            if llen < 1e-9:
                continue
            t = max(0.0, min(1.0, ((cx - lax) * ldx + (cy - lay) * ldy) / (llen * llen)))
            px, py = lax + t * ldx, lay + t * ldy
            d = math.hypot(cx - px, cy - py)
            if d < best_d:
                best_d = d
                best = i
        return best

    def _delete_selected_dimension(self) -> None:
        di = self._selected_dimension
        if di is None or not (0 <= di < len(self._dimensions)):
            self._selected_dimension = None
            return
        del self._dimensions[di]
        self._selected_dimension = None
        self._dimension_drag = None
        self._redraw()
        self._notify()

    def set_image_bounds(self, w_mm: float, h_mm: float) -> None:
        self._img_bounds = (w_mm, h_mm)
        self._redraw()

    def set_background_image(self, pil_img: PILImage.Image, w_mm: float, h_mm: float) -> None:
        self._bg_pil = pil_img
        self._bg_w_mm = w_mm
        self._bg_h_mm = h_mm
        self._bg_pixmap = None
        self._bg_cached_scale = 0.0
        self._redraw()

    def clear_background_image(self) -> None:
        self._bg_pil = None
        self._bg_pixmap = None
        self._redraw()

    def set_mode(self, mode: str) -> None:
        if mode == self._mode:
            return
        if self._mode == "draw":
            self._draw_pts.clear()
            self._draw_point_snap_types.clear()
            self._draw_snap = None
            self._draw_snap_type = None
            self._hover_snap = None
            self._hover_snap_type = None
            self._angle_snap_active = False
            self._draw_constraint = None
            self._draw_shape_preview_active = False
            self._draw_shape_anchor_w = None
            self._draw_shape_cursor_w = None
            self._draw_arc_pts.clear()
            self._pen_pts.clear()
            self._pen_tangents.clear()
            self._pen_dragging = False
            self._pen_press_screen = None
            self._dismiss_dim_inputs()
        elif self._mode == "edit":
            self._edit_poly = None
            self._edit_vert = None
            self._edit_dragging = False
            self._edit_linked_verts = set()
            self._edit_selected_verts = set()
            self._edit_drag_targets = set()
            self._hover_vert = None
        self._mode = mode
        if mode in ("draw", "edit"):
            self._measure_mode = False
        self._set_draw_sidebar_visible(mode == "draw")
        self._refresh_draw_sidebar_state()
        self._update_cursor()
        self._redraw()
        self.modeChanged.emit(mode)

    def arm_lasso_selection(self) -> None:
        """Make the next empty-canvas drag a freehand crossing selection."""
        self.set_mode("select")
        self._lasso_select_enabled = True
        self._show_flash("Lasso selection · drag around shapes · Shift adds", 1600)
        self._update_cursor()

    def get_mode(self) -> str:
        return self._mode

    def fit(self) -> None:
        self._record_view_history(force=True)
        self._fit()

    def fit_selection(self) -> bool:
        bounds = self._selection_bounds()
        if bounds is None:
            return False
        self._record_view_history(force=True)
        self._fit_to_bounds(bounds)
        return True

    def _view_transform(self) -> tuple[float, float, float]:
        return (self._scale, self._ox, self._oy)

    def _record_view_history(self, *, force: bool = False) -> None:
        if self._restoring_view:
            return
        now = time.monotonic()
        if not force and now - self._last_view_record_time < 0.3:
            return
        current = self._view_transform()
        if not self._view_back or self._view_back[-1] != current:
            self._view_back.append(current)
            del self._view_back[:-50]
        self._view_forward.clear()
        self._last_view_record_time = now

    def previous_view(self) -> bool:
        if not self._view_back:
            self._show_flash("No previous view", 800)
            return False
        self._view_forward.append(self._view_transform())
        transform = self._view_back.pop()
        self._restoring_view = True
        self._scale, self._ox, self._oy = transform
        self._restoring_view = False
        self._redraw()
        return True

    def next_view(self) -> bool:
        if not self._view_forward:
            self._show_flash("No next view", 800)
            return False
        self._view_back.append(self._view_transform())
        transform = self._view_forward.pop()
        self._restoring_view = True
        self._scale, self._ox, self._oy = transform
        self._restoring_view = False
        self._redraw()
        return True

    def set_empty_message(self, message: str) -> None:
        """Set the hint shown on an empty canvas ("Title\\nhint line")."""
        self._empty_message = message

    def set_grid_visible(self, visible: bool) -> None:
        self._grid_visible = bool(visible)
        self._redraw()

    def set_context_menu_sections(self, sections: list[str]) -> None:
        from src.core.settings import normalize_context_menu_sections

        self._context_menu_sections = set(normalize_context_menu_sections(sections))

    def _context_menu_section_enabled(self, section: str) -> bool:
        return section in self._context_menu_sections

    def set_grid_snap(self, enabled: bool) -> None:
        self._grid_snap = bool(enabled)
        self._refresh_draw_sidebar_state()
        self._redraw()

    def set_grid_spacing(self, spacing: float) -> None:
        self._grid_spacing = max(0.001, float(spacing))
        self._redraw()

    def set_snap_master(self, enabled: bool) -> None:
        self._snap_master_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()
        self._redraw()

    def set_snap_vertex(self, enabled: bool) -> None:
        self._snap_vertex_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()

    def set_snap_edge(self, enabled: bool) -> None:
        self._snap_edge_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()

    def set_snap_tangent(self, enabled: bool) -> None:
        self._snap_tangent_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()

    def set_snap_extension(self, enabled: bool) -> None:
        self._snap_extension_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()

    def set_snap_angle(self, enabled: bool) -> None:
        self._snap_angle_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()

    # ── Inlined from removed mixins (methods actually called from view.py) ──

    def set_construction_mode(self, enabled: bool) -> None:
        self._draw_construction_mode = bool(enabled)
        self._refresh_draw_sidebar_state()
        self._redraw()

    def set_aspect_ratio_locked(self, enabled: bool) -> None:
        """Keep width/height proportional for both the properties panel's
        typed W/H fields and gizmo-handle drags, until turned off again."""
        self._aspect_ratio_locked = bool(enabled)

    def set_property_highlight(self, key: str | None) -> None:
        """Highlight the geometry controlled by a focused inspector field."""
        self._property_highlight = str(key) if key else None
        self._redraw()

    def get_zoom_percent(self) -> int:
        if self._fit_scale < _MIN_SCALE:
            return 100
        return round(self._scale / self._fit_scale * 100)

    def get_cursor_world_pos(self) -> tuple[float, float] | None:
        if self._cursor_wx is not None and self._cursor_wy is not None:
            return (self._cursor_wx, self._cursor_wy)
        return None

    def duplicate_selected(self) -> None:
        self._duplicate_selected()

    def array_duplicate_grid(self) -> None:
        self._array_duplicate_grid()

    def array_duplicate_radial(self) -> None:
        self._array_duplicate_radial()

    def attach_selected_text_to_path(self) -> None:
        """Run the "Attach Text to Path" command against the current
        selection (exactly one text object + one path)."""
        canvas_commands.run(self, "text.attach_to_path")

    def close_selected_polylines(self) -> int:

        return self._close_selected_polylines()

    def open_selected_polylines(self) -> int:
        return self._open_selected_polylines()

    @property
    def poly_count(self) -> int:
        return len(self._entities)

    @property
    def sel_count(self) -> int:
        return len(self._sel)

    # ── Coordinate helpers ────────────────────────────────────────────────────

    def _w2c(self, x: float, y: float) -> tuple[float, float]:
        return x * self._scale + self._ox, -y * self._scale + self._oy

    def _c2w(self, cx: float, cy: float) -> tuple[float, float]:
        scale = self._scale if abs(self._scale) >= _MIN_SCALE else _MIN_SCALE
        return (cx - self._ox) / scale, -(cy - self._oy) / scale

    # ── Internal ──────────────────────────────────────────────────────────────

    def _notify(self) -> None:
        self._model.notify_selection_changed()
        self.selectionChanged.emit(len(self._sel))

    def _update_cursor(self) -> None:
        if self._space_pan_active:
            self.setCursor(
                Qt.CursorShape.ClosedHandCursor
                if self._space_pan_dragging
                else Qt.CursorShape.OpenHandCursor
            )
            return
        if self._mode == "pan":
            self.setCursor(
                Qt.CursorShape.ClosedHandCursor
                if self._lmb_prev is not None
                else Qt.CursorShape.OpenHandCursor
            )
            return
        if self._measure_mode or self._mode in ("draw", "edit", "trim", "extend"):
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif self._mode == "select" and self._hover_vert is not None and self._sel:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()

    def get_entity_records(self) -> list[dict[str, Any]]:
        """Serialize entities (geometry + kind/meta/flags/group) for layer
        storage and sessions. JSON-safe: points become [x, y] lists."""
        out: list[dict[str, Any]] = []
        for e in self._entities:
            out.append(
                {
                    "id": e.id,
                    "points": [[float(x), float(y)] for x, y in e.points],
                    "kind": e.kind,
                    "meta": deepcopy(e.meta) if e.meta is not None else None,
                    "construction": e.construction,
                    "hidden": e.hidden,
                    "locked": e.locked,
                    "group": e.group,
                    "layer": e.layer,
                    "group_label": (
                        self._group_labels.get(e.group) if e.group is not None else None
                    ),
                }
            )
        return out

    def set_entity_records(self, records: list[dict[str, Any]], *, fit: bool = False) -> None:
        """Restore entities from :meth:`get_entity_records` output."""
        ents: list[EntityRecord] = []
        max_gid = -1
        for r in records or []:
            pts = [(float(p[0]), float(p[1])) for p in r.get("points", [])]
            meta = r.get("meta")
            if isinstance(meta, dict):
                meta = _rehydrate_meta(meta)
            gid = r.get("group")
            gid = int(gid) if isinstance(gid, (int, float)) and gid is not None else None
            if gid is not None:
                max_gid = max(max_gid, gid)
            ents.append(
                EntityRecord(
                    points=pts,
                    id=str(r.get("id") or ""),
                    kind=str(r.get("kind", "polyline")),
                    meta=meta,
                    construction=bool(r.get("construction", False)),
                    hidden=bool(r.get("hidden", False)),
                    locked=bool(r.get("locked", False)),
                    group=gid,
                    layer=(str(r["layer"]) if r.get("layer") is not None else None),
                )
            )
        self._entities = ents
        self._group_labels = {}
        for r in records or []:
            g = r.get("group")
            lbl = r.get("group_label")
            if g is not None and lbl:
                self._group_labels[int(g)] = str(lbl)
        self._next_group_id = max(self._next_group_id, max_gid + 1)
        self._sel = set()
        self._sync_shape_storage_from_entities()
        if fit:
            self._needs_fit = True
            self._fit()
        else:
            self._redraw()
        self._notify()

    def get_export_dxf_state(self) -> list[dict[str, Any]]:
        self._sync_shape_storage_from_entities()
        result: list[dict[str, Any]] = []
        for idx, poly in enumerate(e.points for e in self._entities):
            if self._entities[idx].construction:
                continue
            kind = self._entities[idx].kind
            meta = self._entities[idx].meta
            # Only shapes the user actually named (custom label, or a named
            # group) get their own DXF layer on export — auto-generating a
            # "shape_N"/"group_N" name for every ordinary shape used to force
            # each one onto its own layer, silently fragmenting a document
            # the user had deliberately organized onto a single app layer.
            # Un-named shapes simply follow their real `layer` assignment,
            # which _export() already groups correctly.
            gid = self._entities[idx].group
            if gid is not None:
                default_name = self._group_labels.get(gid)
            else:
                default_name = (meta or {}).get("label")
            if meta is None:
                export_meta: dict[str, Any] = {}
            else:
                export_meta = deepcopy(meta)
            if default_name:
                export_meta.setdefault("name", default_name)
            if kind == "line" and len(poly) >= 2:
                export_meta["start"] = tuple(poly[0])
                export_meta["end"] = tuple(poly[-1])
            elif kind == "spline":
                export_meta["control_points"] = [tuple(pt) for pt in poly]
                export_meta.setdefault("degree", 3)
                export_meta.setdefault("closed", self._is_poly_closed(poly))
            elif kind == "bezier" and len(poly) >= 2:
                # No native DXF bezier entity — export the flattened curve
                # as a plain polyline so the file looks right in any viewer.
                tessellated = self._flattened_points(idx)
                if len(tessellated) >= 2:
                    poly = tessellated
                    kind = "polyline"
                    export_meta = {}
                    if default_name:
                        export_meta["name"] = default_name
            result.append(
                {
                    "index": idx,
                    "polyline": list(poly),
                    "kind": kind,
                    "meta": export_meta,
                    "layer": self._entities[idx].layer,
                }
            )
        return result

    # Default work-area shown before anything is drawn — without this, the
    # canvas keeps its raw __init__ scale (1 px/mm) until the first fit,
    # so an empty document's rulers show a meaningless 0-800mm span instead
    # of a plausible small work area.
    _EMPTY_BBOX = (0.0, 0.0, 100.0, 100.0)

    def _bbox(self) -> tuple[float, float, float, float]:
        pts = [
            point
            for index, entity in enumerate(self._entities)
            if entity.kind not in {"xline", "ray"}
            for point in self._flattened_points(index)
        ]
        if self._img_bounds:
            bw, bh = self._img_bounds
            pts.extend([(0.0, 0.0), (bw, bh)])
        if not pts:
            return self._EMPTY_BBOX
        xs, ys = zip(*pts)
        return min(xs), min(ys), max(xs), max(ys)

    @staticmethod
    def _poly_bounds(
        poly: list[tuple[float, float]],
    ) -> tuple[float, float, float, float]:
        if not poly:
            return 0.0, 0.0, 0.0, 0.0
        xs, ys = zip(*poly)
        return min(xs), min(ys), max(xs), max(ys)

    @staticmethod
    def _poly_rect_for_culling(
        poly: list[tuple[float, float]],
        *,
        epsilon: float = 1e-6,
    ) -> QRectF:
        """Return a non-degenerate world rect for robust viewport culling."""
        if not poly:
            return QRectF(QPointF(0.0, 0.0), QPointF(epsilon, epsilon))
        x0, y0, x1, y1 = CanvasView._poly_bounds(poly)
        if abs(x1 - x0) < epsilon:
            x0 -= epsilon
            x1 += epsilon
        if abs(y1 - y0) < epsilon:
            y0 -= epsilon
            y1 += epsilon
        return QRectF(QPointF(x0, y0), QPointF(x1, y1))

    def _selected_indices(self) -> list[int]:
        return [
            idx
            for idx in sorted(self._sel)
            if idx < len(self._entities) and not self._entities[idx].hidden
        ]

    def _mutable_selected_indices(self) -> list[int]:
        return [idx for idx in self._selected_indices() if not self._entities[idx].locked]

    def _selection_bounds(
        self, indices: list[int] | None = None
    ) -> tuple[float, float, float, float] | None:
        items = indices if indices is not None else self._selected_indices()
        pts = [
            point
            for idx in items
            if self._entities[idx].kind not in {"xline", "ray"}
            for point in self._flattened_points(idx)
        ]
        if not pts:
            return None
        xs, ys = zip(*pts)
        return min(xs), min(ys), max(xs), max(ys)

    def _connected_poly_indices(self, start_idx: int) -> set[int]:
        """Return polylines connected to start_idx via shared vertices."""
        if not (0 <= start_idx < len(self._entities)):
            return set()

        def _key(pt: tuple[float, float]) -> tuple[int, int]:
            return (round(pt[0] * 1_000_000), round(pt[1] * 1_000_000))

        graph: dict[int, set[int]] = {i: set() for i in range(len(self._entities))}
        point_to_polys: dict[tuple[int, int], set[int]] = {}
        for pi, poly in enumerate(e.points for e in self._entities):
            seen: set[tuple[int, int]] = set()
            for pt in poly:
                k = _key(pt)
                if k in seen:
                    continue
                seen.add(k)
                point_to_polys.setdefault(k, set()).add(pi)

        for linked in point_to_polys.values():
            if len(linked) < 2:
                continue
            idxs = list(linked)
            for i in range(len(idxs)):
                a = idxs[i]
                for j in range(i + 1, len(idxs)):
                    b = idxs[j]
                    graph[a].add(b)
                    graph[b].add(a)

        visited = {start_idx}
        stack = [start_idx]
        while stack:
            cur = stack.pop()
            for nxt in graph.get(cur, set()):
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append(nxt)
        return visited

    def _fit_to_bounds(self, bounds: tuple[float, float, float, float]) -> None:
        w = max(self.width(), 100)
        h = max(self.height(), 100)
        x0, y0, x1, y1 = bounds
        dw = max(x1 - x0, 1e-6)
        dh = max(y1 - y0, 1e-6)
        self._scale = max(_MIN_SCALE, min(w / dw, h / dh) * 0.8)
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        self._ox = w / 2 - cx * self._scale
        self._oy = h / 2 + cy * self._scale
        self._redraw()

    def _fit(self) -> None:
        self._fit_to_bounds(self._bbox())
        self._fit_scale = self._scale

    def _escape_cb(self) -> None:
        # Hard reset interaction states.
        self._dismiss_hud_prompt()
        self._draw_pts.clear()
        self._draw_point_snap_types.clear()
        self._dismiss_shape_dim_inputs()
        self._draw_constraint = None
        self._draw_snap = None
        self._draw_snap_type = None
        self._hover_snap = None
        self._hover_snap_type = None
        self._hover_snap_multi = []
        self._angle_snap_active = False
        self._draw_shape_preview_active = False
        self._draw_shape_anchor_w = None
        self._draw_shape_cursor_w = None
        self._draw_arc_pts.clear()
        self._pen_pts.clear()
        self._pen_tangents.clear()
        self._pen_dragging = False
        self._pen_press_screen = None
        self._dismiss_dim_inputs()

        self._edit_dragging = False
        self._edit_linked_verts = set()
        self._edit_selected_verts = set()
        self._edit_drag_targets = set()
        self._edit_drag_moved = False
        self._edit_undo_pushed = False
        self._hover_vert = None

        self._measure_mode = False
        self._measure_anchor = None
        self._measure_hover = None
        self._measure_locked = False
        self._measure_end = None
        self._measure_snapped_a = False
        self._measure_snapped_b = False
        self._measure_hover_pre = None
        self._dismiss_measure_edit()

        self._dimension_mode = False
        self._dim_pending_p1 = None
        self._dim_pending_p2 = None

        self._end_gizmo_drag()
        self._gizmo_scale_rect = None
        self._gizmo_rotate_rect = None

        self._mode = "select"
        self._sel = set()
        self._set_draw_sidebar_visible(False)
        self._update_cursor()
        self.modeChanged.emit("select")
        self._notify()
        self._redraw()

    def _zoom_by(self, factor: float) -> None:
        w, h = max(self.width(), 100), max(self.height(), 100)
        self._zoom_at(w / 2, h / 2, factor)

    RULER_PX = 22

    def set_rulers_visible(self, visible: bool) -> None:
        self._rulers_visible = bool(visible)
        self._layout_draw_sidebar()
        self._redraw()

    def set_geometry_health_visible(self, visible: bool) -> None:
        self._geometry_health_visible = bool(visible)
        self._show_flash(
            "Geometry health overlay: ON" if visible else "Geometry health overlay: OFF",
            900,
        )
        self._redraw()

    def set_curvature_visible(self, visible: bool) -> None:
        self._curvature_visible = bool(visible)
        self._show_flash("Curvature view: ON" if visible else "Curvature view: OFF", 900)
        self._redraw()

    def show_coordinate_entry(self, initial: str = "") -> None:
        """Place a draw point or selection using CAD coordinate notation."""
        from src.backend.cad.coordinates import parse_coordinate

        origin = self._draw_pts[-1] if self._mode == "draw" and self._draw_pts else (0.0, 0.0)
        unit_scale = 25.4 if self._unit_system == "in" else 1.0

        def _apply(text: str) -> None:
            point = parse_coordinate(text, origin=origin, scale=unit_scale)
            if self._mode == "draw" and self._draw_primitive in {"line", "polyline"}:
                self._draw_pts.append(point)
                self._draw_point_snap_types.append("coordinate")
                self._cursor_wx, self._cursor_wy = point
                self._show_dim_inputs()
                self._redraw()
                return
            if self._sel:
                if not self.move_selection_to(*point):
                    self._show_flash("Selection is already at that coordinate", 900)
                return
            raise ValueError("Start a line/polyline or select geometry first")

        self._show_text_hud_prompt(
            "Coordinate: x,y · @dx,dy · @distance<angle",
            _apply,
            initial=initial,
        )

    def set_unit_system(self, unit: str) -> None:
        """Set the display unit ("mm" or "in"). Storage/geometry stay mm."""
        if unit not in ("mm", "in"):
            return
        self._unit_system = unit
        self._redraw()

    _MOVE_SNAP_SAMPLE = 64  # max moving vertices considered per drag event

    def _entity_center(self, idx: int) -> tuple[float, float] | None:
        """An entity's true center point, if it has one: the exact
        meta-defined center for parametric shapes (circle/arc/ellipse —
        precise even for coarse tessellation or open arcs), else the
        area-weighted centroid for any other closed polygon. Returns None
        for open polylines/lines, which have no meaningful "center".
        """
        if not (0 <= idx < len(self._entities)):
            return None
        e = self._entities[idx]
        meta = e.meta
        if isinstance(meta, dict):
            center = meta.get("center")
            if isinstance(center, (tuple, list)) and len(center) == 2:
                return (float(center[0]), float(center[1]))
        poly = self._geometry_for_entity(idx).tessellate()
        if len(poly) >= 3 and self._is_poly_closed(poly):
            return _polygon_centroid(poly)
        return None

    def _moving_sample_points(self) -> list[tuple[float, float]]:
        """Sampled vertices of the selection at drag start (for object snap).

        Includes each selected shape's own CENTER (circle/arc/ellipse exact
        center, or polygon centroid) as an extra sample point — otherwise
        only the shape's rim/vertex points are tested against other shapes'
        snap targets, so dragging one circle's center onto another circle's
        center (object-centroid snapping) would never actually trigger.

        Centers are collected SEPARATELY from rim points and appended AFTER
        the rim-point subsampling below, rather than being appended to the
        same list before subsampling — a rim tessellated with >=64 points
        (e.g. the default 64-segment circle) plus one appended center
        already exceeds `_MOVE_SNAP_SAMPLE`, and the naive stride subsample
        `pts[int(i * step)]` for i in range(64) never actually lands on the
        last (center) index, silently dropping it every time. That's why
        circle-to-circle center snapping could appear to simply not work.
        """
        rim_pts: list[tuple[float, float]] = []
        center_pts: list[tuple[float, float]] = []
        for idx in self._sel:
            if 0 <= idx < len(self._entities) and not self._is_locked(idx):
                rim_pts.extend(self._entities[idx].points)
                center = self._entity_center(idx)
                if center is not None:
                    center_pts.append(center)
        if len(rim_pts) > self._MOVE_SNAP_SAMPLE:
            step = len(rim_pts) / self._MOVE_SNAP_SAMPLE
            rim_pts = [rim_pts[int(i * step)] for i in range(self._MOVE_SNAP_SAMPLE)]
        return rim_pts + center_pts

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _redraw(self) -> None:
        self.update()

    def paintEvent(self, event) -> None:
        """Render the canvas, then paint active-tool and chrome overlays."""
        self._renderer.paintEvent(event)
        tool = self._measure_tool if self._measure_mode else self._tools.get(self._mode)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if tool is not None:
            tool.paint_overlay(painter)
        self._renderer._paint_chrome_rulers(painter)
        painter.end()

    def eventFilter(self, obj, event) -> bool:
        """Intercept Tab/Backtab on the draw-mode dim-input QLineEdits."""
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                reverse = key == Qt.Key.Key_Backtab
                if (
                    self._draw_shape_w_edit is not None
                    and self._draw_shape_h_edit is not None
                    and (obj is self._draw_shape_w_edit or obj is self._draw_shape_h_edit)
                ):
                    if (obj is self._draw_shape_w_edit and not reverse) or (
                        obj is self._draw_shape_h_edit and reverse
                    ):
                        self._draw_shape_h_edit.setFocus()
                        self._draw_shape_h_edit.selectAll()
                    else:
                        self._draw_shape_w_edit.setFocus()
                        self._draw_shape_w_edit.selectAll()
                    return True
                if (
                    self._dim_distance_edit is not None
                    and self._dim_angle_edit is not None
                    and (obj is self._dim_distance_edit or obj is self._dim_angle_edit)
                ):
                    # Focus + select only: dirty is driven by textEdited, so the
                    # live value keeps tracking the cursor until the user types.
                    if (obj is self._dim_distance_edit and not reverse) or (
                        obj is self._dim_angle_edit and reverse
                    ):
                        self._dim_angle_edit.setFocus()
                        self._dim_angle_edit.selectAll()
                    elif (obj is self._dim_angle_edit and not reverse) or (
                        obj is self._dim_distance_edit and reverse
                    ):
                        self._dim_distance_edit.setFocus()
                        self._dim_distance_edit.selectAll()
                    return True  # Consume the Tab — prevent Qt focus chain
        return super().eventFilter(obj, event)

    def focusNextPrevChild(self, next: bool) -> bool:
        """Keep Tab/Shift+Tab inside canvas interaction workflows."""
        if self._selectable and self._mode in {"select", "draw", "edit"}:
            return False
        return super().focusNextPrevChild(next)

    def _show_shape_dim_inputs(self) -> None:
        if (
            not self._shape_primitive_active()
            or self._draw_shape_anchor_w is None
            or self._draw_shape_cursor_w is None
            or self._draw_shape_w_edit is not None
            or self._draw_shape_h_edit is not None
        ):
            return
        sx, sy = self._draw_shape_anchor_w
        ex, ey = self._draw_shape_cursor_w
        w = abs(ex - sx)
        h = abs(ey - sy)
        cx, cy = self._w2c((sx + ex) / 2.0, (sy + ey) / 2.0)
        field_x, field_y = self._hud_position_near(
            cx,
            cy,
            86,
            80 if self._draw_primitive in {"polygon", "star"} else 52,
            offset_x=16,
            offset_y=12,
        )

        w_edit = self._make_hud_edit(width=86, height=24, align=Qt.AlignmentFlag.AlignCenter)
        w_edit.setText(f"{w:.2f}")
        w_edit.setProperty("shape_hud_temp", True)
        w_edit.move(field_x, field_y)
        w_edit.returnPressed.connect(self._apply_and_commit_shape_preview)

        h_edit = self._make_hud_edit(width=86, height=24, align=Qt.AlignmentFlag.AlignCenter)
        h_edit.setText(f"{h:.2f}")
        h_edit.setProperty("shape_hud_temp", True)
        h_edit.move(field_x, field_y + 28)
        h_edit.returnPressed.connect(self._apply_and_commit_shape_preview)

        self._draw_shape_w_edit = w_edit
        self._draw_shape_h_edit = h_edit
        self._draw_shape_w_edit.setFocus()
        self._draw_shape_w_edit.selectAll()

        if self._draw_primitive in {"polygon", "star"}:
            count = (
                self._draw_polygon_sides
                if self._draw_primitive == "polygon"
                else self._draw_star_points
            )
            sides_spin = self._make_hud_spinbox(minimum=3, maximum=64, value=count)
            sides_spin.setProperty("shape_hud_temp", True)
            sides_spin.move(field_x, field_y + 56)
            sides_spin.valueChanged.connect(self._on_polygon_sides_spin_changed)
            self._draw_shape_sides_spin = sides_spin

    def _on_polygon_sides_spin_changed(self, value: int) -> None:
        """Live-update the polygon/star ghost's point-count control."""
        if self._draw_primitive == "star":
            self._draw_star_points = value
        else:
            self._draw_polygon_sides = value
        self._redraw()

    def _dismiss_shape_dim_inputs(self) -> None:
        for edit in (
            self._draw_shape_w_edit,
            self._draw_shape_h_edit,
            self._draw_shape_sides_spin,
        ):
            if edit is None:
                continue
            if bool(edit.property("shape_hud_temp")):
                edit.hide()
                edit.deleteLater()
        if self._draw_shape_w_edit is not None and bool(
            self._draw_shape_w_edit.property("shape_hud_temp")
        ):
            self._draw_shape_w_edit = None
        if self._draw_shape_h_edit is not None and bool(
            self._draw_shape_h_edit.property("shape_hud_temp")
        ):
            self._draw_shape_h_edit = None
        if self._draw_shape_sides_spin is not None and bool(
            self._draw_shape_sides_spin.property("shape_hud_temp")
        ):
            self._draw_shape_sides_spin = None

    def _apply_and_commit_shape_preview(self) -> None:
        if not (self._mode == "draw" and self._draw_shape_preview_active):
            return
        self._apply_shape_size_inputs()
        self._dismiss_shape_dim_inputs()
        self._commit_shape_preview()

    def _hit_measure_button(self, cx: float, cy: float) -> bool:
        x1, y1, x2, y2 = self._mbtn_rect
        return x1 <= cx <= x2 and y1 <= cy <= y2

    def _hit_dimension_button(self, cx: float, cy: float) -> bool:
        x1, y1, x2, y2 = self._dbtn_rect
        return x1 <= cx <= x2 and y1 <= cy <= y2

    # ── Events ────────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_draw_sidebar()
        if self._needs_fit:
            if self._entities:
                self._needs_fit = False
            self._fit()
        else:
            self._redraw()

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        mods = event.modifiers()
        shift_mod = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        if key == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pan_active = True
            self._space_pan_dragging = False
            self._update_cursor()
            event.accept()
            return

        if event.text() == "@" and not isinstance(QApplication.focusWidget(), QLineEdit):
            self.show_coordinate_entry("@")
            event.accept()
            return

        # Tool-specific keys (e.g. quick-shape letters) beat the registry.
        _tool = self._tools.get(self._mode)
        if _tool is not None and _tool.key(event):
            event.accept()
            return

        # Arrow key nudge
        if (
            self._selectable
            and self._sel
            and key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down)
        ):
            amount = 1.0 if shift_mod else 0.1
            dx, dy = 0.0, 0.0
            if key == Qt.Key.Key_Left:
                dx = -amount
            elif key == Qt.Key.Key_Right:
                dx = amount
            elif key == Qt.Key.Key_Up:
                dy = amount
            elif key == Qt.Key.Key_Down:
                dy = -amount
            self._nudge_selected(dx, dy)
            return

        if key == Qt.Key.Key_Escape:
            self._dismiss_hud_prompt()
            fw = QApplication.focusWidget()
            if isinstance(fw, QLineEdit) and bool(fw.property("shape_hud_temp")):
                self._dismiss_shape_dim_inputs()
            if blur_focused_line_edit(self, within=self):
                return
            # If a dim field has focus or is dirty, blur and reset it first
            has_dim_focus = (
                self._dim_distance_edit is not None and self._dim_distance_edit.hasFocus()
            ) or (self._dim_angle_edit is not None and self._dim_angle_edit.hasFocus())
            if has_dim_focus or self._dim_distance_dirty or self._dim_angle_dirty:
                self._dim_distance_dirty = False
                self._dim_angle_dirty = False
                self.setFocus()  # return focus to canvas
                return
            # Cancel an in-progress dimension placement and exit dimension
            # mode outright — Escape should back all the way out, not just
            # drop the pending point and leave the tool armed.
            if self._dim_pending_p1 is not None:
                self._dim_pending_p1 = None
                self._dim_pending_p2 = None
                self._dimension_mode = False
                self._update_cursor()
                self._redraw()
                return
            # Cancel a live move/gizmo/vertex drag before it can be
            # mistaken for a plain "clear selection" — otherwise the drag
            # keeps applying to a selection that was just emptied out from
            # under it, freezing the shape at its half-dragged position.
            if self._cancel_active_drag():
                return
            # Idle in dimension/measure mode (no pending point, no drag) —
            # Escape exits the mode rather than doing nothing.
            if self._dimension_mode or self._measure_mode:
                self._dimension_mode = False
                self._measure_mode = False
                self._measure_anchor = None
                self._measure_hover = None
                self._measure_locked = False
                self._measure_end = None
                self._measure_snapped_a = False
                self._measure_snapped_b = False
                self._dismiss_measure_edit()
                self._update_cursor()
                self._redraw()
                return
            # In select mode, Escape clears selection
            if self._mode == "select" and self._sel:
                self.deselect_all()
                return
            self._escape_cb()
            return

        if self._selectable:
            # B. Dimension HUD key interception — digits/period/minus go to distance field
            if self._dim_distance_edit is not None and key in (
                Qt.Key.Key_0,
                Qt.Key.Key_1,
                Qt.Key.Key_2,
                Qt.Key.Key_3,
                Qt.Key.Key_4,
                Qt.Key.Key_5,
                Qt.Key.Key_6,
                Qt.Key.Key_7,
                Qt.Key.Key_8,
                Qt.Key.Key_9,
                Qt.Key.Key_Period,
                Qt.Key.Key_Minus,
            ):
                # Determine which field to target
                target = self._dim_distance_edit
                if self._dim_angle_edit is not None and self._dim_angle_edit.hasFocus():
                    target = self._dim_angle_edit
                    if not self._dim_angle_dirty:
                        target.clear()
                        self._dim_angle_dirty = True
                else:
                    if not self._dim_distance_dirty:
                        target.clear()
                        self._dim_distance_dirty = True
                    target.setFocus()
                # Insert the character
                target.insert(event.text())
                event.accept()
                return
            if key == Qt.Key.Key_Backspace:
                # If a dim field is focused and dirty, let backspace work on the field
                if (
                    self._dim_distance_edit is not None
                    and self._dim_distance_dirty
                    and self._dim_distance_edit.hasFocus()
                ):
                    self._dim_distance_edit.backspace()
                    if not self._dim_distance_edit.text():
                        self._dim_distance_dirty = False
                    event.accept()
                    return
                if (
                    self._dim_angle_edit is not None
                    and self._dim_angle_dirty
                    and self._dim_angle_edit.hasFocus()
                ):
                    self._dim_angle_edit.backspace()
                    if not self._dim_angle_edit.text():
                        self._dim_angle_dirty = False
                        event.accept()
                        return
                self._key_backspace()
                return
            if (
                key == Qt.Key.Key_A
                and self._mode == "draw"
                and self._draw_pts
                and not self._shape_primitive_active()
            ):
                # Quick-focus the angle field so the next segment can be typed.
                if self._dim_angle_edit is None:
                    self._show_dim_inputs()
                if self._dim_angle_edit is not None:
                    self._dim_angle_edit.setFocus()
                    self._dim_angle_edit.selectAll()
                event.accept()
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if (
                    self._mode == "draw"
                    and self._draw_shape_preview_active
                    and self._shape_primitive_active()
                ):
                    if (
                        self._draw_shape_w_edit is not None and self._draw_shape_w_edit.hasFocus()
                    ) or (
                        self._draw_shape_h_edit is not None and self._draw_shape_h_edit.hasFocus()
                    ):
                        self._apply_and_commit_shape_preview()
                    else:
                        self._dismiss_shape_dim_inputs()
                        self._commit_shape_preview()
                    return
                # If dim inputs are dirty, apply them; otherwise finish draw
                if self._dim_distance_dirty or self._dim_angle_dirty:
                    self._apply_dim_input()
                else:
                    self._finish_draw()
                return
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                reverse = key == Qt.Key.Key_Backtab
                if self._mode == "select" and self._sel:
                    # Tab cycles through the available selection badges
                    # (W, H, and for a single line also L and ∠).
                    axes = self._sel_badge_axes()
                    if axes:
                        if self._sel_dim_edit is None:
                            axis, rect = axes[-1] if reverse else axes[0]
                            self._show_sel_dim_editor(axis, rect)
                        else:
                            cur = self._sel_dim_axis
                            self._apply_sel_dim_editor()
                            axes = self._sel_badge_axes()
                            if axes:
                                names = [a for a, _ in axes]
                                pos_i = names.index(cur) if cur in names else -1
                                step = -1 if reverse else 1
                                axis, rect = axes[(pos_i + step) % len(axes)]
                                self._show_sel_dim_editor(axis, rect)
                    event.accept()
                    return
                if (
                    self._mode == "draw"
                    and not self._shape_primitive_active()
                    and self._draw_pts
                    and (self._dim_distance_edit is None or self._dim_angle_edit is None)
                ):
                    self._show_dim_inputs()
                if (
                    self._mode == "draw"
                    and self._shape_primitive_active()
                    and self._draw_shape_preview_active
                ):
                    if self._draw_shape_w_edit is None or self._draw_shape_h_edit is None:
                        self._show_shape_dim_inputs()
                    if self._draw_shape_w_edit is None or self._draw_shape_h_edit is None:
                        event.accept()
                        return
                    if (self._draw_shape_w_edit.hasFocus() and not reverse) or (
                        self._draw_shape_h_edit.hasFocus() and reverse
                    ):
                        self._draw_shape_h_edit.setFocus()
                        self._draw_shape_h_edit.selectAll()
                    else:
                        self._draw_shape_w_edit.setFocus()
                        self._draw_shape_w_edit.selectAll()
                    event.accept()
                    return
                # Tab cycles focus between distance and angle fields
                if self._dim_distance_edit is not None and self._dim_angle_edit is not None:
                    # Focus + select only — dirty is set by textEdited when the
                    # user actually types, so the value keeps live-updating and
                    # the first keystroke replaces it.
                    if (self._dim_distance_edit.hasFocus() and not reverse) or (
                        self._dim_angle_edit.hasFocus() and reverse
                    ):
                        self._dim_angle_edit.setFocus()
                        self._dim_angle_edit.selectAll()
                    elif (self._dim_angle_edit.hasFocus() and not reverse) or (
                        self._dim_distance_edit.hasFocus() and reverse
                    ):
                        self._dim_distance_edit.setFocus()
                        self._dim_distance_edit.selectAll()
                    else:
                        # Neither field has focus — give focus to distance
                        # (Shift+Tab goes straight to angle)
                        if reverse:
                            self._dim_angle_edit.setFocus()
                            self._dim_angle_edit.selectAll()
                        else:
                            self._dim_distance_edit.setFocus()
                            self._dim_distance_edit.selectAll()
                event.accept()
                return
        elif key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            event.accept()
            return

        # Declarative command shortcuts — see src/ui/canvas/interaction/commands.py.
        cmd = canvas_commands.match_key(key, mods)
        if cmd is not None and canvas_commands.can_run(self, cmd):
            cmd.run(self)
            event.accept()
            return

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pan_active = False
            self._space_pan_dragging = False
            self._lmb_prev = None
            self._update_cursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        if delta == 0:
            event.accept()
            return
        # Ctrl+wheel = fine zoom for precision work.
        fine = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        span = 0.02 if fine else 0.1
        factor = max(1.0 - span, min(1.0 + span, 1.0 + delta * 0.0007))
        pos = event.position()
        self._zoom_at(pos.x(), pos.y(), factor)
        event.accept()

    def _zoom_at(self, cx: float, cy: float, factor: float) -> None:
        """Zoom about a canvas point, clamped to sane bounds."""
        self._record_view_history()
        wx, wy = self._c2w(cx, cy)
        self._scale = max(_MIN_SCALE, min(_MAX_SCALE, self._scale * factor))
        self._ox = cx - wx * self._scale
        self._oy = cy + wy * self._scale
        self._redraw()

    def event(self, ev) -> bool:
        # macOS trackpad pinch zoom.
        if (
            ev.type() == QEvent.Type.NativeGesture
            and ev.gestureType() == Qt.NativeGestureType.ZoomNativeGesture
        ):
            pos = ev.position()
            self._zoom_at(pos.x(), pos.y(), 1.0 + float(ev.value()))
            return True
        return super().event(ev)

    def set_zoom_percent(self, percent: float) -> None:
        """Set zoom relative to the fit scale, anchored at the view center."""
        if self._fit_scale < _MIN_SCALE or percent <= 0:
            return
        target = self._fit_scale * percent / 100.0
        cx, cy = self.width() / 2.0, self.height() / 2.0
        self._animate_view_to(target, cx, cy)

    def _animate_view_to(self, target_scale: float, cx: float, cy: float) -> None:
        """Animate to a new scale anchored at a canvas point (instant when
        the widget is not visible, e.g. headless tests)."""
        target_scale = max(_MIN_SCALE, min(_MAX_SCALE, target_scale))
        wx, wy = self._c2w(cx, cy)
        end_ox = cx - wx * target_scale
        end_oy = cy + wy * target_scale
        if not self.isVisible():
            self._scale, self._ox, self._oy = target_scale, end_ox, end_oy
            self._redraw()
            return
        anim = QVariantAnimation(self)
        anim.setDuration(140)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        s0, ox0, oy0 = self._scale, self._ox, self._oy

        def _step(t: float) -> None:
            self._scale = s0 + (target_scale - s0) * t
            self._ox = ox0 + (end_ox - ox0) * t
            self._oy = oy0 + (end_oy - oy0) * t
            self._redraw()

        anim.valueChanged.connect(_step)
        anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

    def mousePressEvent(self, event: QMouseEvent):
        pos = event.position()
        btn = event.button()

        if btn == Qt.MouseButton.MiddleButton:
            self._mmb_prev = pos
            return

        if btn == Qt.MouseButton.LeftButton and self._space_pan_active:
            self._space_pan_dragging = True
            self._lmb_prev = pos
            self._update_cursor()
            return

        if btn == Qt.MouseButton.LeftButton and self._mode == "pan":
            self._lmb_prev = pos
            self._update_cursor()
            return

        if btn == Qt.MouseButton.RightButton:
            if self._selectable:
                self._rightclick_cb(pos.x(), pos.y())
            return

        if btn != Qt.MouseButton.LeftButton:
            return

        # Rulers: press inside a ruler strip drags out a new guide.
        if self._rulers_visible and self._selectable:
            r = self.RULER_PX
            wx0, wy0 = self._c2w(pos.x(), pos.y())
            if pos.x() <= r and pos.y() <= r:
                return  # corner box
            if pos.y() <= r:
                self._guides.append(("h", wy0))
                self._guide_drag = len(self._guides) - 1
                self._selected_guide = self._guide_drag
                self._guide_drag_moved = False
                self._redraw()
                return
            if pos.x() <= r:
                self._guides.append(("v", wx0))
                self._guide_drag = len(self._guides) - 1
                self._selected_guide = self._guide_drag
                self._guide_drag_moved = False
                self._redraw()
                return
        # Grab an existing guide (only when not over a shape) — click selects
        # it (Delete/Backspace removes it); dragging moves it.
        if (
            self._selectable
            and self._mode == "select"
            and self._guides
            and self._find_poly_at(pos.x(), pos.y()) is None
        ):
            gi = self._find_guide_at(pos.x(), pos.y())
            if gi is not None:
                self._guide_drag = gi
                self._selected_guide = gi
                self._guide_drag_moved = False
                self._redraw()
                return
        # Clicking elsewhere clears any selected guide.
        if self._selected_guide is not None:
            self._selected_guide = None
            self._redraw()

        # Grab an existing placed dimension the same way guides work — click
        # selects it (Delete removes it); dragging moves it perpendicular.
        if (
            self._selectable
            and self._mode == "select"
            and self._dimensions
            and self._find_poly_at(pos.x(), pos.y()) is None
        ):
            di = self._find_dimension_at(pos.x(), pos.y())
            if di is not None:
                self._dimension_drag = di
                self._selected_dimension = di
                self._redraw()
                return
        if self._selected_dimension is not None:
            self._selected_dimension = None
            self._redraw()

        # Selection badges / transform gizmo take priority over tools.
        select_tool = cast(canvas_tools.SelectTool, self._tools["select"])
        if self._mode == "select" and self._sel and select_tool.press_overlays(event):
            return

        if self._dimension_mode:
            self._dimension_tool.press(event)
            return

        if self._measure_mode:
            self._measure_tool.press(event)
            return

        tool = self._tools.get(self._mode)
        if tool is not None:
            tool.press(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position()
        wx, wy = self._c2w(pos.x(), pos.y())
        self._cursor_wx = wx
        self._cursor_wy = wy
        self._hover_snap = None
        self._hover_snap_type = None
        self._hover_snap_multi = []

        if self._mmb_prev is not None and event.buttons() & Qt.MouseButton.MiddleButton:
            self._ox += pos.x() - self._mmb_prev.x()
            self._oy += pos.y() - self._mmb_prev.y()
            self._mmb_prev = pos
            self._redraw()
            return

        if (
            (self._space_pan_active or self._mode == "pan")
            and self._lmb_prev is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self._ox += pos.x() - self._lmb_prev.x()
            self._oy += pos.y() - self._lmb_prev.y()
            self._lmb_prev = pos
            self._redraw()
            return

        if self._gizmo_drag_mode is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._apply_gizmo_drag(wx, wy, event.modifiers())
            self._redraw()
            return

        if self._guide_drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            orient, _ = self._guides[self._guide_drag]
            self._guides[self._guide_drag] = (
                orient,
                wy if orient == "h" else wx,
            )
            self._guide_drag_moved = True
            self._redraw()
            return

        if self._dimension_drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            dim = self._dimensions[self._dimension_drag]
            dim["offset"] = self._dimension_offset_at(dim, wx, wy)
            self._redraw()
            return

        if self._dimension_mode:
            self._dimension_tool.move(event)
            return

        if self._measure_mode:
            self._measure_tool.move(event)
            return

        tool = self._tools.get(self._mode)
        if tool is not None:
            tool.move(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        pos = event.position()

        if event.button() == Qt.MouseButton.MiddleButton:
            self._mmb_prev = None
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._space_pan_active or self._mode == "pan":
            self._space_pan_dragging = False
            self._lmb_prev = None
            self._update_cursor()
            return

        if self._gizmo_drag_mode is not None:
            moved = self._end_gizmo_drag()
            self._redraw()
            self._notify()
            if moved:
                self._fire_poly_change()
            return

        if self._guide_drag is not None:
            if self._guide_drag_moved and (pos.x() <= self.RULER_PX or pos.y() <= self.RULER_PX):
                del self._guides[self._guide_drag]
                self._selected_guide = None
            self._guide_drag = None
            self._guide_drag_moved = False
            self._redraw()
            return

        if self._dimension_drag is not None:
            self._dimension_drag = None
            self._redraw()
            self._notify()
            return

        if self._dimension_mode:
            return

        if self._measure_mode:
            return

        if self._mode in ("edit", "select") and self._edit_dragging:
            canvas_tools.release_edit_drag(self)
            return

        tool = self._tools.get(self._mode)
        if tool is not None and tool.release(event):
            return

        # Click select / deselect fall-through (no tool consumed the release).
        if (
            self._selectable
            and self._mode != "select"
            and self._lmb_press is not None
            and self._lmb_target is not None
        ):
            dx = pos.x() - self._lmb_press.x()
            dy = pos.y() - self._lmb_press.y()
            if abs(dx) <= DRAG_THRESH and abs(dy) <= DRAG_THRESH:
                idx = self._lmb_target
                mods = event.modifiers()
                if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                    if idx in self._sel:
                        self._sel = self._sel - {idx}
                    else:
                        self._sel = self._sel | {idx}
                else:
                    self._sel = {idx}
                self._redraw()
                self._notify()
        elif (
            self._mode == "select"
            and self._selectable
            and self._lmb_press is not None
            and self._lmb_target is None
        ):
            dx = pos.x() - self._lmb_press.x()
            dy = pos.y() - self._lmb_press.y()
            if abs(dx) <= DRAG_THRESH and abs(dy) <= DRAG_THRESH and self._sel:
                self.deselect_all()
        self._lmb_press = None
        self._lmb_prev = None
        self._lmb_target = None
        self._shift_drag = False
        self._band_start = None
        self._band_additive = False
        self._move_origin = None
        self._move_undo_pushed = False
        self._move_anchor_w = None
        self._move_applied_w = (0.0, 0.0)
        self._move_start_pts = []
        self._move_snap_exclude_vertices = set()
        self._move_snap_exclude_segments = set()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        tool = self._tools.get(self._mode)
        if tool is not None:
            tool.double_click(event)

    def _rightclick_cb(self, cx: float, cy: float) -> None:
        if self._mode == "draw":
            if self._draw_shape_preview_active and self._shape_primitive_active():
                self._cancel_draw_points()
                self._show_flash("Shape preview canceled", 700)
                return
            # Right-click = finish open polyline (no close), stay in draw mode
            self._finish_draw(close=False)
            return

        if self._mode == "edit":
            hit = self._find_nearest_vertex(cx, cy)
            if hit is not None:
                pi, vi = hit
                menu = QMenu()

                def _prompt_round_corner() -> None:
                    self._show_hud_prompt(
                        "Round radius (mm)",
                        1.0,
                        lambda r: self._round_vertex(pi, vi, r),
                        minimum=0.01,
                    )

                def _prompt_chamfer_corner() -> None:
                    self._show_hud_prompt(
                        "Chamfer distance (mm)",
                        1.0,
                        lambda d: self._chamfer_vertex(pi, vi, d),
                        minimum=0.01,
                    )

                poly = self._entities[pi].points
                is_closed = (
                    len(poly) >= 4
                    and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01
                )
                unique_count = len(poly) - 1 if is_closed else len(poly)
                if unique_count > 3:
                    menu.addAction("Delete vertex", lambda: self._delete_vertex(pi, vi))
                if (is_closed and unique_count >= 3) or (not is_closed and 0 < vi < len(poly) - 1):
                    menu.addAction("Round corner…", _prompt_round_corner)
                    menu.addAction("Chamfer corner…", _prompt_chamfer_corner)
                menu.addAction("Delete polyline", lambda: self._delete_poly(pi))
                menu.popup(self.mapToGlobal(QPointF(cx, cy).toPoint()))
            return

    def _delete_poly(self, pi: int) -> None:
        self._compact_entities({pi})
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _delete_vertex(self, pi: int, vi: int) -> None:
        # Check if shape is currently closed BEFORE deletion
        poly = self._entities[pi].points
        is_closed = (
            len(poly) >= 4 and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01
        )

        entity = deepcopy(self._entities[pi])
        entity.points.pop(vi)
        self._redraw()

        # Re-close shape if it was closed before deletion
        if is_closed and len(entity.points) >= 4:
            entity.points[-1] = entity.points[0]
        self._canvas_service.update_entities([entity])
        self._notify()
        self._fire_poly_change()

    def _chamfer_vertex(self, pi: int, vi: int, dist: float) -> bool:
        if not (0 <= pi < len(self._entities)) or dist <= 0:
            return False
        poly = self._entities[pi].points
        closed = self._is_poly_closed(poly)
        pts = poly[:-1] if closed else list(poly)
        n = len(pts)
        if n < 3:
            return False
        if closed and vi == n:
            vi = 0
        if not (0 <= vi < n):
            return False
        if not closed and (vi == 0 or vi == n - 1):
            return False

        prev_i = (vi - 1) % n
        next_i = (vi + 1) % n
        ax, ay = pts[prev_i]
        bx, by = pts[vi]
        cx, cy = pts[next_i]
        v1 = (ax - bx, ay - by)
        v2 = (cx - bx, cy - by)
        l1 = math.hypot(*v1)
        l2 = math.hypot(*v2)
        if l1 < 1e-9 or l2 < 1e-9:
            return False
        d = min(dist, l1 * 0.45, l2 * 0.45)
        u1 = (v1[0] / l1, v1[1] / l1)
        u2 = (v2[0] / l2, v2[1] / l2)
        p1 = (bx + u1[0] * d, by + u1[1] * d)
        p2 = (bx + u2[0] * d, by + u2[1] * d)

        new_pts = pts[:vi] + [p1, p2] + pts[vi + 1 :]
        if closed:
            new_poly = new_pts + [new_pts[0]]
        else:
            new_poly = new_pts
        entity = deepcopy(self._entities[pi])
        entity.points = new_poly
        # Corner surgery changes topology and can no longer be represented by
        # the source rectangle/polygon's old parametric metadata.
        entity.kind = "polyline"
        entity.meta = None
        self._canvas_service.update_entities([entity])
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _round_vertex(self, pi: int, vi: int, radius: float) -> bool:
        if not (0 <= pi < len(self._entities)) or radius <= 0:
            return False
        poly = self._entities[pi].points
        closed = self._is_poly_closed(poly)
        pts = poly[:-1] if closed else list(poly)
        n = len(pts)
        if n < 3:
            return False
        if closed and vi == n:
            vi = 0
        if not (0 <= vi < n):
            return False
        if not closed and (vi == 0 or vi == n - 1):
            return False

        prev_i = (vi - 1) % n
        next_i = (vi + 1) % n
        ax, ay = pts[prev_i]
        bx, by = pts[vi]
        cx, cy = pts[next_i]
        u1 = (ax - bx, ay - by)
        u2 = (cx - bx, cy - by)
        l1 = math.hypot(*u1)
        l2 = math.hypot(*u2)
        if l1 < 1e-9 or l2 < 1e-9:
            return False
        u1 = (u1[0] / l1, u1[1] / l1)
        u2 = (u2[0] / l2, u2[1] / l2)
        dot = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
        phi = math.acos(dot)
        if phi < 1e-3 or abs(math.pi - phi) < 1e-3:
            return False

        offset = radius / math.tan(phi / 2.0)
        offset = min(offset, l1 * 0.45, l2 * 0.45)
        if offset <= 1e-6:
            return False
        r = offset * math.tan(phi / 2.0)

        t1 = (bx + u1[0] * offset, by + u1[1] * offset)
        t2 = (bx + u2[0] * offset, by + u2[1] * offset)

        bis = (u1[0] + u2[0], u1[1] + u2[1])
        bl = math.hypot(*bis)
        if bl < 1e-9:
            return False
        bis = (bis[0] / bl, bis[1] / bl)
        center_dist = r / math.sin(phi / 2.0)
        center = (bx + bis[0] * center_dist, by + bis[1] * center_dist)

        a1 = math.atan2(t1[1] - center[1], t1[0] - center[0])
        a2 = math.atan2(t2[1] - center[1], t2[0] - center[0])
        # Use the minor arc between tangent points; choosing the major arc
        # produces loop-like rounding artifacts.
        span = a2 - a1
        while span <= -math.pi:
            span += 2 * math.pi
        while span > math.pi:
            span -= 2 * math.pi
        steps = max(4, min(24, int(abs(span) / (math.pi / 18.0))))
        arc_pts = [
            (
                center[0] + r * math.cos(a1 + span * (i / steps)),
                center[1] + r * math.sin(a1 + span * (i / steps)),
            )
            for i in range(steps + 1)
        ]

        new_pts = pts[:vi] + arc_pts + pts[vi + 1 :]
        if closed:
            new_poly = new_pts + [new_pts[0]]
        else:
            new_poly = new_pts
        entity = deepcopy(self._entities[pi])
        entity.points = new_poly
        entity.kind = "polyline"
        entity.meta = None
        self._canvas_service.update_entities([entity])
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True
