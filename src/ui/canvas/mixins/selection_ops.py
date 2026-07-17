"""SelectionOpsMixin family — clipboard, gizmo drag, and grouping
operations for PolylineView.

Three previously-separate mixins merged here (``ClipboardMixin``,
``GizmoDragMixin``, ``GroupingMixin``) — all are "operations on the current
selection" (copy/paste/duplicate, resize-handle drag, group/ungroup), each
individually small enough that a dedicated file didn't pay for itself.

PolylineView inherits these via
``class PolylineView(QWidget, CanvasRenderer, ..., ClipboardMixin,
GizmoDragMixin, GroupingMixin)``. Since methods are resolved through the
normal MRO, every ``self.*`` reference works without modification — same
pattern as ``CanvasRenderer`` in ``render.py``.

Extracted from ``view.py`` originally as part of shrinking that file's
~7,200 lines. Every method here was verified to have zero external callers
other than ``self``/other-mixin references before the move (the whole-
codebase grep this repo's git history shows a prior "mixin-inlining"
refactor silently dropped ~40 still-referenced methods — see commit
9a7d3a5 — so this file exists specifically to NOT repeat that).
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from src.backend.shapes import transform_meta
from src.ui.canvas.geometry_model import transform_entity_metadata, update_entity_parameter

# Process-wide clipboard (shared across every canvas instance — Pattern,
# Draft, Trace, Convert, plus any additional windows) so copy in one tab and
# paste in another actually round-trips — a plain per-instance list would
# silently paste nothing across tabs.
_SHARED_CLIPBOARD: list[dict[str, Any]] = []

if TYPE_CHECKING:
    from typing import Protocol

    from src.ui.canvas.contracts import CanvasModelHost

    class _ClipboardHost(CanvasModelHost, Protocol):
        """Structural view of the PolylineView state this mixin's methods
        read and write. See ``render.py``'s ``_RendererHost`` for why this
        exists (closes the type-checker gap from multiple inheritance
        without a real circular runtime dependency on view.py)."""

        _next_group_id: int
        _undo_store: Any

        def _is_locked(self, idx: int) -> bool: ...
        def _append_entity(
            self, poly: list[tuple[float, float]], *, kind: str = ..., meta: Any = ...
        ) -> int: ...
        def _translated_entity_meta(self, kind: str, meta: Any, dx: float, dy: float) -> Any: ...
        def _compact_entities(self, drop: set[int]) -> None: ...
        def _transform_entity_meta(self, idx: int, **kwargs: Any) -> None: ...
        def _show_flash(self, text: str, ms: int) -> None: ...
        def _show_hud_prompt(self, *args: Any, **kwargs: Any) -> None: ...
        def _set_repeat_action(self, label: str, callback: Any) -> None: ...

    _ClipboardBase = _ClipboardHost
else:
    _ClipboardBase = object


class ClipboardMixin(_ClipboardBase):
    """Mixin providing copy/cut/paste/duplicate for :class:`PolylineView`.

    Do not instantiate directly — inherit alongside ``QWidget``.
    """

    @property
    def _clipboard(self) -> list[dict[str, Any]]:
        """Process-wide clipboard (module-level _SHARED_CLIPBOARD) so
        copy/paste works across tabs and windows, not just within the one
        canvas that did the copying."""
        return _SHARED_CLIPBOARD

    @_clipboard.setter
    def _clipboard(self, value: list[dict[str, Any]]) -> None:
        _SHARED_CLIPBOARD[:] = value

    def _copy_selected(self) -> None:
        if not self._sel:
            return
        self._clipboard = []
        for i in sorted(self._sel):
            if i >= len(self._entities):
                continue
            self._clipboard.append(
                {
                    "polyline": list(self._entities[i].points),
                    "kind": self._entities[i].kind,
                    "meta": deepcopy(self._entities[i].meta)
                    if self._entities[i].meta is not None
                    else None,
                    "construction": self._entities[i].construction,
                    "group": self._entities[i].group,
                }
            )

    def _paste_records(self, dx: float, dy: float | None = None) -> list[int]:
        """Append clipboard records translated by ``(dx, dy)``; grouped
        sources stay grouped in the copy (each source group maps to a fresh
        group id). ``dy`` defaults to ``dx`` (diagonal offset)."""
        if dy is None:
            dy = dx
        new_indices: list[int] = []
        gid_map: dict[int, int] = {}
        for record in getattr(self, "_clipboard", []):
            poly = list(record.get("polyline", []))
            new_poly = [(x + dx, y + dy) for x, y in poly]
            kind = str(record.get("kind", "polyline"))
            meta = self._translated_entity_meta(kind, record.get("meta"), dx, dy)
            new_idx = self._append_entity(new_poly, kind=kind, meta=meta)
            if record.get("construction"):
                self._entities[new_idx].construction = True
            src_gid = record.get("group")
            if src_gid is not None:
                if src_gid not in gid_map:
                    gid_map[src_gid] = self._next_group_id
                    self._next_group_id += 1
                self._entities[new_idx].group = gid_map[src_gid]
            new_indices.append(new_idx)
        return new_indices

    def _paste_clipboard(self) -> None:
        if not getattr(self, "_clipboard", []):
            return
        self._push_undo()
        new_indices = self._paste_records(1.0)
        self._sel = set(new_indices)
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _duplicate_selected(self) -> None:
        if not self._sel:
            return
        self._copy_selected()
        self._paste_clipboard()

    def _duplicate_selected_with_offset(self) -> None:
        if not self._sel or not self._entities:
            return
        min_x, max_x, min_y, max_y = (
            float("inf"),
            float("-inf"),
            float("inf"),
            float("-inf"),
        )
        for idx in self._sel:
            if idx < len(self._entities):
                for x, y in self._entities[idx].points:
                    min_x = min(min_x, x)
                    max_x = max(max_x, x)
                    min_y = min(min_y, y)
                    max_y = max(max_y, y)
        width = max_x - min_x if max_x > min_x else 10.0
        height = max_y - min_y if max_y > min_y else 10.0
        offset = max(2.0, min(width, height) * 0.1)
        self._copy_selected()
        self._paste_clipboard_with_offset(offset)

    def _paste_clipboard_with_offset(self, offset: float) -> None:
        if not getattr(self, "_clipboard", []):
            return
        self._push_undo()
        new_indices = self._paste_records(offset)
        self._sel = set(new_indices)
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _finish_array_duplicate(self, all_new: list[int]) -> None:
        self._sel = set(all_new)
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _array_duplicate_grid(self) -> None:
        """Prompt for columns/rows/spacing, then lay out a grid of copies
        of the current selection (the selection itself occupies cell 0,0)."""
        if not self._sel:
            self._show_flash("Select shape(s) first", 1000)
            return

        def _got_cols(cols: float) -> None:
            n_cols = max(1, int(round(cols)))

            def _got_rows(rows: float) -> None:
                n_rows = max(1, int(round(rows)))

                def _got_spacing(spacing: float) -> None:
                    self._apply_grid_array(n_cols, n_rows, spacing)

                self._show_hud_prompt("Spacing (mm)", 10.0, _got_spacing, minimum=0.01)

            self._show_hud_prompt("Rows", 2.0, _got_rows, minimum=1, is_length=False)

        self._show_hud_prompt("Columns", 2.0, _got_cols, minimum=1, is_length=False)

    def _apply_grid_array(self, n_cols: int, n_rows: int, spacing: float) -> bool:
        if not self._sel:
            self._show_flash("Select shape(s) first", 1000)
            return False
        if n_cols * n_rows <= 1:
            self._show_flash("Nothing to duplicate (1×1 grid)", 1200)
            return False
        self._copy_selected()
        if not getattr(self, "_clipboard", []):
            return False
        self._push_undo()
        all_new: list[int] = []
        for row in range(n_rows):
            for col in range(n_cols):
                if row == 0 and col == 0:
                    continue
                all_new.extend(self._paste_records(col * spacing, row * spacing))
        self._finish_array_duplicate(all_new)
        self._set_repeat_action(
            f"Grid array {n_cols}×{n_rows}",
            lambda: self._apply_grid_array(n_cols, n_rows, spacing),
        )
        return True

    def _array_duplicate_radial(self) -> None:
        """Prompt for copy count/radius, then place copies of the current
        selection at evenly-spaced points on a circle (translation only —
        copies are not rotated to face outward)."""
        if not self._sel:
            self._show_flash("Select shape(s) first", 1000)
            return

        def _got_copies(copies: float) -> None:
            n = max(1, int(round(copies)))

            def _got_radius(radius: float) -> None:
                self._apply_radial_array(n, radius)

            self._show_hud_prompt("Radius (mm)", 20.0, _got_radius, minimum=0.01)

        self._show_hud_prompt("Copies", 6.0, _got_copies, minimum=1, is_length=False)

    def _apply_radial_array(self, count: int, radius: float) -> bool:
        if not self._sel:
            self._show_flash("Select shape(s) first", 1000)
            return False
        if count <= 1:
            self._show_flash("Nothing to duplicate (need ≥ 2 copies)", 1200)
            return False
        self._copy_selected()
        if not getattr(self, "_clipboard", []):
            return False
        self._push_undo()
        all_new: list[int] = []
        for i in range(1, count):
            angle = 2.0 * math.pi * i / count
            dx = radius * math.cos(angle)
            dy = radius * math.sin(angle)
            all_new.extend(self._paste_records(dx, dy))
        self._finish_array_duplicate(all_new)
        self._set_repeat_action(
            f"Radial array ×{count}",
            lambda: self._apply_radial_array(count, radius),
        )
        return True

    def _array_duplicate_along_path(self) -> None:
        """Distribute copies of selected source shapes along the longest selected path."""
        selected = [index for index in sorted(self._sel) if index < len(self._entities)]
        if len(selected) < 2:
            self._show_flash("Select source shape(s) and one path", 1400)
            return

        def _length(index: int) -> float:
            points = self._entities[index].points
            return sum(math.dist(a, b) for a, b in zip(points, points[1:]))

        path_index = max(selected, key=_length)
        path = list(self._entities[path_index].points)
        source_indices = [index for index in selected if index != path_index]
        if len(path) < 2 or _length(path_index) <= 1e-9 or not source_indices:
            self._show_flash("Selected path has no usable length", 1400)
            return

        def _got_count(value: float) -> None:
            count = max(2, int(round(value)))
            segments = [(a, b, math.dist(a, b)) for a, b in zip(path, path[1:])]
            total = sum(length for _, _, length in segments)
            source_points = [
                point for index in source_indices for point in self._entities[index].points
            ]
            origin_x = (
                min(point[0] for point in source_points) + max(point[0] for point in source_points)
            ) / 2.0
            origin_y = (
                min(point[1] for point in source_points) + max(point[1] for point in source_points)
            ) / 2.0
            original_selection = set(self._sel)
            self._sel = set(source_indices)
            self._copy_selected()
            self._sel = original_selection
            self._push_undo()
            created: list[int] = []
            for copy_index in range(count):
                target_distance = total * copy_index / (count - 1)
                walked = 0.0
                target = path[-1]
                for start, end, length in segments:
                    if walked + length >= target_distance:
                        ratio = (target_distance - walked) / length if length else 0.0
                        target = (
                            start[0] + (end[0] - start[0]) * ratio,
                            start[1] + (end[1] - start[1]) * ratio,
                        )
                        break
                    walked += length
                created.extend(self._paste_records(target[0] - origin_x, target[1] - origin_y))
            self._finish_array_duplicate(created)
            self._show_flash(f"Array along path: {count} positions", 1200)

        self._show_hud_prompt("Copies along path", 6.0, _got_count, minimum=2, is_length=False)

    def _cut_selected(self) -> None:
        if not self._sel:
            return
        cut_set = {idx for idx in self._sel if not self._is_locked(idx)}
        if not cut_set:
            return
        self._copy_selected()
        self._push_undo()
        self._compact_entities(cut_set)
        self._sel.clear()
        self._redraw()
        self._notify()
        self._fire_poly_change()

    def _nudge_selected(self, dx: float, dy: float) -> None:
        if not self._sel:
            return
        mutable = [idx for idx in self._sel if not self._is_locked(idx)]
        if not mutable:
            return
        self._push_undo(coalesce="nudge")
        QTimer.singleShot(500, self._undo_store.break_coalescing)
        for idx in mutable:
            if idx < len(self._entities):
                self._entities[idx].points = [
                    (x + dx, y + dy) for x, y in self._entities[idx].points
                ]
                self._transform_entity_meta(
                    idx,
                    center=(0.0, 0.0),
                    kind=self._entities[idx].kind,
                    meta=self._entities[idx].meta,
                    transform="translate",
                    dx=dx,
                    dy=dy,
                )
        self._redraw()
        self._notify()
        self._fire_poly_change()


# ════════════════════════════════════════════════════════════════════════════
# Gizmo drag (resize handles / rotate-scale)
# ════════════════════════════════════════════════════════════════════════════

if TYPE_CHECKING:
    from typing import Protocol

    class _GizmoHost(Protocol):
        """Structural view of the PolylineView state this mixin's methods
        read and write. See ``render.py``'s ``_RendererHost`` for why this
        exists (closes the type-checker gap from multiple inheritance
        without a real circular runtime dependency on view.py)."""

        _sel: set[int]
        _entities: list[Any]
        _aspect_ratio_locked: bool
        _gizmo_anchor_w: tuple[float, float] | None
        _gizmo_handle_w: tuple[float, float] | None
        _gizmo_drag_mode: str | None
        _gizmo_center_w: tuple[float, float] | None
        _gizmo_start_vec: tuple[float, float] | None
        _gizmo_snapshot: dict[int, list[tuple[float, float]]]
        _gizmo_meta_snapshot: dict[int, dict[str, Any] | None]
        _gizmo_drag_moved: bool
        _gizmo_undo_pushed: bool
        _hover_snap: tuple[float, float] | None
        _hover_snap_type: str | None

        def _selection_bounds(
            self,
        ) -> tuple[float, float, float, float] | None: ...
        def _mutable_selected_indices(self) -> list[int]: ...
        def _resize_handle_snap_adjust(
            self, wx: float, wy: float
        ) -> tuple[float, float, str] | None: ...
        def _push_undo(self, coalesce: str | None = None) -> None: ...

    _GizmoBase = _GizmoHost
else:
    _GizmoBase = object


class GizmoDragMixin(_GizmoBase):
    """Mixin providing the resize-handle / rotate-scale gizmo drag for
    :class:`PolylineView`.

    Do not instantiate directly — inherit alongside ``QWidget``.
    """

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
        bounds = self._selection_bounds()
        if bounds is None or not self._sel:
            return False
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        self._gizmo_local_shape = None
        if mode.startswith("scale-"):
            frac_a, frac_h = self._HANDLE_ANCHORS[mode[6:]]
            if from_center:
                frac_a = (0.5, 0.5)
            indices = self._mutable_selected_indices()
            entity = self._entities[indices[0]] if len(indices) == 1 else None
            meta = entity.meta if entity is not None and isinstance(entity.meta, dict) else None
            dims = None
            if entity is not None and meta is not None:
                if entity.kind in {"rectangle", "rounded_rectangle"}:
                    dims = (float(meta.get("width", 0)), float(meta.get("height", 0)), "width", "height")
                elif entity.kind == "ellipse":
                    dims = (2 * float(meta.get("rx", 0)), 2 * float(meta.get("ry", 0)), "rx", "ry")
                elif entity.kind == "circle":
                    diameter = 2 * float(meta.get("radius", 0))
                    dims = (diameter, diameter, "radius", "radius")
                elif entity.kind == "slot":
                    dims = (float(meta.get("length", 0)), float(meta.get("width", 0)), "length", "width")
            if dims is not None and min(dims[0], dims[1]) > 1e-9:
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

                self._gizmo_anchor_w = _world(frac_a)
                self._gizmo_handle_w = _world(frac_h)
                self._gizmo_local_shape = {
                    "index": indices[0], "center": (cx, cy), "rotation": rotation,
                    "width": dims[0], "height": dims[1], "x_key": dims[2], "y_key": dims[3],
                    "from_center": from_center,
                }
            else:
                x0, y0, x1, y1 = bounds
                self._gizmo_anchor_w = (x0 + (x1 - x0) * frac_a[0], y0 + (y1 - y0) * frac_a[1])
                self._gizmo_handle_w = (x0 + (x1 - x0) * frac_h[0], y0 + (y1 - y0) * frac_h[1])
        else:
            vec = (wx - cx, wy - cy)
            if math.hypot(vec[0], vec[1]) < 1e-9:
                return False
            self._gizmo_start_vec = vec
        self._gizmo_drag_mode = mode
        self._gizmo_center_w = (cx, cy)
        self._gizmo_snapshot = {
            idx: list(self._entities[idx].points) for idx in self._mutable_selected_indices()
        }

        def _meta_copy(idx: int) -> dict[str, Any] | None:
            meta = self._entities[idx].meta
            return dict(meta) if isinstance(meta, dict) else None

        self._gizmo_meta_snapshot = {
            idx: _meta_copy(idx) for idx in self._mutable_selected_indices()
        }
        self._gizmo_drag_moved = False
        self._gizmo_undo_pushed = False
        return bool(self._gizmo_snapshot)

    def _apply_handle_scale(
        self, wx: float, wy: float, mods: Qt.KeyboardModifier | None = None
    ) -> None:
        """Resize the selection by dragging a frame handle. Corners resize
        X and Y independently (Shift = keep aspect), edges scale one axis;
        holding Alt at press scales from the center."""
        if self._gizmo_anchor_w is None or self._gizmo_handle_w is None:
            return
        handle = (self._gizmo_drag_mode or "")[6:]
        if self._gizmo_local_shape is not None:
            self._apply_local_parametric_scale(handle, wx, wy, mods)
            return
        ax, ay = self._gizmo_anchor_w
        hx, hy = self._gizmo_handle_w

        if mods is None:
            mods = QApplication.keyboardModifiers()

        # Snap the dragged handle itself to nearby vertex/midpoint/edge/
        # center of other shapes (any layer) plus grid/guides — mirrors
        # move-drag snapping so resize feels consistent. Alt disables it.
        allow_snap = not bool(mods & Qt.KeyboardModifier.AltModifier)
        snap_result = self._resize_handle_snap_adjust(wx, wy) if allow_snap else None
        if snap_result is not None:
            wx, wy, snap_type = snap_result
            self._hover_snap = (wx, wy)
            self._hover_snap_type = snap_type
        else:
            self._hover_snap = None
            self._hover_snap_type = None

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
        if self._aspect_ratio_locked:
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
            self._gizmo_drag_moved = True
        if not self._gizmo_undo_pushed:
            self._push_undo()
            self._gizmo_undo_pushed = True
        for idx, src_poly in self._gizmo_snapshot.items():
            self._entities[idx].points = [
                (ax + (x - ax) * sx, ay + (y - ay) * sy) for x, y in src_poly
            ]
            # Keep parametric meta (circle/ellipse/rectangle "center") in
            # sync with the resized points — otherwise centroid-based snap
            # targets stay stale at the shape's PRE-resize position, since
            # `_entity_center()` reads meta["center"] directly rather than
            # recomputing it from `.points`. Always derive from the drag-
            # start snapshot (never the live/already-updated meta) so
            # repeated mouse-move events don't compound the transform.
            snap_meta = self._gizmo_meta_snapshot.get(idx)
            if isinstance(snap_meta, dict):
                if abs(sx - sy) <= 1e-9:
                    new_meta = transform_meta(
                        self._entities[idx].kind,
                        snap_meta,
                        transform="scale",
                        center=(ax, ay),
                        factor=sx,
                    )
                    self._entities[idx].meta = new_meta if new_meta is not None else snap_meta
                else:
                    # A world-axis non-uniform scale can turn circles into
                    # ellipses and rotated rectangles into parallelograms.
                    # Those results cannot be represented truthfully by the
                    # original parametric schema. Keep the transformed points
                    # as canonical geometry instead of leaving stale metadata
                    # that redraw would use to restore the old shape.
                    self._entities[idx].kind = "polyline"
                    self._entities[idx].meta = None
        self.geometryChanged.emit()

    def _apply_local_parametric_scale(
        self, handle: str, wx: float, wy: float, mods: Qt.KeyboardModifier | None
    ) -> None:
        """Resize a rotated parametric shape in its own coordinate system."""
        state = self._gizmo_local_shape
        if state is None or self._gizmo_anchor_w is None:
            return
        idx = int(state["index"])
        cx, cy = state["center"]
        angle = math.radians(-float(state["rotation"]))

        def _local(point: tuple[float, float]) -> tuple[float, float]:
            dx, dy = point[0] - cx, point[1] - cy
            return (dx * math.cos(angle) - dy * math.sin(angle), dx * math.sin(angle) + dy * math.cos(angle))

        ax, ay = _local(self._gizmo_anchor_w)
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
        candidate = deepcopy(self._entities[idx])
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
        if not self._gizmo_undo_pushed:
            self._push_undo()
            self._gizmo_undo_pushed = True
        self._entities[idx] = candidate
        self._gizmo_drag_moved = True
        self._sync_shape_storage_from_entities()
        self.geometryChanged.emit()

    def _apply_gizmo_drag(
        self, wx: float, wy: float, mods: Qt.KeyboardModifier | None = None
    ) -> None:
        if self._gizmo_drag_mode is None or not self._gizmo_snapshot:
            return
        if self._gizmo_drag_mode.startswith("scale-"):
            self._apply_handle_scale(wx, wy, mods)
            return
        if self._gizmo_center_w is None or self._gizmo_start_vec is None:
            return

        if not self._gizmo_undo_pushed:
            self._push_undo()
            self._gizmo_undo_pushed = True

        cx, cy = self._gizmo_center_w
        start_vx, start_vy = self._gizmo_start_vec
        cur_vx, cur_vy = wx - cx, wy - cy

        scale = 1.0
        angle = 0.0
        if self._gizmo_drag_mode == "scale":
            start_d = math.hypot(start_vx, start_vy)
            cur_d = math.hypot(cur_vx, cur_vy)
            if start_d > 1e-9:
                scale = max(0.05, min(20.0, cur_d / start_d))
            if abs(scale - 1.0) > 1e-4:
                self._gizmo_drag_moved = True
        elif self._gizmo_drag_mode == "rotate":
            start_a = math.atan2(start_vy, start_vx)
            cur_a = math.atan2(cur_vy, cur_vx)
            angle = cur_a - start_a
            if mods is not None and mods & Qt.KeyboardModifier.ShiftModifier:
                increment = math.radians(15.0)
                angle = round(angle / increment) * increment
            if abs(angle) > math.radians(0.2):
                self._gizmo_drag_moved = True

        ca, sa = math.cos(angle), math.sin(angle)
        for idx, src_poly in self._gizmo_snapshot.items():
            out_poly: list[tuple[float, float]] = []
            for x, y in src_poly:
                sx = cx + (x - cx) * scale
                sy = cy + (y - cy) * scale
                rx = cx + (sx - cx) * ca - (sy - cy) * sa
                ry = cy + (sx - cx) * sa + (sy - cy) * ca
                out_poly.append((rx, ry))
            self._entities[idx].points = out_poly
            # Same staleness fix as _apply_handle_scale: recompute meta["center"]
            # under the identical scale+rotate transform, from the drag-start
            # snapshot, so circle/ellipse centroid snapping stays accurate
            # after a uniform corner-scale or rotate gizmo drag too.
            snap_meta = self._gizmo_meta_snapshot.get(idx)
            if isinstance(snap_meta, dict):
                new_meta = transform_meta(
                    self._entities[idx].kind,
                    snap_meta,
                    transform=("rotate" if self._gizmo_drag_mode == "rotate" else "scale"),
                    center=(cx, cy),
                    angle_deg=math.degrees(angle),
                    factor=scale,
                )
                self._entities[idx].meta = new_meta if new_meta is not None else snap_meta
        self.geometryChanged.emit()

    def _end_gizmo_drag(self) -> bool:
        moved = self._gizmo_drag_moved
        self._gizmo_drag_mode = None
        self._gizmo_center_w = None
        self._gizmo_start_vec = None
        self._gizmo_anchor_w = None
        self._gizmo_handle_w = None
        self._gizmo_snapshot = {}
        self._gizmo_meta_snapshot = {}
        self._gizmo_local_shape = None
        self._gizmo_drag_moved = False
        self._gizmo_undo_pushed = False
        self._hover_snap = None
        self._hover_snap_type = None
        return moved


# ════════════════════════════════════════════════════════════════════════════
# Grouping / ungrouping
# ════════════════════════════════════════════════════════════════════════════


class GroupingMixin:
    """Mixin for managing entity grouping and ungrouping within a canvas."""

    def _group_of(self, idx: int) -> int | None:
        return self._entities[idx].group if 0 <= idx < len(self._entities) else None

    def _group_map(self) -> dict[int, int]:
        """{entity index: group id} for grouped entities."""
        return {i: e.group for i, e in enumerate(self._entities) if e.group is not None}

    def _group_selected(self) -> None:

        if len(self._sel) < 2:
            self._show_flash("Select 2+ shapes to group", 1000)
            return
        self._push_undo()
        gid = self._next_group_id
        self._next_group_id += 1
        for idx in self._sel:
            self._entities[idx].group = gid
        self._show_flash(f"Grouped {len(self._sel)} shapes", 900)
        self._notify()
        self._fire_poly_change()

    def set_group_label(self, gid: int, label: str) -> None:
        label = str(label).strip()
        if label:
            self._group_labels[int(gid)] = label
        else:
            self._group_labels.pop(int(gid), None)
        self._notify()
        self._fire_poly_change()

    def _ungroup_selected(self) -> None:
        ungrouped: set[int] = set()
        for idx in self._sel:
            gid = self._group_of(idx)
            if gid is not None:
                ungrouped.add(gid)
        if not ungrouped:
            return
        self._push_undo()
        # Dissolve all members of affected groups (covers both selected and
        # unselected entities sharing those group ids).
        for e in self._entities:
            if e.group in ungrouped:
                e.group = None
        for gid in ungrouped:
            self._group_labels.pop(gid, None)
        self._show_flash("Ungrouped", 700)
        self._notify()
        self._fire_poly_change()

    def group_indices(self, indices: list[int]) -> int:
        """Group the entities at ``indices`` (from layer tree). Returns count."""
        valid = [i for i in indices if 0 <= i < len(self._entities)]
        if len(valid) < 2:
            self._show_flash("Select 2+ shapes to group", 1000)
            return 0
        self._push_undo()
        gid = self._next_group_id
        self._next_group_id += 1
        for idx in valid:
            self._entities[idx].group = gid
        self._show_flash(f"Grouped {len(valid)} shapes", 900)
        self._sel = set(valid)
        self._notify()
        self._fire_poly_change()
        return len(valid)

    def ungroup_indices(self, indices: list[int]) -> int:
        """Ungroup the entities at ``indices`` (from layer tree). Returns count."""
        self._push_undo()
        ungrouped_gids: set[int] = set()
        valid = [i for i in indices if 0 <= i < len(self._entities)]
        for idx in valid:
            gid = self._group_of(idx)
            if gid is not None:
                ungrouped_gids.add(gid)
                self._entities[idx].group = None
        if not ungrouped_gids:
            self._show_flash("Shapes are not grouped", 700)
            return 0
        # Dissolve all members of affected groups.
        for e in self._entities:
            if e.group in ungrouped_gids:
                e.group = None
        for gid in ungrouped_gids:
            self._group_labels.pop(gid, None)
        self._show_flash("Ungrouped", 700)
        self._notify()
        self._fire_poly_change()
        return len(valid)
