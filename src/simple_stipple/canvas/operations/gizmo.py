"""Resize, rotate, and scale gizmo behavior composed by the canvas view."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, ClassVar

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from simple_stipple.engine.cad.editor_geometry import (
    transform_entity_metadata,
    update_entity_parameter,
)
from simple_stipple.engine.cad.shapes import transform_meta


class GizmoService:
    """Own resize, rotate, and scale drag state transitions."""

    def __init__(self, host) -> None:
        self._host = host

    _HANDLE_ANCHORS: ClassVar[dict[str, tuple[tuple[float, float], tuple[float, float]]]] = {
        # handle → (anchor position, handle position) as bbox fractions
        "nw": ((1.0, 0.0), (0.0, 1.0)),
        "n": ((0.5, 0.0), (0.5, 1.0)),
        "ne": ((0.0, 0.0), (1.0, 1.0)),
        "e": ((0.0, 0.5), (1.0, 0.5)),
        "se": ((0.0, 1.0), (1.0, 0.0)),
        "s": ((0.5, 1.0), (0.5, 0.0)),
        "sw": ((1.0, 1.0), (0.0, 0.0)),
        "w": ((1.0, 0.5), (0.0, 0.5)),
    }

    def _start_gizmo_drag(
        self, mode: str, wx: float, wy: float, *, from_center: bool = False
    ) -> bool:
        bounds = self._host._selection_bounds()
        if bounds is None or not self._host._sel:
            return False
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        self._host._gizmo_local_shape = None
        if mode.startswith("scale-"):
            frac_a, frac_h = self._HANDLE_ANCHORS[mode[6:]]
            if from_center:
                frac_a = (0.5, 0.5)
            indices = self._host._selected_ids()
            entity = self._host._entities_by_id[indices[0]] if len(indices) == 1 else None
            meta = entity.meta if entity is not None and isinstance(entity.meta, dict) else None
            dims = None
            if entity is not None and meta is not None:
                if entity.kind in {"rectangle", "rounded_rectangle"}:
                    dims = (
                        float(meta.get("width", 0)),
                        float(meta.get("height", 0)),
                        "width",
                        "height",
                    )
                elif entity.kind == "ellipse":
                    dims = (2 * float(meta.get("rx", 0)), 2 * float(meta.get("ry", 0)), "rx", "ry")
                elif entity.kind == "circle":
                    diameter = 2 * float(meta.get("radius", 0))
                    dims = (diameter, diameter, "radius", "radius")
                elif entity.kind == "slot":
                    dims = (
                        float(meta.get("length", 0)),
                        float(meta.get("width", 0)),
                        "length",
                        "width",
                    )
            if dims is not None and min(dims[0], dims[1]) > 1e-9:
                assert meta is not None
                cx, cy = (float(v) for v in meta.get("center", (cx, cy)))
                rotation = float(meta.get("rotation", 0.0))
                angle = math.radians(rotation)

                def _world(frac: tuple[float, float]) -> tuple[float, float]:
                    lx = (frac[0] - 0.5) * dims[0]
                    ly = (frac[1] - 0.5) * dims[1]
                    return (
                        cx + lx * math.cos(angle) - ly * math.sin(angle),
                        cy + lx * math.sin(angle) + ly * math.cos(angle),
                    )

                self._host._gizmo_anchor_w = _world(frac_a)
                self._host._gizmo_handle_w = _world(frac_h)
                self._host._gizmo_local_shape = {
                    "entity_id": indices[0],
                    "center": (cx, cy),
                    "rotation": rotation,
                    "width": dims[0],
                    "height": dims[1],
                    "x_key": dims[2],
                    "y_key": dims[3],
                    "from_center": from_center,
                }
            else:
                x0, y0, x1, y1 = bounds
                self._host._gizmo_anchor_w = (
                    x0 + (x1 - x0) * frac_a[0],
                    y0 + (y1 - y0) * frac_a[1],
                )
                self._host._gizmo_handle_w = (
                    x0 + (x1 - x0) * frac_h[0],
                    y0 + (y1 - y0) * frac_h[1],
                )
        else:
            vec = (wx - cx, wy - cy)
            if math.hypot(vec[0], vec[1]) < 1e-9:
                return False
            self._host._gizmo_start_vec = vec
        self._host._gizmo_drag_mode = mode
        self._host._gizmo_center_w = (cx, cy)
        self._host._gizmo_snapshot = {
            eid: list(self._host._entities_by_id[eid].points)
            for eid in self._host._selected_ids()
        }

        def _meta_copy(eid: str) -> dict[str, Any] | None:
            meta = self._host._entities_by_id[eid].meta
            return dict(meta) if isinstance(meta, dict) else None

        self._host._gizmo_meta_snapshot = {
            eid: _meta_copy(eid) for eid in self._host._selected_ids()
        }
        self._host._gizmo_drag_moved = False
        self._host._gizmo_undo_pushed = False
        return bool(self._host._gizmo_snapshot)

    def _apply_handle_scale(
        self, wx: float, wy: float, mods: Qt.KeyboardModifier | None = None
    ) -> None:
        """Resize the selection by dragging a frame handle. Corners resize
        X and Y independently (Shift = keep aspect), edges scale one axis;
        holding Alt at press scales from the center."""
        if self._host._gizmo_anchor_w is None or self._host._gizmo_handle_w is None:
            return
        handle = (self._host._gizmo_drag_mode or "")[6:]
        if self._host._gizmo_local_shape is not None:
            self._apply_local_parametric_scale(handle, wx, wy, mods)
            return
        ax, ay = self._host._gizmo_anchor_w
        hx, hy = self._host._gizmo_handle_w

        if mods is None:
            mods = QApplication.keyboardModifiers()

        # Snap the dragged handle itself to nearby vertex/midpoint/edge/
        # center of other shapes (any layer) plus grid/guides — mirrors
        # move-drag snapping so resize feels consistent. Alt disables it.
        allow_snap = not bool(mods & Qt.KeyboardModifier.AltModifier)
        snap_result = self._host._resize_handle_snap_adjust(wx, wy) if allow_snap else None
        if snap_result is not None:
            wx, wy, snap_type = snap_result
            self._host._hover_snap = (wx, wy)
            self._host._hover_snap_type = snap_type
        else:
            self._host._hover_snap = None
            self._host._hover_snap_type = None

        def _factor(cur: float, a: float, h: float) -> float:
            span = h - a
            if abs(span) < 1e-9:
                return 1.0
            f = (cur - a) / span
            # Clamp magnitude only — preserve sign so dragging a handle past
            # the opposite edge flips the shape (mirrors it) instead of
            # getting stuck at a minimum positive scale.
            if abs(f) < 0.05:
                f = 0.05 if f >= 0.0 else -0.05
            return max(-20.0, min(20.0, f))

        sx = _factor(wx, ax, hx)
        sy = _factor(wy, ay, hy)
        if handle in ("n", "s"):
            sx = 1.0
        elif handle in ("e", "w"):
            sy = 1.0
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            # Shift = keep aspect (uniform, dominant axis wins)
            s = sx if abs(sx - 1.0) >= abs(sy - 1.0) else sy
            sx = sy = s
        if self._host._aspect_ratio_locked:
            # Persistent lock (properties panel toggle) overrides the
            # handle-type/Shift logic above — every handle, edge or
            # corner, scales both axes together from whichever one the
            # drag actually moved.
            if handle in ("n", "s"):
                sx = sy
            elif handle in ("e", "w"):
                sy = sx
            else:
                s = sx if abs(sx - 1.0) >= abs(sy - 1.0) else sy
                sx = sy = s
        if abs(sx - 1.0) > 1e-4 or abs(sy - 1.0) > 1e-4:
            self._host._gizmo_drag_moved = True
        if not self._host._gizmo_undo_pushed:
            self._host._gizmo_command_snapshot = self._host._canvas_service.begin_preview()
            self._host._gizmo_undo_pushed = True
        for eid, src_poly in self._host._gizmo_snapshot.items():
            self._host._entities_by_id[eid].points = [
                (ax + (x - ax) * sx, ay + (y - ay) * sy) for x, y in src_poly
            ]
            # Keep parametric meta (circle/ellipse/rectangle "center") in
            # sync with the resized points — otherwise centroid-based snap
            # targets stay stale at the shape's PRE-resize position, since
            # `_entity_center()` reads meta["center"] directly rather than
            # recomputing it from `.points`. Always derive from the drag-
            # start snapshot (never the live/already-updated meta) so
            # repeated mouse-move events don't compound the transform.
            snap_meta = self._host._gizmo_meta_snapshot.get(eid)
            if isinstance(snap_meta, dict):
                if abs(sx - sy) <= 1e-9:
                    new_meta = transform_meta(
                        self._host._entities_by_id[eid].kind,
                        snap_meta,
                        transform="scale",
                        center=(ax, ay),
                        factor=sx,
                    )
                    self._host._entities_by_id[eid].meta = (
                        new_meta if new_meta is not None else snap_meta
                    )
                else:
                    # A world-axis non-uniform scale can turn circles into
                    # ellipses and rotated rectangles into parallelograms.
                    # Those results cannot be represented truthfully by the
                    # original parametric schema. Keep the transformed points
                    # as canonical geometry instead of leaving stale metadata
                    # that redraw would use to restore the old shape.
                    self._host._entities_by_id[eid].kind = "polyline"
                    self._host._entities_by_id[eid].meta = None
        self._host._refresh_driving_dimensions()
        self._host.geometryChanged.emit()

    def _apply_local_parametric_scale(
        self, handle: str, wx: float, wy: float, mods: Qt.KeyboardModifier | None
    ) -> None:
        """Resize a rotated parametric shape in its own coordinate system."""
        state = self._host._gizmo_local_shape
        if state is None or self._host._gizmo_anchor_w is None:
            return
        eid = state["entity_id"]
        cx, cy = state["center"]
        angle = math.radians(-float(state["rotation"]))

        def _local(point: tuple[float, float]) -> tuple[float, float]:
            dx, dy = point[0] - cx, point[1] - cy
            return (
                dx * math.cos(angle) - dy * math.sin(angle),
                dx * math.sin(angle) + dy * math.cos(angle),
            )

        ax, ay = _local(self._host._gizmo_anchor_w)
        px, py = _local((wx, wy))
        width, height = float(state["width"]), float(state["height"])
        new_w = width if handle in {"n", "s"} else max(1e-3, abs(px - ax))
        new_h = height if handle in {"e", "w"} else max(1e-3, abs(py - ay))
        if state["from_center"]:
            new_w = width if handle in {"n", "s"} else max(1e-3, 2 * abs(px))
            new_h = height if handle in {"e", "w"} else max(1e-3, 2 * abs(py))
        if mods is not None and mods & Qt.KeyboardModifier.ShiftModifier:
            factor = max(new_w / width, new_h / height)
            new_w, new_h = width * factor, height * factor
        if state["x_key"] == state["y_key"]:
            diameter = new_w if handle in {"e", "w"} else new_h
            if len(handle) == 2:
                diameter = max(new_w, new_h)
            new_w = new_h = diameter
        candidate = deepcopy(self._host._entities_by_id[eid])
        x_value = new_w / 2.0 if state["x_key"] in {"rx", "radius"} else new_w
        y_value = new_h / 2.0 if state["y_key"] in {"ry", "radius"} else new_h
        update_entity_parameter(candidate, str(state["x_key"]), x_value)
        update_entity_parameter(candidate, str(state["y_key"]), y_value)
        if not state["from_center"]:
            local_center = (
                0.0 if handle in {"n", "s"} else (ax + px) / 2.0,
                0.0 if handle in {"e", "w"} else (ay + py) / 2.0,
            )
            forward = math.radians(float(state["rotation"]))
            target_center = (
                cx + local_center[0] * math.cos(forward) - local_center[1] * math.sin(forward),
                cy + local_center[0] * math.sin(forward) + local_center[1] * math.cos(forward),
            )
            old_center = candidate.meta.get("center", (cx, cy)) if candidate.meta else (cx, cy)
            dx, dy = target_center[0] - old_center[0], target_center[1] - old_center[1]
            candidate.points = [(x + dx, y + dy) for x, y in candidate.points]
            transform_entity_metadata(candidate, transform="translate", dx=dx, dy=dy)
        if not self._host._gizmo_undo_pushed:
            self._host._gizmo_command_snapshot = self._host._canvas_service.begin_preview()
            self._host._gizmo_undo_pushed = True
        self._host._update_entity_in_storage(candidate)
        self._host._gizmo_drag_moved = True
        self._host._sync_shape_storage_from_entities()
        self._host._refresh_driving_dimensions()
        self._host.geometryChanged.emit()

    def _apply_gizmo_drag(
        self, wx: float, wy: float, mods: Qt.KeyboardModifier | None = None
    ) -> None:
        if self._host._gizmo_drag_mode is None or not self._host._gizmo_snapshot:
            return
        if self._host._gizmo_drag_mode.startswith("scale-"):
            self._apply_handle_scale(wx, wy, mods)
            return
        if self._host._gizmo_center_w is None or self._host._gizmo_start_vec is None:
            return

        if not self._host._gizmo_undo_pushed:
            self._host._gizmo_command_snapshot = self._host._canvas_service.begin_preview()
            self._host._gizmo_undo_pushed = True

        cx, cy = self._host._gizmo_center_w
        start_vx, start_vy = self._host._gizmo_start_vec
        cur_vx, cur_vy = wx - cx, wy - cy

        scale = 1.0
        angle = 0.0
        if self._host._gizmo_drag_mode == "scale":
            start_d = math.hypot(start_vx, start_vy)
            cur_d = math.hypot(cur_vx, cur_vy)
            if start_d > 1e-9:
                scale = max(0.05, min(20.0, cur_d / start_d))
            if abs(scale - 1.0) > 1e-4:
                self._host._gizmo_drag_moved = True
        elif self._host._gizmo_drag_mode == "rotate":
            start_a = math.atan2(start_vy, start_vx)
            cur_a = math.atan2(cur_vy, cur_vx)
            angle = cur_a - start_a
            if mods is not None and mods & Qt.KeyboardModifier.ShiftModifier:
                increment = math.radians(self._host._rotation_snap_increment)
                angle = round(angle / increment) * increment
            if abs(angle) > math.radians(0.2):
                self._host._gizmo_drag_moved = True

        ca, sa = math.cos(angle), math.sin(angle)
        for eid, src_poly in self._host._gizmo_snapshot.items():
            out_poly: list[tuple[float, float]] = []
            for x, y in src_poly:
                sx = cx + (x - cx) * scale
                sy = cy + (y - cy) * scale
                rx = cx + (sx - cx) * ca - (sy - cy) * sa
                ry = cy + (sx - cx) * sa + (sy - cy) * ca
                out_poly.append((rx, ry))
            self._host._entities_by_id[eid].points = out_poly
            # Same staleness fix as _apply_handle_scale: recompute meta["center"]
            # under the identical scale+rotate transform, from the drag-start
            # snapshot, so circle/ellipse centroid snapping stays accurate
            # after a uniform corner-scale or rotate gizmo drag too.
            snap_meta = self._host._gizmo_meta_snapshot.get(eid)
            if isinstance(snap_meta, dict):
                new_meta = transform_meta(
                    self._host._entities_by_id[eid].kind,
                    snap_meta,
                    transform=("rotate" if self._host._gizmo_drag_mode == "rotate" else "scale"),
                    center=(cx, cy),
                    angle_deg=math.degrees(angle),
                    factor=scale,
                )
                self._host._entities_by_id[eid].meta = (
                    new_meta if new_meta is not None else snap_meta
                )
        self._host._refresh_driving_dimensions()
        self._host.geometryChanged.emit()

    def _end_gizmo_drag(self) -> bool:
        moved = self._host._gizmo_drag_moved
        if moved:
            self._host._canvas_service.commit_preview(self._host._gizmo_command_snapshot)
        self._host._gizmo_drag_mode = None
        self._host._gizmo_center_w = None
        self._host._gizmo_start_vec = None
        self._host._gizmo_anchor_w = None
        self._host._gizmo_handle_w = None
        self._host._gizmo_snapshot = {}
        self._host._gizmo_meta_snapshot = {}
        self._host._gizmo_local_shape = None
        self._host._gizmo_drag_moved = False
        self._host._gizmo_undo_pushed = False
        self._host._gizmo_command_snapshot = None
        self._host._hover_snap = None
        self._host._hover_snap_type = None
        return moved


# ════════════════════════════════════════════════════════════════════════════
# Grouping / ungrouping
# ════════════════════════════════════════════════════════════════════════════
