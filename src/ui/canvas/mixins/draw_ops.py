"""DrawOpsMixin family — draw-mode sidebar/shape-preview and smoothing
operations for PolylineView.

Two previously-separate mixins merged here (``DrawSidebarMixin``,
``SmoothingMixin``) — both are "draw-mode adjacent" operations: the sidebar
panel that drives draw-mode tool selection, and the smooth/simplify/
curve-fit operations typically invoked right after drawing/tracing.

PolylineView inherits these via
``class PolylineView(QWidget, CanvasRenderer, ..., DrawSidebarMixin,
SmoothingMixin)``. Since methods are resolved through the normal MRO, every
``self.*`` reference works without modification — same pattern as
``CanvasRenderer`` in ``render.py``.

This block (draw sidebar) was already self-documented in place as
"restored from pre-refactor _draw_mixin" before being re-split out of
``view.py`` — i.e. it used to be its own mixin before the commit-9a7d3a5
incident (a prior mixin split that silently dropped ~40 still-referenced
methods) and was inlined back into ``view.py`` during the manual recovery.
Every method here was verified to have zero external callers other than
``self``/other-mixin references before each move.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation
from shapely.errors import GEOSException
from shapely.geometry import LineString

from src.backend.geometry import (
    build_circle_poly,
    build_ellipse_poly,
    build_polygon_poly,
    build_rect_poly,
    build_rounded_rect_poly,
    build_star_poly,
    shape_slot,
)
from src.infra.settings import normalize_draw_sidebar_shape_tools
from src.ui.widgets.draw_sidebar import DrawSidebar

if TYPE_CHECKING:
    from typing import Protocol

    class _DrawSidebarHost(Protocol):
        """Structural view of the PolylineView state this mixin's methods
        read and write. See ``mixins/render.py``'s ``_RendererHost`` for why
        this exists (closes the type-checker gap from multiple inheritance
        without a real circular runtime dependency on view.py)."""

        _draw_sidebar: DrawSidebar | None
        _draw_sidebar_anim: QPropertyAnimation | None
        _draw_sidebar_visible: bool
        _draw_sidebar_always_visible: bool
        _draw_sidebar_width: int
        _draw_sidebar_height: int | None
        _draw_sidebar_sections: list[str]
        _draw_sidebar_path_tools: list[str]
        _draw_sidebar_shape_tools: list[str]
        _draw_pts: list[Any]
        _draw_point_snap_types: list[Any]
        _draw_snap: Any
        _draw_snap_type: Any
        _draw_constraint: Any
        _draw_constraint_lock: str | None
        _draw_arc_pts: list[Any]
        _draw_arc_mode: str
        _draw_polygon_sides: int
        _draw_star_points: int
        _draw_primitive: str
        _draw_split_enabled: bool
        _draw_shape_preview_active: bool
        _draw_shape_anchor_w: tuple[float, float] | None
        _draw_shape_cursor_w: tuple[float, float] | None
        _angle_snap_active: bool
        _pen_pts: list[Any]
        _pen_tangents: list[Any]
        _pen_dragging: bool
        _pen_press_screen: Any
        _smoothing_method: str
        _mode: str
        drawSidebarWidthChanged: Any
        drawSidebarHeightChanged: Any

        def _chrome_left(self) -> int: ...
        def _chrome_top(self) -> int: ...
        def height(self) -> int: ...
        def set_mode(self, mode: str) -> None: ...
        def toggle_dimension_mode(self) -> None: ...
        def _on_smoothing_method_changed(self, method: str) -> None: ...
        def _finish_draw(self, *, close: bool = False) -> None: ...
        def _key_backspace(self) -> None: ...
        def _dismiss_dim_inputs(self) -> None: ...
        def _append_draw_polyline(
            self, poly: list[tuple[float, float]], *, enter_edit: bool, kind: str, meta: Any
        ) -> None: ...
        def _show_flash(self, text: str, ms: int) -> None: ...
        def _redraw(self) -> None: ...
        def _update_shape_size_fields_from_preview(self) -> None: ...

    _DrawSidebarBase = _DrawSidebarHost
else:
    _DrawSidebarBase = object


class DrawSidebarMixin(_DrawSidebarBase):
    """Mixin providing the draw-mode sidebar panel + shape preview commit
    for :class:`PolylineView`.

    Do not instantiate directly — inherit alongside ``QWidget``.
    """

    def _build_draw_sidebar(self) -> None:
        was_visible = self._draw_sidebar_visible
        if self._draw_sidebar is not None:
            # Rebuild (e.g. the customize-sections dialog changed the
            # section list) — drop the old panel/animation cleanly first.
            self._draw_sidebar.hide()
            self._draw_sidebar.deleteLater()
            self._draw_sidebar = None
            self._draw_sidebar_anim = None

        panel = DrawSidebar(
            parent=self,
            on_polyline_family=self._on_polyline_family_change,
            on_shapes_family=self._on_shapes_family_change,
            on_text=lambda: self._set_draw_primitive("text"),
            on_arc_mode=self._on_arc_mode_change,
            on_constraint=self._on_constraint_change,
            on_split=self._on_split_change,
            on_dimension=self.toggle_dimension_mode,
            on_smoothing_method=self._on_smoothing_method_changed,
            on_finish_open=lambda: self._finish_draw(close=False),
            on_close_edit=lambda: self._finish_draw(close=True),
            on_undo_point=self._key_backspace,
            on_cancel_draw=self._cancel_draw_points,
            on_back_to_select=lambda: self.set_mode("select"),
            width=self._draw_sidebar_width,
            sections=self._draw_sidebar_sections,
            path_tools=self._draw_sidebar_path_tools,
            shape_tools=self._draw_sidebar_shape_tools,
            on_width_changed=self._on_draw_sidebar_width_changed,
            on_height_changed=self._on_draw_sidebar_height_changed,
        )
        panel.hide()

        anim = QPropertyAnimation(panel, b"pos", self)
        anim.setDuration(150)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._on_draw_sidebar_anim_finished)

        self._draw_sidebar = panel
        self._draw_sidebar_anim = anim
        self._refresh_draw_sidebar_state()
        if was_visible:
            self._set_draw_sidebar_visible(True, animate=False)

    def _on_draw_sidebar_width_changed(self, width: int) -> None:
        self._draw_sidebar_width = width
        self._layout_draw_sidebar()
        self.drawSidebarWidthChanged.emit(width)

    def set_draw_sidebar_width(self, width: int) -> None:
        """Apply a width from settings (app startup / another window
        resized it) without re-emitting drawSidebarWidthChanged."""
        self._draw_sidebar_width = width
        if self._draw_sidebar is not None:
            self._draw_sidebar._apply_width(width)
            self._layout_draw_sidebar()

    def _on_draw_sidebar_height_changed(self, height: int) -> None:
        self._draw_sidebar_height = height
        self._layout_draw_sidebar()
        self.drawSidebarHeightChanged.emit(height)

    def set_draw_sidebar_height(self, height: int | None) -> None:
        """Apply a height from settings (app startup / another window
        resized it) without re-emitting drawSidebarHeightChanged. ``None``
        reverts to auto-fitting the available space."""
        self._draw_sidebar_height = height
        if self._draw_sidebar is not None and height is not None:
            self._draw_sidebar._apply_height(height)
        self._layout_draw_sidebar()

    def set_draw_sidebar_sections(self, sections: list[str]) -> None:
        self._draw_sidebar_sections = list(sections)
        self._build_draw_sidebar()

    def set_draw_sidebar_path_tools(self, tools: list[str]) -> None:
        self._draw_sidebar_path_tools = list(tools)
        self._build_draw_sidebar()

    def set_draw_sidebar_shape_tools(self, tools: list[str]) -> None:
        self._draw_sidebar_shape_tools = normalize_draw_sidebar_shape_tools(tools)
        self._build_draw_sidebar()

    def set_draw_sidebar_always_visible(self, enabled: bool) -> None:
        self._draw_sidebar_always_visible = enabled
        self._set_draw_sidebar_visible(self._mode == "draw" or enabled)

    def _draw_sidebar_target_height(self, y: int) -> int:
        """Auto-fit height (available canvas space) unless the user has
        manually dragged the sidebar's own bottom-edge handle, in which
        case that override sticks until they resize it again."""
        if self._draw_sidebar_height is not None:
            return self._draw_sidebar_height
        return min(430, max(260, self.height() - y - 8))

    def _layout_draw_sidebar(self) -> None:
        if self._draw_sidebar is None:
            return
        left = self._chrome_left()
        top = self._chrome_top()
        y = top + 8
        self._draw_sidebar.setFixedHeight(self._draw_sidebar_target_height(y))
        x = left + 8 if self._draw_sidebar_visible else left - self._draw_sidebar.width() + 20
        self._draw_sidebar.move(x, y)

    def _set_draw_sidebar_visible(self, visible: bool, *, animate: bool = True) -> None:
        if self._draw_sidebar is None or self._draw_sidebar_anim is None:
            return
        if self._draw_sidebar_always_visible:
            visible = True
        if self._draw_sidebar_visible == visible and self._draw_sidebar.isVisible():
            self._refresh_draw_sidebar_state()
            return

        self._draw_sidebar_visible = visible
        self._refresh_draw_sidebar_state()
        left = self._chrome_left()
        y = self._chrome_top() + 8
        hidden_x = left - self._draw_sidebar.width() + 20
        shown_x = left + 8
        self._draw_sidebar.setFixedHeight(self._draw_sidebar_target_height(y))

        if not animate:
            if visible:
                self._draw_sidebar.show()
                self._draw_sidebar.move(shown_x, y)
            else:
                self._draw_sidebar.move(hidden_x, y)
                self._draw_sidebar.hide()
            return

        if visible:
            self._draw_sidebar.show()
            self._draw_sidebar.move(hidden_x, y)
            self._draw_sidebar_anim.stop()
            self._draw_sidebar_anim.setStartValue(QPoint(hidden_x, y))
            self._draw_sidebar_anim.setEndValue(QPoint(shown_x, y))
            self._draw_sidebar_anim.start()
        else:
            self._draw_sidebar_anim.stop()
            self._draw_sidebar_anim.setStartValue(self._draw_sidebar.pos())
            self._draw_sidebar_anim.setEndValue(QPoint(hidden_x, y))
            self._draw_sidebar_anim.start()

    def _refresh_draw_sidebar_state(self) -> None:
        if not isinstance(self._draw_sidebar, DrawSidebar):
            return
        has_pts = len(self._draw_pts)
        self._draw_sidebar.set_polyline_actions_enabled(
            can_finish=has_pts >= 2,
            can_close=has_pts >= 3,
            can_undo=has_pts >= 1,
        )
        self._draw_sidebar.set_split_enabled(self._draw_split_enabled)
        self._draw_sidebar.set_smoothing_method(self._smoothing_method)
        self._draw_sidebar.set_active_tool(self._draw_primitive)
        self._draw_sidebar.set_arc_mode(self._draw_arc_mode)
        self._draw_sidebar.set_arc_mode_enabled(self._draw_primitive == "arc")
        self._draw_sidebar.set_constraint_mode(self._draw_constraint_lock)
        self._draw_sidebar.set_constraint_mode_enabled(self._draw_primitive in {"line", "polyline"})
        self._update_shape_size_fields_from_preview()

    def _commit_shape_preview(self) -> bool:
        if not self._draw_shape_preview_active:
            return False
        if self._draw_shape_anchor_w is None or self._draw_shape_cursor_w is None:
            return False
        sx, sy = self._draw_shape_anchor_w
        ex, ey = self._draw_shape_cursor_w
        cx = (sx + ex) / 2.0
        cy = (sy + ey) / 2.0
        w = abs(ex - sx)
        h = abs(ey - sy)

        poly: list[tuple[float, float]] = []
        kind = "polyline"
        meta: dict[str, Any] | None = None
        if self._draw_primitive in {"rectangle", "rounded_rectangle"}:
            rounded = self._draw_primitive == "rounded_rectangle"
            radius = min(w, h) * 0.1
            poly = (
                build_rounded_rect_poly(cx, cy, w, h, radius)
                if rounded
                else build_rect_poly(cx, cy, w, h)
            )
            kind = self._draw_primitive
            meta = {
                "center": (cx, cy),
                "width": w,
                "height": h,
                "rotation": 0.0,
            }
            if rounded:
                meta["radius"] = radius
        elif self._draw_primitive == "circle":
            # Match preview behavior: first click is center, drag to radius.
            radius = math.hypot(ex - sx, ey - sy)
            poly = build_circle_poly(sx, sy, radius)
            kind = "circle"
            meta = {"center": (sx, sy), "radius": radius}
        elif self._draw_primitive == "ellipse":
            poly = build_ellipse_poly(cx, cy, w / 2.0, h / 2.0)
            kind = "ellipse"
            meta = {"center": (cx, cy), "rx": w / 2.0, "ry": h / 2.0, "rotation": 0.0}
        elif self._draw_primitive == "slot":
            poly = [(px + cx, py + cy) for px, py in shape_slot(w, h)]
            kind = "slot"
            meta = {"center": (cx, cy), "length": w, "width": h, "rotation": 0.0}
        elif self._draw_primitive == "polygon":
            # Center-first, matching circle: first click is center, drag
            # sets the radius directly (was previously bounding-box corner
            # to corner, unlike every other radius-based shape).
            radius = math.hypot(ex - sx, ey - sy)
            poly = build_polygon_poly(sx, sy, radius, self._draw_polygon_sides)
            kind = "polygon"
            meta = {
                "center": (sx, sy),
                "radius": radius,
                "sides": self._draw_polygon_sides,
                "rotation": 0.0,
            }
        elif self._draw_primitive == "star":
            radius = math.hypot(ex - sx, ey - sy)
            poly = build_star_poly(sx, sy, radius, self._draw_star_points)
            kind = "star"
            meta = {
                "center": (sx, sy),
                "radius": radius,
                "points": self._draw_star_points,
                "inner_ratio": 0.45,
                "rotation": -90.0,
            }

        self._draw_shape_preview_active = False
        self._draw_shape_anchor_w = None
        self._draw_shape_cursor_w = None

        if len(poly) >= 2:
            self._append_draw_polyline(poly, enter_edit=False, kind=kind, meta=meta)
            self._show_flash(f"{self._draw_primitive.title()} created", 800)
            self._refresh_draw_sidebar_state()
            self._redraw()
            return True

        self._refresh_draw_sidebar_state()
        self._redraw()
        return False

    def _on_draw_sidebar_anim_finished(self) -> None:
        if self._draw_sidebar is None:
            return
        if not self._draw_sidebar_visible:
            self._draw_sidebar.hide()

    def _on_polyline_family_change(self, tool: str) -> None:
        if self._mode != "draw":
            self.set_mode("draw")
        self._set_draw_primitive(tool)

    def _on_shapes_family_change(self, tool: str) -> None:
        if self._mode != "draw":
            self.set_mode("draw")
        self._set_draw_primitive(tool)

    def _on_arc_mode_change(self, mode: str) -> None:
        if self._draw_primitive != "arc":
            self._set_draw_primitive("arc")
        self._draw_arc_mode = mode
        self._draw_arc_pts.clear()
        self._refresh_draw_sidebar_state()
        self._show_flash(
            "Arc: center-start-end" if mode == "center-start-end" else "Arc: three-point",
            900,
        )
        self._redraw()

    def _on_constraint_change(self, mode: str) -> None:
        self._draw_constraint_lock = None if mode == "Free" else mode
        self._refresh_draw_sidebar_state()
        self._show_flash(
            f"Constraint: {self._draw_constraint_lock}"
            if self._draw_constraint_lock
            else "Constraint: Free",
            900,
        )
        self._redraw()

    def _on_split_change(self, enabled: bool) -> None:
        self._draw_split_enabled = enabled
        self._refresh_draw_sidebar_state()
        self._show_flash("Split: on" if enabled else "Split: off", 800)

    def _cancel_draw_points(self) -> None:
        if self._mode != "draw":
            return
        self._draw_pts.clear()
        self._draw_point_snap_types.clear()
        self._draw_snap = None
        self._draw_snap_type = None
        self._draw_constraint = None
        self._angle_snap_active = False
        self._draw_shape_preview_active = False
        self._draw_shape_anchor_w = None
        self._draw_shape_cursor_w = None
        self._draw_arc_pts.clear()
        if hasattr(self, "_dismiss_shape_dim_inputs"):
            self._dismiss_shape_dim_inputs()
        self._dismiss_dim_inputs()
        self._refresh_draw_sidebar_state()
        self._redraw()

    def _set_draw_primitive(self, tool: str) -> None:
        valid = {
            "polyline",
            "line",
            "arc",
            "spline",
            "rectangle",
            "rounded_rectangle",
            "circle",
            "ellipse",
            "polygon",
            "star",
            "slot",
            "text",
            "bezier",
        }
        if tool not in valid:
            return
        self._draw_primitive = tool
        self._draw_pts.clear()
        self._draw_arc_pts.clear()
        self._pen_pts.clear()
        self._pen_tangents.clear()
        self._pen_dragging = False
        self._pen_press_screen = None
        self._draw_shape_preview_active = False
        self._draw_shape_anchor_w = None
        self._draw_shape_cursor_w = None
        self._dismiss_dim_inputs()
        self._update_shape_size_fields_from_preview()
        self._refresh_draw_sidebar_state()
        self._show_flash(f"Tool: {tool}", 650)
        self._redraw()


# ════════════════════════════════════════════════════════════════════════════
# Smoothing / simplify / bezier curve-fit
# ════════════════════════════════════════════════════════════════════════════

if TYPE_CHECKING:
    from typing import Any, Protocol

    class _SmoothingHost(Protocol):
        """Structural view of the PolylineView state this mixin's methods
        read and write. See ``render.py``'s ``_RendererHost`` for why this
        exists (closes the type-checker gap from multiple inheritance
        without a real circular runtime dependency on view.py)."""

        _entities: list[Any]
        _smoothing_method: str
        _smooth_iterations: int
        _simplify_tolerance: float
        _sel: set[int]
        selectionChanged: Any
        smoothingMethodChanged: Any
        smoothIterationsChanged: Any
        simplifyToleranceChanged: Any

        def _mutable_selected_indices(self) -> list[int]: ...
        def _is_poly_closed(self, poly: list[tuple[float, float]]) -> bool: ...
        def _push_undo(self, coalesce: str | None = None) -> None: ...
        def _redraw(self) -> None: ...
        def _notify(self) -> None: ...
        def _fire_poly_change(self) -> None: ...
        def _refresh_draw_sidebar_state(self) -> None: ...
        def _show_flash(self, text: str, ms: int) -> None: ...

    _SmoothingBase = _SmoothingHost
else:
    _SmoothingBase = object


class SmoothingMixin(_SmoothingBase):
    """Mixin providing smoothing, simplification, and bezier curve-fit
    operations for :class:`PolylineView`.

    Do not instantiate directly — inherit alongside ``QWidget``.
    """

    def set_smoothing_method(self, method: str) -> None:
        """Set the algorithm smooth_selected() runs."""
        if method not in ("chaikin", "gaussian", "catmull_rom"):
            return
        self._smoothing_method = method
        # Piggyback on the existing signal so anything bound to the
        # selection (e.g. the properties panel) re-reads with the new unit,
        # even if the selection itself didn't change.
        self.selectionChanged.emit(len(self._sel))
        self._refresh_draw_sidebar_state()

    def _on_smoothing_method_changed(self, method: str) -> None:
        """User picked a smoothing method from this tab's sidebar — apply it
        here and let app.py persist + re-broadcast to every other tab."""
        self.set_smoothing_method(method)
        self.smoothingMethodChanged.emit(method)

    def set_smooth_iterations(self, iterations: int) -> None:
        """Apply a remembered iteration count from settings (startup, or
        echoed from another tab) without re-emitting the change signal."""
        self._smooth_iterations = int(iterations)

    def _on_smooth_iterations_changed(self, iterations: int) -> None:
        """User typed a new value into the Smooth HUD prompt — remember it
        here and let app.py persist + re-broadcast to every other tab."""
        self.set_smooth_iterations(iterations)
        self.smoothIterationsChanged.emit(self._smooth_iterations)

    def set_simplify_tolerance(self, tolerance: float) -> None:
        """Apply a remembered tolerance from settings (startup, or echoed
        from another tab) without re-emitting the change signal."""
        self._simplify_tolerance = float(tolerance)

    def _on_simplify_tolerance_changed(self, tolerance: float) -> None:
        """User typed a new value into the Simplify HUD prompt — remember it
        here and let app.py persist + re-broadcast to every other tab."""
        self.set_simplify_tolerance(tolerance)
        self.simplifyToleranceChanged.emit(self._simplify_tolerance)

    def smooth_selected(self, iterations: int = 2) -> int:
        """Smooth jagged selected polylines using the configured algorithm
        (Settings > Application Behavior > Smoothing method):

        - "chaikin" (default): repeated corner-cutting — pulls corners
          inward on each pass, so the curve approaches but never quite
          touches the original vertices. Vertices that turn sharper than
          ~110 degrees are treated as intentional cusps and left alone.
        - "gaussian": weighted-average of each vertex with its neighbors,
          repeated per iteration. Keeps the original vertex count instead
          of doubling it every pass.
        - "catmull_rom": resamples a centripetal spline that interpolates
          exactly through every original vertex; ``iterations`` controls
          resample density instead of pass count. The oversampled curve is
          decimated afterward so dense/near-collinear input doesn't turn
          into far more points than the shape actually needs.

        Closed shapes stay closed; open polylines keep their endpoints
        fixed. Returns the number of shapes smoothed.
        """
        indices = self._mutable_selected_indices()
        to_smooth = [i for i in indices if len(self._entities[i].points) >= 3]
        if not to_smooth:
            return 0
        self._push_undo()
        method = self._smoothing_method
        count = 0
        for idx in to_smooth:
            poly = self._entities[idx].points
            closed = self._is_poly_closed(poly)
            pts = poly[:-1] if closed else list(poly)
            if len(pts) < 3:
                continue
            if method == "gaussian":
                for _ in range(max(1, iterations)):
                    pts = self._gaussian_pass(pts, closed=closed)
            elif method == "catmull_rom":
                samples = max(2, int(iterations) * 4)
                pts = self._catmull_rom_smooth(pts, closed=closed, samples_per_segment=samples)
            else:
                for _ in range(max(1, iterations)):
                    pts = self._chaikin_pass(pts, closed=closed)
            if closed:
                pts.append(pts[0])
            self._entities[idx].points = pts
            # Smoothing invalidates any parametric meta (circle/arc/rect/…);
            # the result is a plain freeform polyline.
            self._entities[idx].kind = "polyline"
            self._entities[idx].meta = None
            count += 1
        if count == 0:
            return 0
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return count

    @staticmethod
    def _chaikin_pass(
        pts: list[tuple[float, float]], *, closed: bool, corner_angle: float = 110.0
    ) -> list[tuple[float, float]]:
        """One Chaikin corner-cutting pass: replaces each corner with two
        points 1/4 and 3/4 along its adjoining segments — except vertices
        that turn sharper than ``corner_angle`` degrees, which read as
        intentional cusps/points (e.g. lettering serifs) rather than jagged
        noise, so they pass through unmodified instead of being rounded
        off. Open-curve endpoints are always preserved."""
        n = len(pts)

        def is_sharp(i: int) -> bool:
            if closed:
                prev, curr, nxt = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
            elif i == 0 or i == n - 1:
                return True
            else:
                prev, curr, nxt = pts[i - 1], pts[i], pts[i + 1]
            v1x, v1y = curr[0] - prev[0], curr[1] - prev[1]
            v2x, v2y = nxt[0] - curr[0], nxt[1] - curr[1]
            len1, len2 = math.hypot(v1x, v1y), math.hypot(v2x, v2y)
            if len1 < 1e-9 or len2 < 1e-9:
                return False
            cos_turn = max(-1.0, min(1.0, (v1x * v2x + v1y * v2y) / (len1 * len2)))
            return math.degrees(math.acos(cos_turn)) > corner_angle

        seg_count = n if closed else n - 1
        out: list[tuple[float, float]] = []
        for i in range(seg_count):
            i1 = (i + 1) % n
            p0, p1 = pts[i], pts[i1]
            near = p0 if is_sharp(i) else (p0[0] * 0.75 + p1[0] * 0.25, p0[1] * 0.75 + p1[1] * 0.25)
            far = p1 if is_sharp(i1) else (p0[0] * 0.25 + p1[0] * 0.75, p0[1] * 0.25 + p1[1] * 0.75)
            out.append(near)
            out.append(far)

        # Preserved corners can make adjoining edges emit the same vertex
        # twice in a row (once as a "far" point, once as the next edge's
        # "near" point) — collapse those exact duplicates.
        deduped: list[tuple[float, float]] = []
        for p in out:
            if not deduped or math.hypot(p[0] - deduped[-1][0], p[1] - deduped[-1][1]) > 1e-9:
                deduped.append(p)
        return deduped

    # 5-tap Gaussian kernel (sigma ~= 0.85), offsets -2..2.
    _GAUSSIAN_KERNEL: ClassVar[tuple[float, ...]] = (
        0.06136,
        0.24477,
        0.38774,
        0.24477,
        0.06136,
    )

    @classmethod
    def _gaussian_pass(
        cls, pts: list[tuple[float, float]], *, closed: bool
    ) -> list[tuple[float, float]]:
        """One Gaussian-smoothing pass: replaces each vertex with a weighted
        average of itself and its neighbors. Unlike Chaikin, vertex count
        stays fixed; open-curve endpoints are left untouched."""
        n = len(pts)
        kernel = cls._GAUSSIAN_KERNEL
        radius = len(kernel) // 2
        out: list[tuple[float, float]] = []
        for i in range(n):
            if not closed and (i == 0 or i == n - 1):
                out.append(pts[i])
                continue
            sx = sy = wsum = 0.0
            for k, w in enumerate(kernel):
                j = i + (k - radius)
                if closed:
                    j %= n
                elif j < 0 or j >= n:
                    continue
                sx += pts[j][0] * w
                sy += pts[j][1] * w
                wsum += w
            out.append((sx / wsum, sy / wsum))
        return out

    @staticmethod
    def _centripetal_point(
        p0: tuple[float, float],
        p1: tuple[float, float],
        p2: tuple[float, float],
        p3: tuple[float, float],
        u: float,
    ) -> tuple[float, float]:
        """One point on a centripetal (alpha=0.5) Catmull-Rom segment
        between control points p1 and p2, at local parameter u in [0, 1]
        (Barry-Goldman formulation). Centripetal parameterization — knot
        spacing by sqrt(chord length) rather than a flat step — is what
        keeps the curve from looping/overshooting near sharp turns when
        input points are unevenly spaced, which hand-traced polylines
        almost always are."""

        def knot(t_prev: float, a: tuple[float, float], b: tuple[float, float]) -> float:
            d = math.hypot(b[0] - a[0], b[1] - a[1])
            return t_prev + max(d, 1e-6) ** 0.5

        t0 = 0.0
        t1 = knot(t0, p0, p1)
        t2 = knot(t1, p1, p2)
        t3 = knot(t2, p2, p3)
        t = t1 + u * (t2 - t1)

        def lerp(
            a: tuple[float, float],
            b: tuple[float, float],
            ta: float,
            tb: float,
            at_t: float,
        ) -> tuple[float, float]:
            span = tb - ta
            f = 0.0 if span <= 1e-9 else (at_t - ta) / span
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)

        a1 = lerp(p0, p1, t0, t1, t)
        a2 = lerp(p1, p2, t1, t2, t)
        a3 = lerp(p2, p3, t2, t3, t)
        b1 = lerp(a1, a2, t0, t2, t)
        b2 = lerp(a2, a3, t1, t3, t)
        return lerp(b1, b2, t1, t2, t)

    @staticmethod
    def _catmull_rom_smooth(
        pts: list[tuple[float, float]],
        *,
        closed: bool,
        samples_per_segment: int = 8,
    ) -> list[tuple[float, float]]:
        """Resample a centripetal Catmull-Rom spline through ``pts``: unlike
        the Chaikin and Gaussian passes, the result interpolates exactly
        through every original vertex instead of pulling corners inward.
        Returns an (unclosed) point list; the caller re-appends the closure
        point for closed shapes, matching ``_chaikin_pass``'s convention.

        The raw oversampled curve is then decimated with a small
        Douglas-Peucker pass — dense, near-collinear input (typical of
        hand traces) otherwise produces far more points than the curve
        actually needs, since every original segment gets the same fixed
        sample count regardless of how short or straight it already is.
        """
        n = len(pts)
        if n < 3:
            return list(pts)

        def at(i: int) -> tuple[float, float]:
            if closed:
                return pts[i % n]
            return pts[max(0, min(n - 1, i))]

        out: list[tuple[float, float]] = []
        seg_count = n if closed else n - 1
        total_len = 0.0
        for i in range(seg_count):
            p0, p1, p2, p3 = at(i - 1), at(i), at(i + 1), at(i + 2)
            total_len += math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            for s in range(samples_per_segment):
                u = s / samples_per_segment
                out.append(SmoothingMixin._centripetal_point(p0, p1, p2, p3, u))
        if not closed:
            out.append(pts[-1])

        avg_spacing = total_len / max(1, seg_count)
        tolerance = max(avg_spacing * 0.05, 1e-4)
        try:
            simplified = LineString(out).simplify(tolerance, preserve_topology=False)
            return [(float(x), float(y)) for x, y in simplified.coords]
        except (GEOSException, ValueError):
            return out

    def simplify_selected(self, tolerance: float = 0.2) -> int:
        """Reduce vertex count on selected polylines via Douglas-Peucker
        simplification (Shapely), preserving overall shape within
        ``tolerance`` mm. Closed shapes stay closed. Returns the number of
        shapes actually simplified (unchanged/degenerate results are
        skipped).
        """
        indices = self._mutable_selected_indices()
        to_simplify = [i for i in indices if len(self._entities[i].points) >= 3]
        if not to_simplify:
            return 0
        self._push_undo()
        count = 0
        for idx in to_simplify:
            poly = self._entities[idx].points
            closed = self._is_poly_closed(poly)
            try:
                simplified = LineString(poly).simplify(tolerance, preserve_topology=False)
            except (GEOSException, ValueError):
                continue
            coords = [(float(x), float(y)) for x, y in simplified.coords]
            if len(coords) < 2:
                continue
            if closed and coords[0] != coords[-1]:
                coords.append(coords[0])
            if len(coords) >= len(poly):
                continue  # no reduction achieved — leave the shape as-is
            self._entities[idx].points = coords
            # Simplification invalidates any parametric meta (circle/arc/
            # rect/…); the result is a plain freeform polyline.
            self._entities[idx].kind = "polyline"
            self._entities[idx].meta = None
            count += 1
        if count == 0:
            return 0
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return count

    def fit_selected_to_curve(self, tolerance: float = 0.3, corner_angle_deg: float = 55.0) -> int:
        """Convert selected polylines into smooth, editable bezier curves —
        far fewer control points than a dense/jagged point cloud (e.g. from
        Trace), with real corners (sharp turns) kept sharp instead of
        rounded off. See ``fit_polyline_to_bezier`` for the fitting
        approach and its precision tradeoff vs. a strict least-squares fit.
        Returns the number of shapes actually converted.
        """
        from src.backend.geometry import fit_polyline_to_bezier

        indices = self._mutable_selected_indices()
        to_fit = [i for i in indices if len(self._entities[i].points) >= 3]
        if not to_fit:
            return 0
        self._push_undo()
        count = 0
        for idx in to_fit:
            poly = self._entities[idx].points
            closed = self._is_poly_closed(poly)
            result = fit_polyline_to_bezier(
                poly, tolerance=tolerance, corner_angle_deg=corner_angle_deg, closed=closed
            )
            if result is None:
                continue
            anchors, tangents = result
            self._entities[idx].points = anchors
            self._entities[idx].kind = "bezier"
            self._entities[idx].meta = {
                "tangents": tangents,
                "segments": 16,
                "closed": closed,
            }
            count += 1
        if count == 0:
            return 0
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return count
