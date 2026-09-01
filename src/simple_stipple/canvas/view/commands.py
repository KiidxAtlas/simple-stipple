# Commands and tool callbacks extracted from view.py

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QLabel, QMenu

from simple_stipple.canvas.constants import MIN_SCALE as _MIN_SCALE
from simple_stipple.core.cad.constraints import GeometricConstraint
from simple_stipple.core.editing.corners import chamfered_corner_points, rounded_corner_points
from simple_stipple.ui.components.units import suffix as _unit_suffix


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
                precision = max(0, min(6, int(d.get("precision", 2))))
            except (KeyError, TypeError, ValueError):
                continue
            if len(p1) == 2 and len(p2) == 2:
                restored.append(
                    {
                        "type": str(d.get("type", "linear")),
                        "p1": p1,
                        "p2": p2,
                        "offset": offset,
                        "precision": precision,
                        **(
                            {"p3": tuple(d["p3"]), "points": list(d.get("points", []))}
                            if d.get("type") == "angle" and "p3" in d
                            else {}
                        ),
                        **(
                            {"driving": deepcopy(d["driving"])}
                            if isinstance(d.get("driving"), dict)
                            else {}
                        ),
                    }
                )
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
    # Groups restore through set_entity_records (per-entity "group" key); the
    # legacy view-state "groups" map was written id-keyed but read here as
    # index-keyed, which silently WIPED every group on workspace load.
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


def _rightclick_cb(self, cx: float, cy: float) -> None:
    if self._dimension_mode:
        if self._dimension_tool.back():
            self._show_flash("Last dimension step cleared", 900)
        else:
            self.toggle_dimension_mode(self._dimension_kind)
            self._show_flash("Dimension tool closed", 800)
        self._redraw()
        return

    if self._measure_mode:
        if self._measure_locked:
            self._measure_locked = False
            self._measure_end = None
            self._dismiss_measure_edit()
            self._show_flash("Target entry canceled · pick the second reference point", 1200)
        elif self._measure_anchor is not None:
            self._measure_anchor = None
            self._measure_hover = None
            self._show_flash("Reference point cleared", 900)
        else:
            self.toggle_measure()
            self._show_flash("Scale tool closed", 800)
        self._redraw()
        return

    dimension = self._find_dimension_at(cx, cy)
    if dimension is not None:
        self._selected_dimension = dimension
        menu = QMenu(self)
        if isinstance(self._dimensions[dimension].get("driving"), dict):
            menu.addAction("Edit measurement…", lambda: self._edit_driving_dimension(dimension))
            menu.addSeparator()
        precision_menu = menu.addMenu("Decimal places")
        current_precision = int(self._dimensions[dimension].get("precision", 2))
        for decimals in range(7):
            action = precision_menu.addAction(str(decimals))
            action.setCheckable(True)
            action.setChecked(decimals == current_precision)
            action.triggered.connect(
                lambda _checked=False, value=decimals: self._set_dimension_precision(
                    dimension, value
                )
            )
        menu.addSeparator()
        menu.addAction("Delete dimension", self._delete_selected_dimension)
        menu.popup(self.mapToGlobal(QPointF(cx, cy).toPoint()))
        self._redraw()
        return

    if self._mode == "draw":
        if self._draw_shape_preview_active and self._shape_primitive_active():
            self._cancel_draw_points()
            self._show_flash("Shape preview canceled", 700)
            return
        # Right-click = finish open polyline (no close), stay in draw mode
        self._finish_draw(close=False)
        return

    if self._mode == "edit":
        hit = self._hit_test.nearest_vertex_by_id(cx, cy)
        if hit is not None:
            entity_id, vi = hit
            menu = QMenu()

            def _prompt_round_corner() -> None:
                poly = self._entities_by_id[entity_id].points
                closed = self._is_poly_closed(poly)

                def _preview(r: float) -> None:
                    new_poly = rounded_corner_points(poly, vi, r, closed=closed)
                    self._set_operation_preview([new_poly] if new_poly is not None else [])

                self._show_hud_prompt(
                    "Round radius (mm)",
                    1.0,
                    lambda r: self._round_vertex(entity_id, vi, r),
                    minimum=0.01,
                    preview=_preview,
                )

            def _prompt_chamfer_corner() -> None:
                poly = self._entities_by_id[entity_id].points
                closed = self._is_poly_closed(poly)

                def _preview(d: float) -> None:
                    new_poly = chamfered_corner_points(poly, vi, d, closed=closed)
                    self._set_operation_preview([new_poly] if new_poly is not None else [])

                self._show_hud_prompt(
                    "Chamfer distance (mm)",
                    1.0,
                    lambda d: self._chamfer_vertex(entity_id, vi, d),
                    minimum=0.01,
                    preview=_preview,
                )

            poly = self._entities_by_id[entity_id].points
            is_closed = (
                len(poly) >= 4
                and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01
            )
            unique_count = len(poly) - 1 if is_closed else len(poly)
            if unique_count > 3:
                menu.addAction("Delete vertex", lambda: self._delete_vertex(entity_id, vi))
            if (is_closed and unique_count >= 3) or (not is_closed and 0 < vi < len(poly) - 1):
                menu.addAction("Round corner…", _prompt_round_corner)
                menu.addAction("Chamfer corner…", _prompt_chamfer_corner)
            menu.addAction("Delete polyline", lambda: self._delete_poly(entity_id))
            menu.popup(self.mapToGlobal(QPointF(cx, cy).toPoint()))
        return


def _round_vertex(self, entity_id: str, vi: int, radius: float) -> bool:
    if entity_id not in self._entities_by_id or radius <= 0:
        return False
    poly = self._entities_by_id[entity_id].points
    new_poly = rounded_corner_points(poly, vi, radius, closed=self._is_poly_closed(poly))
    if new_poly is None:
        return False
    entity = deepcopy(self._entities_by_id[entity_id])
    entity.points = new_poly
    entity.kind = "polyline"
    entity.meta = None
    self._canvas_service.update_entities([entity])
    self._redraw()
    self._notify()
    self._fire_poly_change()
    return True


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
    if self._bg_drag is not None:
        _mode, _sx, _sy, ox, oy, ow, oh, rotation = self._bg_drag
        self._bg_x_mm, self._bg_y_mm = ox, oy
        self._bg_w_mm, self._bg_h_mm = ow, oh
        self._bg_rotation_deg = rotation
        if callable(self._bg_edit_callback):
            self._bg_edit_callback(ox, oy, ow, oh, rotation)
        self._bg_pixmap = None
        self._bg_drag = None
        self._redraw()
        return True
    if self._guide_drag is not None:
        self._canvas_service.cancel_preview(self._guide_preview)
        self._guide_preview = None
        self._guide_drag = None
        self._guide_drag_moved = False
        self._selected_guide = None
        self._redraw()
        return True
    if self._dimension_drag is not None:
        self._canvas_service.cancel_preview(self._dimension_drag_preview)
        self._dimension_drag_preview = None
        self._dimension_drag = None
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
    w_edit = self._make_hud_edit(width=86, height=24, align=Qt.AlignmentFlag.AlignCenter)
    w_edit.setText(f"{w:.2f}")
    w_edit.setAccessibleName("Shape width")
    w_edit.setToolTip("Width · enter a value or expression")
    w_edit.setProperty("shape_hud_temp", True)
    w_edit.returnPressed.connect(self._apply_and_commit_shape_preview)
    # Resize live as the user types; Enter still commits. The preview is
    # transient (moves the rubber cursor), so partial input is harmless.
    w_edit.textEdited.connect(lambda _t: self._apply_shape_size_inputs())
    w_label = QLabel(f"W ({_unit_suffix(self._unit_system)})", self)
    w_label.setProperty("role", "canvas-hud-label")
    w_label.setProperty("shape_hud_temp", True)
    w_label.setFixedSize(86, 16)
    w_label.show()
    self._draw_shape_w_label = w_label

    h_edit = self._make_hud_edit(width=86, height=24, align=Qt.AlignmentFlag.AlignCenter)
    h_edit.setText(f"{h:.2f}")
    h_edit.setAccessibleName("Shape height")
    h_edit.setToolTip("Height · enter a value or expression")
    h_edit.setProperty("shape_hud_temp", True)
    h_edit.returnPressed.connect(self._apply_and_commit_shape_preview)
    h_edit.textEdited.connect(lambda _t: self._apply_shape_size_inputs())
    h_label = QLabel(f"H ({_unit_suffix(self._unit_system)})", self)
    h_label.setProperty("role", "canvas-hud-label")
    h_label.setProperty("shape_hud_temp", True)
    h_label.setFixedSize(86, 16)
    h_label.show()
    self._draw_shape_h_label = h_label

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
        sides_spin.setAccessibleName(
            "Star points" if self._draw_primitive == "star" else "Polygon sides"
        )
        sides_spin.setPrefix("Points: " if self._draw_primitive == "star" else "Sides: ")
        sides_spin.setFixedWidth(112)
        sides_spin.valueChanged.connect(self._on_polygon_sides_spin_changed)
        self._draw_shape_sides_spin = sides_spin

    # Anchor the fields exactly where the amber badges painted a moment ago
    # (they hide while the fields exist), so Tab reads as "edit the number on
    # the shape", not "open a floating panel".
    self._reposition_shape_dim_inputs()


def _find_dimension_at(self, cx: float, cy: float) -> int | None:
    """Find a placed dimension by its line, arc, rays, or value badge."""

    def segment_distance(start, end) -> float:
        dx, dy = end[0] - start[0], end[1] - start[1]
        length_sq = dx * dx + dy * dy
        if length_sq < 1e-9:
            return math.dist((cx, cy), start)
        t = max(
            0.0,
            min(1.0, ((cx - start[0]) * dx + (cy - start[1]) * dy) / length_sq),
        )
        return math.dist((cx, cy), (start[0] + t * dx, start[1] + t * dy))

    best: int | None = None
    best_d = 9.0
    for i, dim in enumerate(self._dimensions):
        if dim.get("type") == "angle" and "p3" in dim:
            first = self._w2c(*dim["p1"])
            vertex = self._w2c(*dim["p2"])
            third = self._w2c(*dim["p3"])
            a1 = math.atan2(first[1] - vertex[1], first[0] - vertex[0])
            a2 = math.atan2(third[1] - vertex[1], third[0] - vertex[0])
            sweep = (a2 - a1 + math.pi) % math.tau - math.pi
            mid = a1 + sweep / 2.0
            label = (
                vertex[0] + math.cos(mid) * 46.0,
                vertex[1] + math.sin(mid) * 46.0,
            )
            if abs(cx - label[0]) <= 58 and abs(cy - label[1]) <= 15:
                return i
            # The two rays usually lie directly on top of the measured
            # shape segments. They are visual witnesses, not dimension
            # handles; treating them as hits makes the underlying shape
            # impossible to select or edit. Only the badge and arc own
            # pointer interaction for angular dimensions.
            arc_distance = abs(math.dist((cx, cy), vertex) - 28.0)
            angle_from_first = (
                math.atan2(cy - vertex[1], cx - vertex[0]) - a1 + math.pi
            ) % math.tau - math.pi
            within_sweep = (
                0.0 <= angle_from_first <= sweep
                if sweep >= 0.0
                else sweep <= angle_from_first <= 0.0
            )
            if within_sweep and arc_distance < best_d:
                best, best_d = i, arc_distance
            continue
        line = self._dimension_line_points(dim)
        if line is None:
            continue
        (lax_w, lay_w), (lbx_w, lby_w) = line
        lax, lay = self._w2c(lax_w, lay_w)
        lbx, lby = self._w2c(lbx_w, lby_w)
        label_x, label_y = (lax + lbx) / 2.0, (lay + lby) / 2.0 - 12.0
        if abs(cx - label_x) <= 62 and abs(cy - label_y) <= 15:
            return i
        d = segment_distance((lax, lay), (lbx, lby))
        if d < best_d:
            best_d = d
            best = i
    return best


def get_export_dxf_state(self, *, include_hidden: bool = False) -> list[dict[str, Any]]:
    self._sync_shape_storage_from_entities()
    result: list[dict[str, Any]] = []
    for entity_id, entity in self._entities_by_id.items():
        if entity.construction or (entity.hidden and not include_hidden):
            continue
        kind = entity.kind
        meta = entity.meta
        poly = entity.points
        # Only shapes the user actually named (custom label, or a named
        # group) get their own DXF layer on export — auto-generating a
        # "shape_N"/"group_N" name for every ordinary shape used to force
        # each one onto its own layer, silently fragmenting a document
        # the user had deliberately organized onto a single app layer.
        # Un-named shapes simply follow their real `layer` assignment,
        # which _export() already groups correctly.
        gid = entity.group
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
            tessellated = self._flattened_points_by_id(entity_id)
            if len(tessellated) >= 2:
                poly = tessellated
                kind = "polyline"
                export_meta = {}
                if default_name:
                    export_meta["name"] = default_name
        result.append(
            {
                "entity_id": entity_id,
                "polyline": list(poly),
                "kind": kind,
                "meta": export_meta,
                "layer": entity.layer,
                "group": entity.group,
            }
        )
    return result


# Default work-area shown before anything is drawn — without this, the
# canvas keeps its raw __init__ scale (1 px/mm) until the first fit,
# so an empty document's rulers show a meaningless 0-800mm span instead
# of a plausible small work area.
_EMPTY_BBOX = (0.0, 0.0, 100.0, 100.0)


def _cancel_draw_in_progress(self) -> bool:
    """Cancel the current in-progress path/shape without leaving Draw mode.

    Standard CAD behavior (and what the status hint "Esc cancels" promises):
    the first Escape drops the unfinished geometry but keeps the tool armed;
    a second Escape then exits to Select via _escape_cb.
    """
    if self._mode != "draw":
        return False
    in_progress = bool(
        self._draw_pts
        or self._draw_arc_pts
        or self._pen_pts
        or self._draw_shape_preview_active
        or self._draw_shape_anchor_w is not None
    )
    if not in_progress:
        return False
    self._draw_pts.clear()
    self._draw_point_snap_types.clear()
    self._dismiss_shape_dim_inputs()
    self._dismiss_dim_inputs()
    self._draw_constraint = None
    self._draw_snap = None
    self._draw_snap_type = None
    self._angle_snap_active = False
    self._draw_shape_preview_active = False
    self._draw_shape_anchor_w = None
    self._draw_shape_cursor_w = None
    self._draw_arc_pts.clear()
    self._pen_pts.clear()
    self._pen_tangents.clear()
    self._pen_dragging = False
    self._pen_press_screen = None
    self._show_flash("Path cancelled — Esc again to exit Draw", 1200)
    self._redraw()
    return True


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
    self._corner_pick_armed = None
    self._constraint_pick_armed = None

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


def exit_to_select(self) -> None:
    """Cancel the active canvas interaction and restore Select mode.

    This is intentionally callable outside ``keyPressEvent`` so an Escape
    event from a side-panel or HUD input can use the exact same cleanup.
    """
    self._cancel_active_drag()
    self._escape_cb()
