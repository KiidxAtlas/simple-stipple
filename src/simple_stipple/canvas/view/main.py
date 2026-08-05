# pyright: reportAttributeAccessIssue=false
"""CanvasView — interactive pan/zoom canvas widget with polyline selection, measure, draw, and edit tools."""

from __future__ import annotations

import math
import time
from copy import deepcopy
from typing import Any, cast

from PIL import Image as PILImage
from PySide6.QtCore import (
    QEvent,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPainter, QWheelEvent
from PySide6.QtWidgets import QWidget

from simple_stipple.canvas import commands as canvas_commands
from simple_stipple.canvas.constants import DRAG_THRESH
from simple_stipple.canvas.constants import MIN_SCALE as _MIN_SCALE
from simple_stipple.canvas.model import CanvasModel
from simple_stipple.canvas.operations.hit_test import HitTestService
from simple_stipple.canvas.tools import tools as canvas_tools
from simple_stipple.canvas.tools.select import SelectionService
from simple_stipple.canvas.view.commands import (
    _cancel_active_drag,
    _cancel_draw_in_progress,
    _escape_cb,
    _find_dimension_at,
    _rightclick_cb,
    _round_vertex,
    _show_shape_dim_inputs,
    exit_to_select,
    get_export_dxf_state,
    set_view_state,
)
from simple_stipple.canvas.view.config import _initialize_view
from simple_stipple.canvas.view.helpers import (
    _animate_view_to,
    _background_edit_hit,
    _chamfer_vertex,
    _connected_entities,
    _dismiss_shape_dim_inputs,
    _entity_center,
    _moving_sample_points,
    _remove_dimensions_for_entities,
    _update_cursor,
    add_polylines_state,
    eventFilter,
    get_command_guidance,
    get_context_actions,
    get_entity_records,
    get_status_summary,
    get_view_state,
    select_geometry_category,
    set_entity_records,
    set_ghost_polylines,
    set_mode,
    show_coordinate_entry,
    toggle_dimension_mode,
    toggle_measure,
    trigger_context_action,
)
from simple_stipple.canvas.view.interactions import (
    _append_dimension,
    _clear_dimensions,
    _commit_annotation_edit,
    _edit_driving_dimension,
    _refresh_driving_dimensions,
    _remove_dimension,
    _remove_guide,
    _set_dimension_precision,
    _set_dimension_precision_value,
    keyPressEvent,
    keyReleaseEvent,
    mouseDoubleClickEvent,
    mouseMoveEvent,
    mousePressEvent,
    mouseReleaseEvent,
)
from simple_stipple.document.model import CanvasDocument, EntityRecord
from simple_stipple.engine.cad.constraints import GeometricConstraint
from simple_stipple.engine.cad.editor_geometry import (
    CanvasGeometry,
    entity_shows_point_handles,
    geometry_for_entity,
    shape_for_entity,
)
from simple_stipple.engine.cad.shapes import ShapeFactory

_MAX_SCALE = 20000.0  # px per mm — deep zoom for tiny features


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
            doc = deepcopy(model.document)
            doc.replace(entities)
            service = self.__dict__.get("_canvas_service")
            if service is None:
                model.replace_document(doc)
            else:
                service.replace_document(doc)

    @property
    def _entities_by_id(self) -> dict[str, EntityRecord]:
        """Cached O(1) entity lookup, rebuilt only when the entity list changes."""
        entities = self._entities
        cache_key = (id(entities), len(entities))
        if cache_key != self.__entities_by_id_key:
            self.__entities_by_id = {entity.id: entity for entity in entities}
            self.__entities_by_id_key = cache_key
        return self.__entities_by_id

    def _entity_for_id(self, entity_id: str) -> EntityRecord | None:
        """Return the entity record for ``entity_id``, or ``None`` if not found."""
        return self._entities_by_id.get(entity_id)

    # Guides and dimensions are document state (see CanvasDocument), so they
    # ride the same undo stack and snapshots as geometry. Exposing them as
    # document-backed properties keeps every existing call site working while
    # making the document the single source of truth — there is no separate
    # view-owned copy to drift out of sync on undo/redo or document replacement.
    @property
    def _guides(self) -> list[tuple[str, float]]:
        return self._document.guides

    @_guides.setter
    def _guides(self, guides: list[tuple[str, float]]) -> None:
        self._document.guides = list(guides)

    @property
    def _dimensions(self) -> list[dict]:
        return self._document.dimensions

    @_dimensions.setter
    def _dimensions(self, dimensions: list[dict]) -> None:
        self._document.dimensions = list(dimensions)

    @property
    def _sel(self) -> set[str]:
        return self._document.selection

    @_sel.setter
    def _sel(self, selection: set[str]) -> None:
        from simple_stipple.document.commands import SelectCommand

        service = self.__dict__.get("_canvas_service")
        if service is None:
            self._document.select_ids(tuple(sorted(selection)))
        else:
            service.execute(SelectCommand(entity_ids=tuple(sorted(selection))), record=False)

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
    backgroundSelectionChanged = Signal(bool)
    document_changed = Signal()
    operation_failed = Signal(str)
    viewChanged = Signal()  # emitted on zoom/pan so status readouts can update live
    cursorPositionChanged = Signal(float, float)

    def _flagged(self, attr: str) -> set[str]:
        """Entity IDs whose boolean ``attr`` is set."""
        return self._document.flagged_ids(attr)

    def _set_flagged(self, attr: str, entity_ids: set[str]) -> None:
        """Set boolean ``attr`` to exactly ``entity_ids`` (wholesale assignment)."""
        self._document.set_flagged_ids(attr, entity_ids)

    def _group_of(self, entity_id: str) -> int | None:
        return self._grouping_service.group_of(entity_id)

    def _group_map(self) -> dict[str, int | None]:
        return self._grouping_service.group_map()

    def _group_selected(self) -> None:
        self._grouping_service.group_selected()

    def set_group_label(self, group_id: int, label: str) -> None:
        self._grouping_service.set_label(group_id, label)

    def _ungroup_selected(self) -> None:
        self._grouping_service.ungroup_selected()

    def group_entities(self, entity_ids: list[str]) -> int:
        return self._grouping_service.group_entities(entity_ids)

    def ungroup_entities(self, entity_ids: list[str]) -> int:
        return self._grouping_service.ungroup_entities(entity_ids)

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

    def move_indices_to_layer(self, entity_ids: list[str], layer: str) -> int:
        return self._layer_service.move_entities(entity_ids, layer)

    def _on_active_layer(self, entity: EntityRecord) -> bool:
        return self._layer_service.on_active(entity)

    def _entity_selectable(self, entity_id: str) -> bool:
        if entity_id in self._render_only_entity_ids:
            return False
        return self._layer_service.selectable(entity_id)

    def _entity_selectable_by_id(self, entity_id: str) -> bool:
        if entity_id in self._render_only_entity_ids:
            return False
        return self._layer_service.selectable(entity_id)

    def _noninteractive_ids(self) -> set[str]:
        return self._layer_service.noninteractive_indices() | self._render_only_entity_ids

    def set_render_only_entity_ids(self, entity_ids: set[str]) -> None:
        """Keep generated strokes visible while excluding them from editing and hit tests."""
        self._render_only_entity_ids = set(entity_ids)
        self._sel.difference_update(self._render_only_entity_ids)
        self._reset_edit_interaction_state()
        self._redraw()

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

    def _find_nearest_vertex(self, cx: float, cy: float) -> tuple[str, int] | None:
        return self._hit_test.nearest_vertex(cx, cy)

    def _find_nearest_vertex_by_id(self, cx: float, cy: float) -> tuple[str, int] | None:
        return self._hit_test.nearest_vertex_by_id(cx, cy)

    def _delete_poly(self, entity_id: str) -> None:
        if entity_id not in self._entities_by_id:
            return
        drop = {entity_id}
        self._compact_entities(drop)
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _delete_vertex(self, entity_id: str, vi: int) -> None:
        entity = self._entities_by_id.get(entity_id)
        if entity is None:
            return
        poly = entity.points
        is_closed = (
            len(poly) >= 4 and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01
        )
        updated = deepcopy(entity)
        updated.points.pop(vi)
        self._redraw()
        if is_closed and len(updated.points) >= 4:
            updated.points[-1] = updated.points[0]
        self._canvas_service.update_entities([updated])
        self._notify()

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
    ) -> tuple[str, int, tuple[float, float]] | None:
        return self._hit_test.nearest_edge(cx, cy)

    def _find_poly_at(self, cx: float, cy: float) -> str | None:
        return self._hit_test.entity_at(cx, cy)

    def _find_polys_at(self, cx: float, cy: float) -> list[str]:
        return self._hit_test.entities_at(cx, cy)

    def _find_profile_at(self, cx: float, cy: float) -> set[str]:
        return self._hit_test.profile_at(cx, cy)

    def _find_guide_at(self, cx: float, cy: float) -> int | None:
        return self._hit_test.guide_at(cx, cy)

    def _find_inactive_poly_at(self, cx: float, cy: float) -> str | None:
        return self._hit_test.inactive_entity_at(cx, cy)

    def _find_ghost_poly_at(self, cx: float, cy: float) -> int | None:
        """Find ghost polyline index at screen coords. Ghost polys don't have entity IDs."""
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

    def _apply_measure_scale(self) -> None:
        self._hud_service._apply_measure_scale()

    def add_text_at(self, *args, **kwargs) -> int:
        return self._text_service.add_text_at(*args, **kwargs)

    def text_params_at(self, entity_id: str) -> dict[str, Any] | None:
        return self._text_service.text_params_at(entity_id)

    def rebuild_text(self, entity_id: str, values: dict[str, Any]) -> bool:
        return self._text_service.rebuild_text(entity_id, values)

    def attach_text_to_path(self, *args, **kwargs) -> bool:
        return self._text_service.attach_text_to_path(*args, **kwargs)

    def prompt_edit_text(self, entity_id: str) -> None:
        self._text_service.prompt_edit_text(entity_id)

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

    def _linked_vertices_by_id(self, entity_id: str, vert_idx: int) -> set[tuple[str, int]]:
        return self._selection_service._linked_vertices_by_id(entity_id, vert_idx)

    def _apply_edit_vertex_position(self, wx: float, wy: float) -> None:
        self._selection_service._apply_edit_vertex_position(wx, wy)

    def _bezier_handles(self, entity_id: str):
        return self._selection_service._bezier_handles(entity_id)

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
        if self._draw_primitive == "bezier":
            self._selection_service._finish_pen(close=close)
            return
        self._selection_service._finish_draw(close=close)

    def _commit_drawn_polyline(self, *args, **kwargs) -> None:
        self._selection_service._commit_drawn_polyline(*args, **kwargs)

    def _finish_pen(self, *, close: bool = False) -> bool:
        return self._selection_service._finish_pen(close=close)

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

    def _is_locked(self, entity_id: str) -> bool:
        return (entity := self._entities_by_id.get(entity_id)) is not None and entity.locked

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
        _initialize_view(self, parent, selectable, on_change, on_mode_change, on_poly_change)

    def load(
        self,
        polys: list[list[tuple[float, float]]],
        *,
        fit: bool = True,
        entity_ids: list[str] | None = None,
    ) -> None:
        ids = entity_ids if entity_ids is not None and len(entity_ids) == len(polys) else None
        self._entities = []
        for index, poly in enumerate(polys):
            entity = EntityRecord(points=list(poly), layer=self._active_layer)
            if ids is not None:
                entity.id = ids[index]
            self._entities.append(entity)
        self._sel = set()
        self._group_labels.clear()

        self._sync_shape_storage_from_entities()

        self._needs_fit = fit
        if fit:
            self._fit()
        else:
            self._redraw()
        self._notify()

    def set_accent_polys(self, accent: dict[str, str]) -> None:
        """Override render color for specific entity IDs (e.g. cutout shapes).

        Pass an empty dict to clear all accents.
        """
        self._accent_polys = dict(accent)
        self._renderer.invalidate_dense_preview_cache()
        self._redraw()

    def set_region_tint(self, tint: dict[str, str]) -> None:
        """Fill these entities translucently — the user is picking an *area*,
        so the feedback has to read as an area, not an outline highlight."""
        self._region_tint = dict(tint)
        self._renderer.invalidate_dense_preview_cache()
        self._redraw()

    def set_region_picking(self, enabled: bool) -> None:
        """Let a click on empty space inside a closed shape select it."""
        self._region_picking = bool(enabled)

    def _find_region_at(self, cx: float, cy: float):
        return self._hit_test.region_at(cx, cy)

    def set_selection_follows_geometry(self, enabled: bool) -> None:
        """Use path highlighting instead of a rectangular transform frame."""
        self._selection_follows_geometry = bool(enabled)
        self._redraw()

    def set_selection_drag_edits(self, enabled: bool) -> None:
        """Allow/disallow geometry edits caused by dragging in Select mode."""
        self._selection_drag_edits = bool(enabled)

    def _flattened_points_by_id(self, entity_id: str) -> list[tuple[float, float]]:
        """Entity ID version of _flattened_points."""
        if entity_id not in self._entities_by_id:
            return []
        return self._geometry_for_entity_by_id(entity_id).tessellate()

    def _geometry_for_entity_by_id(self, entity_id: str) -> CanvasGeometry:
        """Entity ID version of _geometry_for_entity."""
        return geometry_for_entity(self._entities_by_id[entity_id])

    def _entity_shows_point_handles_by_id(self, entity_id: str) -> bool:
        return entity_shows_point_handles(self._entities_by_id[entity_id])

    def get_polylines_state(self) -> list[list[tuple[float, float]]]:
        return [self._flattened_points_by_id(eid) for eid in self._entities_by_id]

    def set_polylines_state(
        self,
        polys: list[list[tuple[float, float]]],
        fit: bool = False,
        entity_ids: list[str] | None = None,
    ) -> None:
        ids = entity_ids if entity_ids is not None and len(entity_ids) == len(polys) else None
        self._entities = []
        for index, poly in enumerate(polys):
            entity = EntityRecord(points=list(poly), layer=self._active_layer)
            if ids is not None:
                entity.id = ids[index]
            self._entities.append(entity)
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
        add_polylines_state(self, polys, fit=False)

    def set_view_state(self, state: dict[str, Any]) -> None:
        return set_view_state(self, state)

    def get_selected(self) -> list[list[tuple[float, float]]]:
        return [self._flattened_points_by_id(eid) for eid in sorted(self._sel)]

    def _append_entity(
        self,
        poly: list[tuple[float, float]],
        *,
        kind: str = "polyline",
        meta: dict[str, Any] | None = None,
    ) -> int:
        entity = EntityRecord(
            points=list(poly),
            kind=kind,
            meta=deepcopy(meta) if meta is not None else None,
            layer=self._active_layer,
        )
        self._canvas_service.append_entity(entity)
        self._sync_shape_storage_from_entities()
        return len(self._entities) - 1

    def _reset_edit_interaction_state(self) -> None:
        self._hover_poly = None
        self._edit_poly = None
        self._edit_vert = None
        self._edit_dragging = False
        self._edit_linked_verts = set()
        self._edit_selected_verts = set()
        self._edit_drag_targets = set()
        self._hover_vert = None

    def _sync_shape_storage_from_entities(self) -> None:
        """Invalidate the lazily-built snap-shape cache."""
        self._snap_shapes_cache = None

    def _snap_shapes(self) -> dict[str, Any]:
        """Shape objects for the snap engine, keyed by entity ID, rebuilt on demand."""
        if self._snap_shapes_cache is None:
            ShapeFactory.reset_id_counter(0)
            self._snap_shapes_cache = {
                entity.id: shape_for_entity(entity) for entity in self._entities
            }
        assert self._snap_shapes_cache is not None
        return self._snap_shapes_cache

    def get_selected_ids(self) -> list[str]:
        return list(self._sel)

    def get_entity_ids(self) -> list[str]:
        """Return entity IDs in the same order as the displayed geometry."""
        return list(self._entities_by_id)

    def set_selection(self, entity_ids: list[str]) -> None:
        new_sel = {eid for eid in entity_ids if self._entity_selectable(eid)}
        if new_sel == self._sel:
            return
        self._sel = new_sel
        self._redraw()
        self._notify()

    def set_hidden_ids(self, entity_ids: list[str]) -> None:
        new_hidden = {eid for eid in entity_ids if eid in self._entities_by_id}
        new_sel = self._sel - new_hidden
        if new_hidden == self._flagged("hidden") and new_sel == self._sel:
            return
        self._set_flagged("hidden", new_hidden)
        self._sel = new_sel
        self._redraw()
        self._notify()

    def set_locked_ids(self, entity_ids: list[str]) -> None:
        new_locked = {eid for eid in entity_ids if eid in self._entities_by_id}
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
        set_ghost_polylines(self, polys, visible=None)

    def set_result_polylines(
        self,
        polys: list[list[tuple[float, float]]] | None,
        *,
        pattern_span: tuple[int, int] = (0, 0),
    ) -> None:
        """Install the solved pattern as a render-only overlay.

        The entity set is untouched, so the canvas keeps holding the real
        outlines and every edit stays an edit of the document. ``pattern_span``
        marks the slice of ``polys`` that is generated pattern cells, which is
        what right-click cell removal acts on.
        """
        self._result_polys = [list(poly) for poly in (polys or [])]
        self._result_pattern_span = pattern_span
        self._renderer.invalidate_result_cache()
        self._redraw()

    def set_issue_markers(self, markers) -> None:
        """Show preflight findings on the part, at their own coordinates.

        Each marker needs ``.point`` and ``.severity`` — the shape
        ``engine.cad.preflight.GeometryIssue`` already has.
        """
        self._issue_markers = tuple(markers or ())
        self._redraw()

    def issue_marker_at(self, cx: float, cy: float):
        """The finding under the cursor, so clicking one can select its path."""
        best, best_distance = None, 12.0
        for marker in self._issue_markers:
            mx, my = self._w2c(*marker.point)
            distance = math.hypot(mx - cx, my - cy)
            if distance < best_distance:
                best, best_distance = marker, distance
        return best

    def set_result_visible(self, visible: bool) -> None:
        if bool(visible) == self._result_visible:
            return
        self._result_visible = bool(visible)
        self._redraw()

    def result_visible(self) -> bool:
        return self._result_visible

    def result_cell_at(self, cx: float, cy: float) -> int | None:
        """Index into ``_result_polys`` of the generated cell under the cursor.

        Only cells inside ``pattern_span`` qualify — outline and fill strokes
        in the result are not removable motifs.
        """
        if not self._result_polys or not self._result_visible:
            return None
        start, end = self._result_pattern_span
        wx, wy = self._c2w(cx, cy)
        best: int | None = None
        best_distance = 10.0
        for index in range(max(0, start), min(end, len(self._result_polys))):
            poly = self._result_polys[index]
            if len(poly) < 2:
                continue
            distance = self._hit_test.closest_point(poly, wx, wy, cx, cy)
            if isinstance(distance, float) and distance < best_distance:
                best_distance, best = distance, index
        return best

    def set_dense_preview_render(self, enabled: bool) -> None:
        """Batch dense preview strokes without changing stored geometry."""
        enabled = bool(enabled)
        if enabled == self._dense_preview_render:
            return
        self._dense_preview_render = enabled
        self._renderer.invalidate_dense_preview_cache()
        self._redraw()

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
            "snap_midpoint": self._snap_midpoint_enabled,
            "snap_intersection": self._snap_intersection_enabled,
            "snap_edge": self._snap_edge_enabled,
            "snap_tangent": self._snap_tangent_enabled,
            "snap_extension": self._snap_extension_enabled,
            "snap_angle": self._snap_angle_enabled,
            "snap_parallel": self._snap_parallel_enabled,
            "snap_perpendicular": self._snap_perpendicular_enabled,
            "snap_equal_length": self._snap_equal_length_enabled,
            "snap_axis_alignment": self._snap_axis_alignment_enabled,
            "snap_align_x": self._snap_align_x_enabled,
            "snap_align_y": self._snap_align_y_enabled,
            "snap_strength": self._snap_strength,
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

    def _compact_entities(self, drop: set[str]) -> None:
        """Remove entities identified by ``drop`` entity IDs.

        Kind/meta/flags live on the EntityRecord, so they travel with the
        surviving entities — no index remapping needed (previously ~45 lines
        of error-prone bookkeeping).
        """
        if drop:
            self._canvas_service.delete_entities(tuple(drop))

    def delete_entities(self, entity_ids: list[str]) -> int:
        """Delete specific entities regardless of the active layer (used by
        the layer tree); locked entities survive."""
        drop = {
            eid
            for eid in entity_ids
            if (entity := self._entities_by_id.get(eid)) is not None and not entity.locked
        }
        if not drop:
            return 0
        from simple_stipple.document.commands import DeleteCommand

        result = self._canvas_service.execute(DeleteCommand(entity_ids=tuple(drop)))
        if not result.changed:
            return 0
        self._remove_dimensions_for_entities(set(drop))
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return len(drop)

    def delete_selected(self) -> int:
        delete_set = {eid for eid in self._sel if not self._is_locked(eid)}
        n = len(delete_set)
        if not n:
            return 0
        from simple_stipple.document.commands import DeleteCommand

        result = self._canvas_service.execute(DeleteCommand(entity_ids=tuple(delete_set)))
        if not result.changed:
            return 0
        self._remove_dimensions_for_entities(set(delete_set))
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return n

    def set_undo_hooks(self, undo=None, redo=None) -> None:
        """Let the owning page undo its own state before the canvas history.

        Undo is reachable from the Edit menu, the command palette and the
        radial menu, all of which call straight into the canvas. A page that
        owns undoable state outside the document has to be consulted here or
        those routes silently skip it.
        """
        self._undo_hook = undo
        self._redo_hook = redo

    def undo_depth(self) -> int:
        """Number of undoable canvas actions — lets a caller interleave its
        own undo stack with this one without duplicating the history."""
        return self._canvas_service.undo_depth()

    def undo(self) -> bool:
        if callable(self._undo_hook) and self._undo_hook():
            return True
        command_result = self._canvas_service.undo()
        if command_result.changed:
            self._gizmo_drag_mode = None
            self._refresh_driving_dimensions()
            self._reset_edit_interaction_state()
            self._redraw()
            self._notify()
            self._fire_poly_change()
            return True
        self._show_flash("Nothing to undo", 900)
        return False

    # Temporary named compatibility API; each method delegates to the composed
    # editing adapter and is removed as its caller migrates to DocumentService.

    def _is_poly_closed(self, *args, **kwargs):
        return self._editing._is_poly_closed(*args, **kwargs)

    def _split_geometry_with_line(self, *args, **kwargs):
        return self._editing._split_geometry_with_line(*args, **kwargs)

    def _carve_geometry_with_shape(self, *args, **kwargs):
        return self._editing._carve_geometry_with_shape(*args, **kwargs)

    def _snap_to_polyline(self, *args, **kwargs):
        return self._editing._snap_to_polyline(*args, **kwargs)

    def _resolve_snap(self, *args, **kwargs):
        return self._editing._resolve_snap(*args, **kwargs)

    def _resolve_drag_snap(self, *args, **kwargs):
        return self._editing._resolve_drag_snap(*args, **kwargs)

    def _angle_snap(self, *args, **kwargs):
        return self._editing._angle_snap(*args, **kwargs)

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

    def scale_by_reference(self, *args, **kwargs):
        return self._editing.scale_by_reference(*args, **kwargs)

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
        if callable(self._redo_hook) and self._redo_hook():
            return True
        command_result = self._canvas_service.redo()
        if command_result.changed:
            self._gizmo_drag_mode = None
            self._refresh_driving_dimensions()
            self._reset_edit_interaction_state()
            self._redraw()
            self._notify()
            self._fire_poly_change()
            return True
        self._show_flash("Nothing to redo", 900)
        return False

    def select_all(self) -> None:
        self._sel = {e.id for e in self._entities if e.id not in self._noninteractive_ids()}
        self._all_dimensions_selected = bool(self._dimensions)
        self._selected_dimension = 0 if self._dimensions else None
        self._redraw()
        self._notify()

    def select_open_paths(self) -> None:
        """Select every interactive path whose endpoints do not close."""
        blocked = self._noninteractive_ids()
        self._sel = {
            entity.id
            for entity in self._entities
            if entity.id not in blocked and not self._is_poly_closed(entity.points)
        }
        self._redraw()
        self._notify()

    def select_closed_paths(self) -> None:
        """Select every interactive closed path."""
        blocked = self._noninteractive_ids()
        self._sel = {
            entity.id
            for entity in self._entities
            if entity.id not in blocked and self._is_poly_closed(entity.points)
        }
        self._redraw()
        self._notify()

    def deselect_all(self) -> None:
        self._sel = set()
        self._selected_dimension = None
        self._all_dimensions_selected = False
        self._redraw()
        self._notify()

    def _invert_selection(self) -> None:
        """Invert selection: select all unselected, deselect all selected."""
        all_ids = {e.id for e in self._entities} - self._noninteractive_ids()
        self._sel = all_ids - self._sel
        self._redraw()
        self._notify()

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

    def _dimension_line_points(
        self, dim: dict
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        """World-space endpoints of the offset dimension line — p1/p2 shifted
        perpendicular by ``offset`` mm. ``None`` when p1 == p2."""
        ax, ay = dim["p1"]
        bx, by = dim["p2"]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return None
        nx, ny = -dy / length, dx / length
        offset = dim["offset"]
        return (
            (ax + nx * offset, ay + ny * offset),
            (bx + nx * offset, by + ny * offset),
        )

    def _delete_selected_dimension(self) -> None:
        if self._all_dimensions_selected:
            self._clear_dimensions()
            self._selected_dimension = None
            self._all_dimensions_selected = False
            self._notify()
            self._fire_poly_change()
            return
        di = self._selected_dimension
        if di is None or not (0 <= di < len(self._dimensions)):
            self._selected_dimension = None
            return
        self._remove_dimension(di)
        self._selected_dimension = None
        self._all_dimensions_selected = False
        self._dimension_drag = None
        self._notify()

    def set_image_bounds(self, w_mm: float, h_mm: float) -> None:
        self._img_bounds = (w_mm, h_mm)
        self._redraw()

    def set_background_image(
        self,
        pil_img: PILImage.Image,
        w_mm: float,
        h_mm: float,
        x_mm: float = 0.0,
        y_mm: float = 0.0,
        rotation_deg: float = 0.0,
    ) -> None:
        self._bg_pil = pil_img
        self._bg_w_mm = w_mm
        self._bg_h_mm = h_mm
        self._bg_x_mm = x_mm
        self._bg_y_mm = y_mm
        self._bg_rotation_deg = rotation_deg
        self._bg_pixmap = None
        self._bg_cached_scale = 0.0
        self._redraw()

    def clear_background_image(self) -> None:
        self._bg_pil = None
        self._bg_pixmap = None
        if self._bg_selected:
            self._bg_selected = False
            self.backgroundSelectionChanged.emit(False)
        self._redraw()

    def set_background_image_editable(self, enabled: bool, callback=None) -> None:
        self._bg_editable = bool(enabled)
        if not self._bg_editable:
            self.select_background_image(False)
        self._bg_edit_callback = callback
        self._bg_drag = None
        self._redraw()

    def set_background_image_key_callback(self, callback) -> None:
        """Set the owner callback for keys while an editable image is selected."""
        self._bg_key_callback = callback

    def select_background_image(self, selected: bool = True) -> None:
        was_selected = self._bg_selected
        self._bg_selected = bool(selected and self._bg_editable and self._bg_pil is not None)
        if not self._bg_selected:
            self._bg_drag = None
        self._redraw()
        if self._bg_selected != was_selected:
            self.backgroundSelectionChanged.emit(self._bg_selected)

    def is_background_image_selected(self) -> bool:
        return self._bg_selected

    def _background_contains(self, cx: float, cy: float) -> bool:
        if not self._bg_editable or self._bg_pil is None:
            return False
        wx, wy = self._background_unrotate(*self._c2w(cx, cy))
        return (
            self._bg_x_mm <= wx <= self._bg_x_mm + self._bg_w_mm
            and self._bg_y_mm <= wy <= self._bg_y_mm + self._bg_h_mm
        )

    def _background_unrotate(self, wx: float, wy: float) -> tuple[float, float]:
        """Map a world point into the image's unrotated placement coordinates."""
        angle = math.radians(-self._bg_rotation_deg)
        center_x = self._bg_x_mm + self._bg_w_mm / 2.0
        center_y = self._bg_y_mm + self._bg_h_mm / 2.0
        dx, dy = wx - center_x, wy - center_y
        return (
            center_x + dx * math.cos(angle) - dy * math.sin(angle),
            center_y + dx * math.sin(angle) + dy * math.cos(angle),
        )

    def _background_canvas_corners(self) -> dict[str, tuple[float, float]]:
        center_x = self._bg_x_mm + self._bg_w_mm / 2.0
        center_y = self._bg_y_mm + self._bg_h_mm / 2.0
        angle = math.radians(self._bg_rotation_deg)
        result = {}
        for name, (wx, wy) in {
            "nw": (self._bg_x_mm, self._bg_y_mm + self._bg_h_mm),
            "ne": (self._bg_x_mm + self._bg_w_mm, self._bg_y_mm + self._bg_h_mm),
            "se": (self._bg_x_mm + self._bg_w_mm, self._bg_y_mm),
            "sw": (self._bg_x_mm, self._bg_y_mm),
        }.items():
            dx, dy = wx - center_x, wy - center_y
            rotated = (
                center_x + dx * math.cos(angle) - dy * math.sin(angle),
                center_y + dx * math.sin(angle) + dy * math.cos(angle),
            )
            result[name] = self._w2c(*rotated)
        return result

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

    def set_empty_actions(self, actions: list[tuple[str, object]]) -> None:
        """Offer the empty canvas's next steps as buttons, not numbered prose.

        Shown only while the canvas holds nothing; ``sync_empty_actions``
        keeps that true on every redraw and resize.
        """
        from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

        existing = getattr(self, "_empty_actions_bar", None)
        if existing is not None:
            existing.deleteLater()
        self._empty_actions_bar = None
        if not actions:
            return
        bar = QWidget(self)
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for label, callback in actions:
            button = QPushButton(label, bar)
            button.setMinimumHeight(30)
            button.clicked.connect(callback)
            row.addWidget(button)
        self._empty_actions_bar = bar
        self.sync_empty_actions()

    def sync_empty_actions(self) -> None:
        """Centre the empty-state buttons under the hint, or hide them."""
        bar = getattr(self, "_empty_actions_bar", None)
        if bar is None:
            return
        show = not self._entities and not self._draw_pts
        bar.setVisible(show)
        if not show:
            return
        bar.adjustSize()
        bar.move(
            max(0, (self.width() - bar.width()) // 2),
            max(0, self.height() // 2 + 40),
        )
        bar.raise_()

    def set_grid_visible(self, visible: bool) -> None:
        self._grid_visible = bool(visible)
        self._redraw()

    def set_context_menu_sections(self, sections: list[str]) -> None:
        from simple_stipple.platform.config import normalize_context_menu_sections

        normalized = normalize_context_menu_sections(sections)
        self._context_menu_sections = set(normalized)
        self._context_menu_section_order = normalized

    def set_context_menu_overflow_sections(self, sections: list[str]) -> None:
        from simple_stipple.platform.config import normalize_context_menu_overflow_sections

        self._context_menu_overflow_sections = set(
            normalize_context_menu_overflow_sections(sections)
        )

    def set_context_menu_profile(self, profile: str) -> None:
        self._context_menu_profile = str(profile)

    def set_context_menu_profiles(self, profiles: dict) -> None:
        from simple_stipple.platform.config import normalize_context_menu_profiles

        profile = normalize_context_menu_profiles(profiles).get(self._context_menu_profile, {})
        self.set_context_menu_sections(profile.get("sections", []))
        self.set_context_menu_overflow_sections(profile.get("overflow", []))
        self._context_menu_transform_items = list(profile.get("transform", []))
        self._context_menu_item_order = list(profile.get("items", []))
        self._context_menu_overflow_items = set(profile.get("overflow_items", []))
        self._context_menu_actions_configured = bool(profile.get("action_items_configured", []))

    def _context_menu_section_enabled(self, section: str) -> bool:
        if self._context_menu_actions_configured or self._context_menu_item_order:
            # Action-level configuration filters the completed menu once all
            # possible leaf actions have been built.
            return True
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

    def set_snap_midpoint(self, enabled: bool) -> None:
        self._snap_midpoint_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()

    def set_snap_intersection(self, enabled: bool) -> None:
        self._snap_intersection_enabled = bool(enabled)
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

    def set_snap_parallel(self, enabled: bool) -> None:
        self._snap_parallel_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()

    def set_snap_perpendicular(self, enabled: bool) -> None:
        self._snap_perpendicular_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()

    def set_snap_equal_length(self, enabled: bool) -> None:
        self._snap_equal_length_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()

    def set_snap_strength(self, strength: float) -> None:
        """Set the screen-space magnetic capture radius multiplier."""
        try:
            self._snap_strength = max(0.0, min(2.0, float(strength)))
        except (TypeError, ValueError):
            self._snap_strength = 1.0
        self._refresh_draw_sidebar_state()
        self._redraw()

    def set_snap_axis_alignment(self, enabled: bool) -> None:
        self._snap_axis_alignment_enabled = bool(enabled)
        self._snap_align_x_enabled = bool(enabled)
        self._snap_align_y_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()

    def set_snap_align_x(self, enabled: bool) -> None:
        self._snap_align_x_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()

    def set_snap_align_y(self, enabled: bool) -> None:
        self._snap_align_y_enabled = bool(enabled)
        self._refresh_draw_sidebar_state()

    # ── Inlined from removed mixins (methods actually called from view.py) ──

    def set_construction_mode(self, enabled: bool) -> None:
        self._draw_construction_mode = bool(enabled)
        self._refresh_draw_sidebar_state()
        self._redraw()

    def set_rotation_snap_increment(self, value: float) -> None:
        self._rotation_snap_increment = max(0.1, min(180.0, float(value)))

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

    def _queue_cursor_position_update(self) -> None:
        """Publish the final mouse-move position after snapping has resolved."""
        if getattr(self, "_cursor_position_update_queued", False):
            return
        self._cursor_position_update_queued = True
        QTimer.singleShot(0, self._emit_cursor_position_update)

    def _emit_cursor_position_update(self) -> None:
        self._cursor_position_update_queued = False
        position = self.get_cursor_world_pos()
        if position is not None:
            self.cursorPositionChanged.emit(*position)

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

    _EMPTY_BBOX = (0.0, 0.0, 100.0, 100.0)

    def _notify(self) -> None:
        self._model.notify_selection_changed()
        self.selectionChanged.emit(len(self._sel))

    def _bbox(self) -> tuple[float, float, float, float]:
        pts = [
            point
            for eid in self._entities_by_id
            if self._entities_by_id[eid].kind not in {"xline", "ray"}
            for point in self._flattened_points_by_id(eid)
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

    def _selected_ids(self) -> list[str]:
        return [
            eid
            for eid in sorted(self._sel)
            if (entity := self._entities_by_id.get(eid)) is not None and not entity.hidden
        ]

    def _mutable_selected_ids(self) -> list[str]:
        return [
            eid
            for eid in self._selected_ids()
            if not self._entities_by_id.get(eid, None) or not self._entities_by_id[eid].locked
        ]

    def _selection_bounds(
        self, entity_ids: list[str] | None = None
    ) -> tuple[float, float, float, float] | None:
        items = entity_ids if entity_ids is not None else self._selected_ids()
        pts = [
            point
            for eid in items
            if (entity := self._entities_by_id.get(eid)) is not None
            and entity.kind not in {"xline", "ray"}
            for point in self._flattened_points_by_id(eid)
        ]
        if not pts:
            return None
        xs, ys = zip(*pts)
        return min(xs), min(ys), max(xs), max(ys)

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

    def _zoom_by(self, factor: float) -> None:
        w, h = max(self.width(), 100), max(self.height(), 100)
        self._zoom_at(w / 2, h / 2, factor)

    RULER_PX = 22

    def set_rulers_visible(self, visible: bool) -> None:
        self._rulers_visible = bool(visible)
        self._layout_draw_sidebar()
        self._redraw()

    def set_geometry_health_visible(self, visible: bool, *, announce: bool = False) -> None:
        self._geometry_health_visible = bool(visible)
        if announce:
            self._show_flash(
                "Geometry health overlay: ON" if visible else "Geometry health overlay: OFF",
                900,
            )
        self._redraw()

    def set_curvature_visible(self, visible: bool, *, announce: bool = False) -> None:
        self._curvature_visible = bool(visible)
        if announce:
            self._show_flash("Curvature view: ON" if visible else "Curvature view: OFF", 900)
        self._redraw()

    def set_unit_system(self, unit: str) -> None:
        """Set the display unit ("mm" or "in"). Storage/geometry stay mm."""
        if unit not in ("mm", "in"):
            return
        self._unit_system = unit
        self._redraw()

    _MOVE_SNAP_SAMPLE = 64  # max moving vertices considered per drag event

    def _redraw(self) -> None:
        self.sync_empty_actions()
        self.update()

    def paintEvent(self, event) -> None:
        """Render the canvas, then paint active-tool and chrome overlays."""
        self._renderer.paintEvent(event)
        tool = (
            self._measure_tool
            if self._measure_mode
            else self._dimension_tool
            if self._dimension_mode
            else self._tools.get(self._mode)
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if tool is not None:
            tool.paint_overlay(painter)
        self._renderer._paint_chrome_rulers(painter)
        painter.end()

    def focusNextPrevChild(self, next: bool) -> bool:
        """Keep Tab/Shift+Tab inside canvas interaction workflows."""
        if self._selectable and self._mode in {"select", "draw", "edit"}:
            return False
        return super().focusNextPrevChild(next)

    def _on_polygon_sides_spin_changed(self, value: int) -> None:
        """Live-update the polygon/star ghost's point-count control."""
        if self._draw_primitive == "star":
            self._draw_star_points = value
        else:
            self._draw_polygon_sides = value
        self._redraw()

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

    def _hit_angle_dimension_button(self, cx: float, cy: float) -> bool:
        x1, y1, x2, y2 = self._adbtn_rect
        return x1 <= cx <= x2 and y1 <= cy <= y2

    # ── Events ────────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_draw_sidebar()
        self.sync_empty_actions()
        if self._needs_fit:
            if self._entities:
                self._needs_fit = False
            self._fit()
        else:
            self._redraw()

    def keyPressEvent(self, event: QKeyEvent):
        return keyPressEvent(self, event)

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
        old_scale = self._scale
        self._scale = max(_MIN_SCALE, min(_MAX_SCALE, self._scale * factor))
        # Feedback when a zoom is swallowed by the clamp — previously the view
        # simply stopped responding with no explanation. Flash only on the
        # transition into the limit so continued scrolling doesn't spam.
        if factor > 1.0 and self._scale >= _MAX_SCALE and old_scale < _MAX_SCALE:
            self._show_flash("Maximum zoom", 900)
        elif factor < 1.0 and self._scale <= _MIN_SCALE and old_scale > _MIN_SCALE:
            self._show_flash("Minimum zoom", 900)
        self._ox = cx - wx * self._scale
        self._oy = cy + wy * self._scale
        self._redraw()
        self.viewChanged.emit()

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

    def mousePressEvent(self, event: QMouseEvent):
        pos = event.position()
        btn = event.button()

        if btn == Qt.MouseButton.LeftButton:
            bg_hit = self._background_edit_hit(pos.x(), pos.y())
            if bg_hit is not None:
                wx, wy = self._c2w(pos.x(), pos.y())
                self._bg_drag = (
                    bg_hit,
                    wx,
                    wy,
                    self._bg_x_mm,
                    self._bg_y_mm,
                    self._bg_w_mm,
                    self._bg_h_mm,
                    self._bg_rotation_deg,
                )
                return
            if self._bg_selected:
                self.select_background_image(False)
            elif (
                self._background_contains(pos.x(), pos.y())
                and self._find_poly_at(pos.x(), pos.y()) is None
            ):
                self.select_background_image(True)
                self._sel.clear()
                self._notify()
                return

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

        # Persistent, visible tool buttons. These used to be painted by dead
        # renderer helpers and had no event routing, so they were effectively
        # invisible and unclickable.
        if self._hit_measure_button(pos.x(), pos.y()):
            self.toggle_measure()
            return
        if self._hit_dimension_button(pos.x(), pos.y()):
            self.toggle_dimension_mode("linear")
            return
        if self._hit_angle_dimension_button(pos.x(), pos.y()):
            self.toggle_dimension_mode("angle")
            return

        # Rulers: press inside a ruler strip drags out a new guide.
        if self._rulers_visible and self._selectable:
            r = self.RULER_PX
            wx0, wy0 = self._c2w(pos.x(), pos.y())
            if pos.x() <= r and pos.y() <= r:
                return  # corner box
            if pos.y() <= r:
                self._guide_preview = self._canvas_service.begin_preview()
                self._guides.append(("h", wy0))
                self._guide_drag = len(self._guides) - 1
                self._selected_guide = self._guide_drag
                self._guide_drag_moved = False
                self._redraw()
                return
            if pos.x() <= r:
                self._guide_preview = self._canvas_service.begin_preview()
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
                self._guide_preview = self._canvas_service.begin_preview()
                self._guide_drag = gi
                self._selected_guide = gi
                self._guide_drag_moved = False
                self._redraw()
                return
        # Clicking elsewhere clears any selected guide.
        if self._selected_guide is not None:
            self._selected_guide = None
            self._redraw()

        # Existing dimensions take priority even while the Dimension tool is
        # armed, so clicking a value edits/selects it instead of starting a
        # new placement. Offset dragging remains a Select-mode interaction.
        if self._selectable and self._dimensions:
            di = self._find_dimension_at(pos.x(), pos.y())
            if di is not None:
                self._selected_dimension = di
                self._all_dimensions_selected = False
                self._sel.clear()
                self._dimension_drag = (
                    di
                    if self._mode == "select" and self._dimensions[di].get("type") != "angle"
                    else None
                )
                self._notify()
                self._redraw()
                return
        if self._selected_dimension is not None:
            self._selected_dimension = None
            self._notify()
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

        if self._bg_drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            mode, sx, sy, ox, oy, ow, oh, rotation = self._bg_drag
            if mode == "move":
                self._bg_x_mm, self._bg_y_mm = ox + wx - sx, oy + wy - sy
            elif mode == "rotate":
                center_x, center_y = ox + ow / 2.0, oy + oh / 2.0
                start_angle = math.degrees(math.atan2(sy - center_y, sx - center_x))
                current_angle = math.degrees(math.atan2(wy - center_y, wx - center_x))
                self._bg_rotation_deg = rotation + current_angle - start_angle
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    increment = self._rotation_snap_increment
                    self._bg_rotation_deg = round(self._bg_rotation_deg / increment) * increment
            else:
                wx, wy = self._background_unrotate(wx, wy)
                left, right, bottom, top = ox, ox + ow, oy, oy + oh
                if "w" in mode:
                    left = min(wx, right - 0.01)
                if "e" in mode:
                    right = max(wx, left + 0.01)
                if "s" in mode:
                    bottom = min(wy, top - 0.01)
                if "n" in mode:
                    top = max(wy, bottom + 0.01)
                self._bg_x_mm, self._bg_y_mm = left, bottom
                self._bg_w_mm, self._bg_h_mm = right - left, top - bottom
            if callable(self._bg_edit_callback):
                self._bg_edit_callback(
                    self._bg_x_mm,
                    self._bg_y_mm,
                    self._bg_w_mm,
                    self._bg_h_mm,
                    self._bg_rotation_deg,
                )
            self._bg_pixmap = None
            self._redraw()
            return

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

        if self._bg_drag is not None:
            self._bg_drag = None
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
            # Commit the whole gesture (add / move / delete) as one undoable
            # command; a click that changed nothing commits as a no-op.
            self._canvas_service.commit_preview(self._guide_preview)
            self._guide_preview = None
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
                eid = self._lmb_target
                mods = event.modifiers()
                if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                    if eid in self._sel:
                        self._sel = self._sel - {eid}
                    else:
                        self._sel = self._sel | {eid}
                else:
                    self._sel = {eid}
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
        dimension = self._find_dimension_at(event.position().x(), event.position().y())
        if dimension is not None:
            self._selected_dimension = dimension

            if isinstance(self._dimensions[dimension].get("driving"), dict):
                self._edit_driving_dimension(dimension)
                return

            def set_precision(value: float) -> None:
                self._set_dimension_precision_value(dimension, int(round(value)))
                self._notify()

            self._show_hud_prompt(
                "Dimension decimals",
                float(self._dimensions[dimension].get("precision", 2)),
                set_precision,
                minimum=0,
                maximum=6,
            )
            return
        tool = self._tools.get(self._mode)
        if tool is not None:
            tool.double_click(event)


CanvasView._cancel_active_drag = _cancel_active_drag
CanvasView._cancel_draw_in_progress = _cancel_draw_in_progress
CanvasView._escape_cb = _escape_cb
CanvasView.exit_to_select = exit_to_select
CanvasView._find_dimension_at = _find_dimension_at
CanvasView._rightclick_cb = _rightclick_cb
CanvasView._round_vertex = _round_vertex
CanvasView._show_shape_dim_inputs = _show_shape_dim_inputs
CanvasView.get_export_dxf_state = get_export_dxf_state
CanvasView.set_view_state = set_view_state
CanvasView._animate_view_to = _animate_view_to
CanvasView._background_edit_hit = _background_edit_hit
CanvasView._chamfer_vertex = _chamfer_vertex
CanvasView._connected_entities = _connected_entities
CanvasView._dismiss_shape_dim_inputs = _dismiss_shape_dim_inputs
CanvasView._entity_center = _entity_center
CanvasView._moving_sample_points = _moving_sample_points
CanvasView._remove_dimensions_for_entities = _remove_dimensions_for_entities
CanvasView._update_cursor = _update_cursor
CanvasView.add_polylines_state = add_polylines_state
CanvasView.eventFilter = eventFilter
CanvasView.get_command_guidance = get_command_guidance
CanvasView.get_context_actions = get_context_actions
CanvasView.get_entity_records = get_entity_records
CanvasView.get_status_summary = get_status_summary
CanvasView.get_view_state = get_view_state
CanvasView.select_geometry_category = select_geometry_category
CanvasView.set_entity_records = set_entity_records
CanvasView.set_ghost_polylines = set_ghost_polylines
CanvasView.set_mode = set_mode
CanvasView.show_coordinate_entry = show_coordinate_entry
CanvasView.trigger_context_action = trigger_context_action
CanvasView.toggle_dimension_mode = toggle_dimension_mode
CanvasView.toggle_measure = toggle_measure
CanvasView._append_dimension = _append_dimension
CanvasView._clear_dimensions = _clear_dimensions
CanvasView._commit_annotation_edit = _commit_annotation_edit
CanvasView._edit_driving_dimension = _edit_driving_dimension
CanvasView._refresh_driving_dimensions = _refresh_driving_dimensions
CanvasView._remove_dimension = _remove_dimension
CanvasView._remove_guide = _remove_guide
CanvasView._set_dimension_precision = _set_dimension_precision
CanvasView._set_dimension_precision_value = _set_dimension_precision_value
CanvasView.keyPressEvent = keyPressEvent
CanvasView.keyReleaseEvent = keyReleaseEvent
CanvasView.mouseDoubleClickEvent = mouseDoubleClickEvent
CanvasView.mouseMoveEvent = mouseMoveEvent
CanvasView.mousePressEvent = mousePressEvent
CanvasView.mouseReleaseEvent = mouseReleaseEvent
