"""Draw-tool state, quick shapes, HUD text, and text geometry services.

Single implementation home for the creation-side canvas operations composed
by the canvas view: draw-sidebar state, procedural quick shapes, dimension
HUD prompts, and text entity creation with font management.
"""

from __future__ import annotations

import logging
import math
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics, QPainterPath
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QSpinBox, QWidget

from simple_stipple.canvas.widgets.draw_sidebar import DrawSidebar
from simple_stipple.core.cad.geometry import (
    build_circle_poly,
    build_ellipse_poly,
    build_polygon_poly,
    build_rect_poly,
    build_rounded_rect_poly,
    build_star_poly,
    shape_circle,
    shape_polygon,
    shape_rect,
    shape_slot,
)
from simple_stipple.core.document.model import EntityRecord
from simple_stipple.platform.settings import normalize_draw_sidebar_shape_tools, user_data_dir
from simple_stipple.ui.components.feedback import refresh_style
from simple_stipple.ui.components.units import (
    parse_numeric_expression as _parse_expression,
)
from simple_stipple.ui.components.units import (
    suffix as _unit_suffix,
)
from simple_stipple.ui.components.units import (
    to_display as _to_display,
)

LOGGER = logging.getLogger(__name__)


class DrawOpsService:
    """Own draw sidebar widgets, tool state, and shape-preview commits."""

    def __init__(self, host) -> None:
        self._host = host

    def _build_draw_sidebar(self) -> None:
        was_visible = self._host._draw_sidebar_visible
        if self._host._draw_sidebar is not None:
            # Rebuild (e.g. the customize-sections dialog changed the
            # section list) — drop the old panel/animation cleanly first.
            self._host._draw_sidebar.hide()
            self._host._draw_sidebar.deleteLater()
            self._host._draw_sidebar = None
            self._host._draw_sidebar_anim = None

        panel = DrawSidebar(
            parent=self._host,
            on_polyline_family=self._on_polyline_family_change,
            on_shapes_family=self._on_shapes_family_change,
            on_text=lambda: self._set_draw_primitive("text"),
            on_arc_mode=self._on_arc_mode_change,
            on_constraint=self._on_constraint_change,
            on_split=self._on_split_change,
            on_dimension=self._host.toggle_dimension_mode,
            on_smoothing_method=self._host._on_smoothing_method_changed,
            on_finish_open=lambda: self._host._finish_draw(close=False),
            on_close_edit=lambda: self._host._finish_draw(close=True),
            on_undo_point=self._host._key_backspace,
            on_cancel_draw=self._cancel_draw_points,
            on_back_to_select=lambda: self._host.set_mode("select"),
            width=self._host._draw_sidebar_width,
            sections=self._host._draw_sidebar_sections,
            path_tools=self._host._draw_sidebar_path_tools,
            shape_tools=self._host._draw_sidebar_shape_tools,
            on_width_changed=self._on_draw_sidebar_width_changed,
            on_height_changed=self._on_draw_sidebar_height_changed,
        )
        panel.hide()

        anim = QPropertyAnimation(panel, b"pos", self._host)
        anim.setDuration(150)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._on_draw_sidebar_anim_finished)

        self._host._draw_sidebar = panel
        self._host._draw_sidebar_anim = anim
        self._host._refresh_draw_sidebar_state()
        if was_visible:
            self._set_draw_sidebar_visible(True, animate=False)

    def _on_draw_sidebar_width_changed(self, width: int) -> None:
        self._host._draw_sidebar_width = width
        self._layout_draw_sidebar()
        self._host.drawSidebarWidthChanged.emit(width)

    def set_draw_sidebar_width(self, width: int) -> None:
        """Apply a width from settings (app startup / another window
        resized it) without re-emitting drawSidebarWidthChanged."""
        self._host._draw_sidebar_width = width
        if self._host._draw_sidebar is not None:
            self._host._draw_sidebar._apply_width(width)
            self._layout_draw_sidebar()

    def _on_draw_sidebar_height_changed(self, height: int) -> None:
        self._host._draw_sidebar_height = height
        self._layout_draw_sidebar()
        self._host.drawSidebarHeightChanged.emit(height)

    def set_draw_sidebar_height(self, height: int | None) -> None:
        """Apply a height from settings (app startup / another window
        resized it) without re-emitting drawSidebarHeightChanged. ``None``
        reverts to auto-fitting the available space."""
        self._host._draw_sidebar_height = height
        if self._host._draw_sidebar is not None and height is not None:
            self._host._draw_sidebar._apply_height(height)
        self._layout_draw_sidebar()

    def set_draw_sidebar_sections(self, sections: list[str]) -> None:
        self._host._draw_sidebar_sections = list(sections)
        self._build_draw_sidebar()

    def set_draw_sidebar_path_tools(self, tools: list[str]) -> None:
        self._host._draw_sidebar_path_tools = list(tools)
        self._build_draw_sidebar()

    def set_draw_sidebar_shape_tools(self, tools: list[str]) -> None:
        self._host._draw_sidebar_shape_tools = normalize_draw_sidebar_shape_tools(tools)
        self._build_draw_sidebar()

    def set_draw_sidebar_always_visible(self, enabled: bool) -> None:
        self._host._draw_sidebar_always_visible = enabled
        self._set_draw_sidebar_visible(self._host._mode == "draw" or enabled)

    def _draw_sidebar_target_height(self, y: int) -> int:
        """Auto-fit height (available canvas space) unless the user has
        manually dragged the sidebar's own bottom-edge handle, in which
        case that override sticks until they resize it again."""
        if self._host._draw_sidebar_height is not None:
            return self._host._draw_sidebar_height
        return min(430, max(260, self._host.height() - y - 8))

    def _draw_sidebar_y(self) -> int:
        """Place the drawer below both the ruler and the canvas toolbar."""
        # Host chrome only reserves ruler pixels. The canvas toolbar is
        # painted above the scene as well, so anchoring at +8 caused the
        # drawer title to overlap its active-mode/guidance text.
        return self._host._chrome_top() + 60

    def _layout_draw_sidebar(self) -> None:
        if self._host._draw_sidebar is None:
            return
        left = self._host._chrome_left()
        y = self._draw_sidebar_y()
        self._host._draw_sidebar.setFixedHeight(self._draw_sidebar_target_height(y))
        x = (
            left + 8
            if self._host._draw_sidebar_visible
            else left - self._host._draw_sidebar.width() + 20
        )
        self._host._draw_sidebar.move(x, y)

    def _set_draw_sidebar_visible(self, visible: bool, *, animate: bool = True) -> None:
        if self._host._draw_sidebar is None or self._host._draw_sidebar_anim is None:
            return
        if self._host._draw_sidebar_always_visible:
            visible = True
        if self._host._draw_sidebar_visible == visible and self._host._draw_sidebar.isVisible():
            self._host._refresh_draw_sidebar_state()
            return

        self._host._draw_sidebar_visible = visible
        self._host._refresh_draw_sidebar_state()
        left = self._host._chrome_left()
        y = self._draw_sidebar_y()
        hidden_x = left - self._host._draw_sidebar.width() + 20
        shown_x = left + 8
        self._host._draw_sidebar.setFixedHeight(self._draw_sidebar_target_height(y))
        app = QApplication.instance()
        if app is not None and app.property("reducedMotion"):
            animate = False

        if not animate:
            if visible:
                self._host._draw_sidebar.show()
                self._host._draw_sidebar.move(shown_x, y)
            else:
                self._host._draw_sidebar.move(hidden_x, y)
                self._host._draw_sidebar.hide()
            return

        if visible:
            self._host._draw_sidebar.show()
            self._host._draw_sidebar.move(hidden_x, y)
            self._host._draw_sidebar_anim.stop()
            self._host._draw_sidebar_anim.setStartValue(QPoint(hidden_x, y))
            self._host._draw_sidebar_anim.setEndValue(QPoint(shown_x, y))
            self._host._draw_sidebar_anim.start()
        else:
            self._host._draw_sidebar_anim.stop()
            self._host._draw_sidebar_anim.setStartValue(self._host._draw_sidebar.pos())
            self._host._draw_sidebar_anim.setEndValue(QPoint(hidden_x, y))
            self._host._draw_sidebar_anim.start()

    def _refresh_draw_sidebar_state(self) -> None:
        if not isinstance(self._host._draw_sidebar, DrawSidebar):
            return
        has_pts = len(self._host._draw_pts)
        self._host._draw_sidebar.set_polyline_actions_enabled(
            can_finish=has_pts >= 2,
            can_close=has_pts >= 3,
            can_undo=has_pts >= 1,
        )
        self._host._draw_sidebar.set_split_enabled(self._host._draw_split_enabled)
        self._host._draw_sidebar.set_smoothing_method(self._host._smoothing_method)
        self._host._draw_sidebar.set_active_tool(self._host._draw_primitive)
        self._host._draw_sidebar.set_arc_mode(self._host._draw_arc_mode)
        self._host._draw_sidebar.set_arc_mode_enabled(self._host._draw_primitive == "arc")
        self._host._draw_sidebar.set_constraint_mode(self._host._draw_constraint_lock)
        self._host._draw_sidebar.set_constraint_mode_enabled(
            self._host._draw_primitive in {"line", "polyline"}
        )
        self._host._update_shape_size_fields_from_preview()

    def _commit_shape_preview(self) -> bool:
        if not self._host._draw_shape_preview_active:
            return False
        if self._host._draw_shape_anchor_w is None or self._host._draw_shape_cursor_w is None:
            return False
        sx, sy = self._host._draw_shape_anchor_w
        ex, ey = self._host._draw_shape_cursor_w
        cx = (sx + ex) / 2.0
        cy = (sy + ey) / 2.0
        w = abs(ex - sx)
        h = abs(ey - sy)

        poly: list[tuple[float, float]] = []
        paths: list[list[tuple[float, float]]] = []
        kind = "polyline"
        meta: dict[str, Any] | None = None
        if self._host._draw_primitive in {"rectangle", "rounded_rectangle"}:
            rounded = self._host._draw_primitive == "rounded_rectangle"
            radius = min(w, h) * 0.1
            poly = (
                build_rounded_rect_poly(cx, cy, w, h, radius)
                if rounded
                else build_rect_poly(cx, cy, w, h)
            )
            kind = self._host._draw_primitive
            meta = {
                "center": (cx, cy),
                "width": w,
                "height": h,
                "rotation": 0.0,
            }
            if rounded:
                meta["radius"] = radius
        elif self._host._draw_primitive == "circle":
            # Match preview behavior: first click is center, drag to radius.
            radius = math.hypot(ex - sx, ey - sy)
            poly = build_circle_poly(sx, sy, radius)
            kind = "circle"
            meta = {"center": (sx, sy), "radius": radius}
        elif self._host._draw_primitive == "ellipse":
            poly = build_ellipse_poly(cx, cy, w / 2.0, h / 2.0)
            kind = "ellipse"
            meta = {"center": (cx, cy), "rx": w / 2.0, "ry": h / 2.0, "rotation": 0.0}
        elif self._host._draw_primitive == "slot":
            poly = [(px + cx, py + cy) for px, py in shape_slot(w, h)]
            kind = "slot"
            meta = {"center": (cx, cy), "length": w, "width": h, "rotation": 0.0}
        elif self._host._draw_primitive == "polygon":
            # Center-first, matching circle: first click is center, drag
            # sets the radius directly (was previously bounding-box corner
            # to corner, unlike every other radius-based shape).
            radius = math.hypot(ex - sx, ey - sy)
            poly = build_polygon_poly(sx, sy, radius, self._host._draw_polygon_sides)
            kind = "polygon"
            meta = {
                "center": (sx, sy),
                "radius": radius,
                "sides": self._host._draw_polygon_sides,
                "rotation": 0.0,
            }
        elif self._host._draw_primitive == "star":
            radius = math.hypot(ex - sx, ey - sy)
            poly = build_star_poly(sx, sy, radius, self._host._draw_star_points)
            kind = "star"
            meta = {
                "center": (sx, sy),
                "radius": radius,
                "points": self._host._draw_star_points,
                "inner_ratio": 0.45,
                "rotation": -90.0,
            }
        elif self._host._draw_primitive in getattr(self._host, "_PROCEDURAL_QUICK_SHAPES", set()):
            paths = self._host._build_drag_procedural_shapes(
                self._host._draw_primitive, w, h, cx, cy
            )
            poly = paths[0] if paths else []
            kind = self._host._draw_primitive
            meta = {
                "generator": kind,
                "center": (cx, cy),
                "width": w,
                "height": h,
            }

        self._host._draw_shape_preview_active = False
        self._host._draw_shape_anchor_w = None
        self._host._draw_shape_cursor_w = None

        if len(poly) >= 2:
            if len(paths) > 1:
                group = self._host._next_group_id
                entities = [
                    EntityRecord(
                        points=points,
                        kind=kind,
                        meta=meta,
                        group=group,
                        layer=self._host._active_layer,
                        construction=self._host._draw_construction_mode,
                    )
                    for points in paths
                ]
                self._host._canvas_service.create_entities(entities)
                self._host._document.selection = {entity.id for entity in entities}
                self._host._notify()
                self._host._fire_poly_change()
                self._host._show_flash(f"{kind.title()} created", 800)
                self._host._refresh_draw_sidebar_state()
                self._host._redraw()
                return True
            if (
                self._host._draw_split_enabled
                and not self._host._draw_construction_mode
                and self._host._is_poly_closed(poly)
            ):
                before = self._host._canvas_service.begin_preview()
                carved, count = self._host._carve_geometry_with_shape(poly)
                if carved:
                    self._host._entities.append(
                        EntityRecord(
                            points=list(poly),
                            kind=kind,
                            meta=meta,
                            layer=self._host._active_layer,
                        )
                    )
                    self._host._document.selection = {self._host._entities[-1].id}
                    self._host._canvas_service.commit_preview(before)
                    self._host._notify()
                    self._host._fire_poly_change()
                    self._host._show_flash(f"Carved {count} region(s)", 1000)
                    self._host._refresh_draw_sidebar_state()
                    self._host._redraw()
                    return True
            self._host._append_draw_polyline(poly, enter_edit=False, kind=kind, meta=meta)
            self._host._show_flash(f"{self._host._draw_primitive.title()} created", 800)
            self._host._refresh_draw_sidebar_state()
            self._host._redraw()
            return True

        self._host._refresh_draw_sidebar_state()
        self._host._redraw()
        return False

    def _on_draw_sidebar_anim_finished(self) -> None:
        if self._host._draw_sidebar is None:
            return
        if not self._host._draw_sidebar_visible:
            self._host._draw_sidebar.hide()

    def _on_polyline_family_change(self, tool: str) -> None:
        if self._host._mode != "draw":
            self._host.set_mode("draw")
        self._set_draw_primitive(tool)

    def _on_shapes_family_change(self, tool: str) -> None:
        if self._host._mode != "draw":
            self._host.set_mode("draw")
        self._set_draw_primitive(tool)

    def _on_arc_mode_change(self, mode: str) -> None:
        if self._host._draw_primitive != "arc":
            self._set_draw_primitive("arc")
        self._host._draw_arc_mode = mode
        self._host._draw_arc_pts.clear()
        self._host._refresh_draw_sidebar_state()
        self._host._show_flash(
            "Arc: center-start-end" if mode == "center-start-end" else "Arc: three-point",
            900,
        )
        self._host._redraw()

    def _on_constraint_change(self, mode: str) -> None:
        self._host._draw_constraint_lock = None if mode == "Free" else mode
        self._host._refresh_draw_sidebar_state()
        self._host._show_flash(
            f"Constraint: {self._host._draw_constraint_lock}"
            if self._host._draw_constraint_lock
            else "Constraint: Free",
            900,
        )
        self._host._redraw()

    def _on_split_change(self, enabled: bool) -> None:
        self._host._draw_split_enabled = enabled
        self._host._refresh_draw_sidebar_state()
        self._host._show_flash("Split: on" if enabled else "Split: off", 800)

    def _cancel_draw_points(self) -> None:
        if self._host._mode != "draw":
            return
        self._host._draw_pts.clear()
        self._host._draw_point_snap_types.clear()
        self._host._draw_snap = None
        self._host._draw_snap_type = None
        self._host._draw_constraint = None
        self._host._angle_snap_active = False
        self._host._draw_shape_preview_active = False
        self._host._draw_shape_anchor_w = None
        self._host._draw_shape_cursor_w = None
        self._host._draw_arc_pts.clear()
        if hasattr(self, "_dismiss_shape_dim_inputs"):
            self._host._dismiss_shape_dim_inputs()
        self._host._dismiss_dim_inputs()
        self._host._refresh_draw_sidebar_state()
        self._host._redraw()

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
        valid.update(getattr(self._host, "_PROCEDURAL_QUICK_SHAPES", set()))
        if tool not in valid:
            return
        self._host._draw_primitive = tool
        self._host._snap_engine.clear_relationship_reference()
        self._host._draw_pts.clear()
        self._host._draw_arc_pts.clear()
        self._host._pen_pts.clear()
        self._host._pen_tangents.clear()
        self._host._pen_dragging = False
        self._host._pen_press_screen = None
        self._host._draw_shape_preview_active = False
        self._host._draw_shape_anchor_w = None
        self._host._draw_shape_cursor_w = None
        self._host._dismiss_dim_inputs()
        self._host._update_shape_size_fields_from_preview()
        self._host._refresh_draw_sidebar_state()
        self._host._show_flash(f"Tool: {tool}", 650)
        self._host._redraw()


# Construction and constraint state adaptation


def mode_from_modifiers(host: Any, modifiers) -> str:
    if modifiers & Qt.KeyboardModifier.AltModifier:
        return "circle"
    if modifiers & Qt.KeyboardModifier.ControlModifier:
        return "slot"
    return host._quick_shape_mode


def start_drag(host: Any, mode: str, position) -> None:
    pos = position.toPoint()
    wx, wy = host._c2w(position.x(), position.y())
    host._shape_drag_active = True
    host._shape_drag_mode = mode
    host._shape_start_w = (wx, wy)
    host._shape_start_c = pos
    host._shape_end_c = pos


def translate(coords: list[tuple[float, float]], cx: float, cy: float) -> list[tuple[float, float]]:
    return [(x + cx, y + cy) for x, y in coords]


def build_shapes(
    host: Any, mode: str, sx: float, sy: float, ex: float, ey: float
) -> list[list[tuple[float, float]]]:
    width = abs(ex - sx)
    height = abs(ey - sy)
    if width < 1e-6 or height < 1e-6:
        return []
    cx = (sx + ex) / 2.0
    cy = (sy + ey) / 2.0
    if mode == "rectangle":
        return [translate(shape_rect(width, height), cx, cy)]
    if mode == "circle":
        return [translate(shape_circle(min(width, height) / 2.0, 64), cx, cy)]
    if mode == "slot":
        return [translate(shape_slot(max(width, height), min(width, height)), cx, cy)]
    if mode == "hexagon":
        return [translate(shape_polygon(6, min(width, height) / 2.0), cx, cy)]
    if mode in host._PROCEDURAL_QUICK_SHAPES:
        return build_procedural_shapes(mode, width, height, cx, cy)
    return []


def build_procedural_shapes(
    mode: str, width: float, height: float, cx: float, cy: float
) -> list[list[tuple[float, float]]]:
    """Scale a procedural primitive into the drag bounds, preserving holes."""
    from simple_stipple.core.cad.primitives import (
        chamfered_star,
        dovetail_box,
        finger_joint_box,
        gear,
        keyhole,
        ring,
        rounded_star,
        spiral,
        superellipse,
        tabbed_panel,
        teardrop,
    )

    generators = {
        "gear": lambda: [gear()],
        "spiral": lambda: [spiral()],
        "superellipse": lambda: [superellipse()],
        "teardrop": lambda: [teardrop()],
        "keyhole": lambda: [keyhole()],
        "ring": lambda: list(ring()),
        "rounded_star": lambda: [rounded_star()],
        "chamfered_star": lambda: [chamfered_star()],
        "finger_joint_box": lambda: [finger_joint_box()],
        "dovetail_box": lambda: [dovetail_box()],
        "tabbed_panel": lambda: [tabbed_panel()],
    }
    paths = generators[mode]()
    points = [point for path in paths for point in path]
    min_x, max_x = min(point[0] for point in points), max(point[0] for point in points)
    min_y, max_y = min(point[1] for point in points), max(point[1] for point in points)
    scale_x = width / max(max_x - min_x, 1e-9)
    scale_y = height / max(max_y - min_y, 1e-9)
    source_cx = (min_x + max_x) / 2.0
    source_cy = (min_y + max_y) / 2.0
    return [
        [
            ((point[0] - source_cx) * scale_x + cx, (point[1] - source_cy) * scale_y + cy)
            for point in path
        ]
        for path in paths
    ]


class HudTextService:
    """Own transient HUD widgets and dimension input state."""

    def __init__(self, host) -> None:
        self._host = host

    def _show_flash(self, text: str, duration_ms: int = 1200) -> None:
        """Show a brief flash indicator on the canvas."""
        from simple_stipple.ui.components.feedback import record_notification

        record_notification(text)
        settings = getattr(self._host, "_settings", {})
        if settings.get("persistent_notifications"):
            duration_ms = max(duration_ms, 5000)
        elif settings.get("reduced_motion"):
            duration_ms = min(duration_ms, 700)
        self._host._flash_text = text
        if self._host._cursor_wx is not None and self._host._cursor_wy is not None:
            self._host._flash_anchor_c = self._host._w2c(
                self._host._cursor_wx, self._host._cursor_wy
            )
        else:
            bounds = self._host._selection_bounds()
            self._host._flash_anchor_c = (
                self._host._w2c((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)
                if bounds is not None
                else None
            )
        if self._host._flash_timer is not None:
            self._host._flash_timer.stop()
        self._host._flash_timer = QTimer(cast("QWidget", self._host))
        self._host._flash_timer.setSingleShot(True)
        self._host._flash_timer.timeout.connect(self._clear_flash)
        self._host._flash_timer.start(duration_ms)
        self._host._redraw()

    def _clear_flash(self) -> None:
        self._host._flash_text = None
        self._host._flash_anchor_c = None
        self._host._flash_timer = None
        self._host._redraw()

    # ── Auto-dimension HUD (Fusion 360 style) ──────────────────────────────

    def _make_hud_edit(
        self,
        placeholder: str = "",
        width: int = 80,
        height: int = 26,
        align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignCenter,
    ) -> QLineEdit:
        """Create a styled HUD QLineEdit parented to the canvas.

        Redesigned for better visibility and usability:
        - Larger, more touchable targets (26px height)
        - Modern dark theme with subtle borders
        - Monospace font for precise number reading
        """
        edit = QLineEdit(cast("QWidget", self._host))
        edit.setFixedWidth(max(width, 76))
        edit.setFixedHeight(max(height, 30))
        edit.setAlignment(align)
        edit.setProperty("role", "canvas-hud-input")
        edit.setAccessibleName(placeholder or "Canvas numeric input")
        edit.setAccessibleDescription("Type a value, press Enter to apply, or Escape to cancel")

        if placeholder:
            edit.setPlaceholderText(placeholder)
        edit.textEdited.connect(lambda _text: self._clear_hud_error(edit))
        edit.installEventFilter(cast("QWidget", self._host))
        edit.show()
        return edit

    def _make_hud_spinbox(
        self,
        *,
        minimum: int,
        maximum: int,
        value: int,
        width: int = 86,
        height: int = 24,
    ) -> QSpinBox:
        """Create a styled HUD QSpinBox (native up/down arrows + typing +
        Up/Down keys) parented to the canvas, matching _make_hud_edit's
        look. Used for live-adjustable integer parameters like polygon
        side count, where the built-in valueChanged signal gives a
        step-and-see-it-update interaction for free."""
        spin = QSpinBox(cast("QWidget", self._host))
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setFixedWidth(max(width, 76))
        spin.setFixedHeight(max(height, 30))
        spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spin.setProperty("role", "canvas-hud-input")
        spin.setAccessibleName("Canvas numeric stepper")
        spin.setAccessibleDescription("Type a value or use the arrow keys to adjust it")
        spin.installEventFilter(cast("QWidget", self._host))
        spin.show()
        return spin

    @staticmethod
    def _clear_hud_error(widget: QLineEdit) -> None:
        """Clear stale validation chrome as soon as the user corrects input."""
        if widget.property("error"):
            widget.setProperty("error", False)
            refresh_style(widget)

    def _show_hud_prompt(
        self,
        label: str,
        default: float,
        callback,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
        is_length: bool = True,
        preview=None,
    ) -> None:
        """Inline numeric prompt anchored to the active drawing context: Enter commits,
        Escape dismisses. Replaces modal QInputDialog for canvas ops.

        ``is_length`` marks *default*/the parsed value as an mm length that
        should round-trip through the active display unit (mm/in) — pass
        ``False`` for non-length values (angles, counts) so they're shown
        and returned as-is.
        """
        self._dismiss_hud_prompt()
        unit = self._host._unit_system if is_length else None
        display_label = label.replace("mm", _unit_suffix(unit)) if unit else label
        display_default = _to_display(default, unit) if unit else default
        edit = self._make_hud_edit(placeholder="Value", width=180, height=32)
        edit.setText(f"{display_default:g}")
        edit.selectAll()
        edit.setToolTip(display_label)
        x, y = self._context_hud_position(180, 52)
        label_widget = QLabel(display_label, cast("QWidget", self._host))
        label_widget.setProperty("role", "canvas-hud-label")
        label_widget.setFixedSize(180, 18)
        label_widget.move(x, y)
        label_widget.show()
        edit.move(x, y + 20)
        self._host._hud_prompt_edit = edit
        self._host._hud_prompt_label = label_widget
        self._host._show_flash(display_label, 1600)

        def _preview(text: str) -> None:
            if preview is None:
                return
            try:
                value = _parse_expression(text, unit or "mm", is_length=is_length)
            except (TypeError, ValueError):
                self._host._clear_operation_preview()
                return
            if minimum is not None and value < minimum:
                self._host._clear_operation_preview()
                return
            if maximum is not None and value > maximum:
                self._host._clear_operation_preview()
                return
            preview(value)

        if preview is not None:
            edit.textChanged.connect(_preview)
            _preview(edit.text())

        def _reject(message: str) -> None:
            # Keep the prompt open and flag it — silently vanishing made bad
            # input indistinguishable from success.
            edit.setProperty("error", True)
            edit.setToolTip(message)
            refresh_style(edit)
            self._host._show_flash(message, 1400)
            edit.selectAll()

        def _commit() -> None:
            try:
                value = _parse_expression(edit.text(), unit or "mm", is_length=is_length)
            except (TypeError, ValueError):
                _reject("Enter a valid number or expression")
                return
            if minimum is not None and value < minimum:
                _reject(
                    f"Value must be at least {_to_display(minimum, unit) if unit else minimum:g}"
                )
                return
            if maximum is not None and value > maximum:
                _reject(
                    f"Value must be at most {_to_display(maximum, unit) if unit else maximum:g}"
                )
                return
            self._dismiss_hud_prompt()
            callback(value)

        edit.returnPressed.connect(_commit)
        edit.setFocus()

    def _show_text_hud_prompt(
        self,
        label: str,
        callback,
        *,
        initial: str = "",
        width: int = 190,
    ) -> None:
        """Inline non-numeric prompt whose callback may raise ValueError."""
        self._dismiss_hud_prompt()
        edit = self._make_hud_edit(placeholder="Value", width=width, height=24)
        edit.setText(initial)
        x, y = self._context_hud_position(width, 44)
        label_widget = QLabel(label, cast("QWidget", self._host))
        label_widget.setProperty("role", "canvas-hud-label")
        label_widget.setFixedSize(width, 18)
        label_widget.move(x, y)
        label_widget.show()
        edit.move(x, y + 20)
        edit.setToolTip(label)
        self._host._hud_prompt_edit = edit
        self._host._hud_prompt_label = label_widget
        self._host._show_flash(label, 1800)

        def _commit() -> None:
            try:
                callback(edit.text().strip())
            except ValueError as exc:
                edit.setProperty("error", True)
                edit.setToolTip(str(exc))
                refresh_style(edit)
                self._host._show_flash(str(exc), 1400)
                edit.selectAll()
                return
            self._dismiss_hud_prompt()

        edit.returnPressed.connect(_commit)
        edit.setFocus()

    def _context_hud_position(self, width: int, height: int) -> tuple[int, int]:
        """Anchor prompts near cursor/selection while keeping them on canvas."""
        if self._host._cursor_wx is not None and self._host._cursor_wy is not None:
            anchor_x, anchor_y = self._host._w2c(self._host._cursor_wx, self._host._cursor_wy)
        else:
            bounds = self._host._selection_bounds()
            if bounds is not None:
                anchor_x, anchor_y = self._host._w2c(
                    (bounds[0] + bounds[2]) / 2.0,
                    (bounds[1] + bounds[3]) / 2.0,
                )
            else:
                anchor_x, anchor_y = self._host.width() / 2.0, self._host.height() / 2.0
        return self._hud_position_near(anchor_x, anchor_y, width, height)

    def _hud_position_near(
        self,
        anchor_x: float,
        anchor_y: float,
        width: int,
        height: int,
        *,
        offset_x: int = 20,
        offset_y: int = 18,
    ) -> tuple[int, int]:
        """Place a world-context control near its anchor without clipping."""
        x = max(8, min(int(anchor_x + offset_x), max(8, self._host.width() - width - 8)))
        y = max(8, min(int(anchor_y + offset_y), max(8, self._host.height() - height - 8)))
        return x, y

    def _dismiss_hud_prompt(self) -> None:
        edit = getattr(self._host, "_hud_prompt_edit", None)
        if edit is not None:
            edit.deleteLater()
        self._host._hud_prompt_edit = None
        label_widget = getattr(self._host, "_hud_prompt_label", None)
        if label_widget is not None:
            label_widget.deleteLater()
        self._host._hud_prompt_label = None
        self._host._clear_operation_preview()

    def _show_dim_inputs(self) -> None:
        """Create both distance and angle QLineEdits that float near the cursor."""
        self._dismiss_dim_inputs()
        if not self._host._draw_pts:
            return

        dist_label = QLabel(
            f"Length ({_unit_suffix(self._host._unit_system)})",
            cast("QWidget", self._host),
        )
        dist_label.setProperty("role", "canvas-hud-label")
        dist_label.setFixedSize(92, 16)
        dist_label.show()
        self._host._dim_distance_label = dist_label

        dist_edit = self._make_hud_edit("Length", 92)
        dist_edit.setAccessibleDescription("Next segment length in the active unit")
        dist_edit.returnPressed.connect(self._apply_dim_input)
        # textEdited fires only on user keystrokes (not setText), so the dirty
        # flag tracks genuine typing; clearing the field resumes live updates.
        dist_edit.textEdited.connect(
            lambda t: setattr(self._host, "_dim_distance_dirty", bool(t.strip()))
        )
        self._host._dim_distance_edit = dist_edit
        self._host._dim_distance_dirty = False

        angle_label = QLabel("Angle (°)", cast("QWidget", self._host))
        angle_label.setProperty("role", "canvas-hud-label")
        angle_label.setFixedSize(92, 16)
        angle_label.show()
        self._host._dim_angle_label = angle_label

        angle_edit = self._make_hud_edit("Angle", 92)
        angle_edit.setAccessibleDescription("Next segment angle in degrees")
        angle_edit.returnPressed.connect(self._apply_dim_input)
        angle_edit.textEdited.connect(
            lambda t: setattr(self._host, "_dim_angle_dirty", bool(t.strip()))
        )
        self._host._dim_angle_edit = angle_edit
        self._host._dim_angle_dirty = False

        # Position immediately at the current cursor — otherwise the fields
        # flash at the canvas origin (0, 0) until the next mouse-move event.
        if self._host._cursor_wx is not None and self._host._cursor_wy is not None:
            cx, cy = self._host._w2c(self._host._cursor_wx, self._host._cursor_wy)
            self._update_dim_positions(cx, cy)

    def _dismiss_dim_inputs(self) -> None:
        """Remove the auto-dimension HUD widgets."""
        if self._host._dim_distance_edit is not None:
            self._host._dim_distance_edit.hide()
            self._host._dim_distance_edit.deleteLater()
            self._host._dim_distance_edit = None
        if self._host._dim_angle_edit is not None:
            self._host._dim_angle_edit.hide()
            self._host._dim_angle_edit.deleteLater()
            self._host._dim_angle_edit = None
        for attr in ("_dim_distance_label", "_dim_angle_label"):
            label = getattr(self._host, attr, None)
            if label is not None:
                label.hide()
                label.deleteLater()
                setattr(self._host, attr, None)
        self._host._dim_distance_dirty = False
        self._host._dim_angle_dirty = False

    # ── Inline selection-badge dimension editor ───────────────────────────────

    def _show_sel_dim_editor(self, axis: str, rect: QRectF) -> None:
        """Show a floating QLineEdit over a selection badge for direct editing.

        ``axis`` is "w"/"h" (bounding-box size) or, for a single selected
        2-point line, "l" (length) / "a" (absolute angle in degrees).
        """
        self._dismiss_sel_dim_editor()
        if axis in ("l", "a"):
            entity_id = self._host._selected_single_line()
            if entity_id is None:
                return
            entity = self._host._entities_by_id[entity_id]
            (ax, ay), (bx, by) = entity.points
            if axis == "l":
                cur_val = math.hypot(bx - ax, by - ay)
            else:
                cur_val = math.degrees(math.atan2(by - ay, bx - ax))
        else:
            bounds = self._host._selection_bounds()
            if bounds is None:
                return
            x0, y0, x1, y1 = bounds
            cur_val = (x1 - x0) if axis == "w" else (y1 - y0)

        edit = self._make_hud_edit(
            width=max(int(rect.width()) + 20, 112),
            height=32,
            align=Qt.AlignmentFlag.AlignCenter,
        )
        if axis == "a":
            edit.setText(f"{cur_val:.2f}")
        else:
            edit.setText(f"{_to_display(cur_val, self._host._unit_system):.2f}")
        edit.selectAll()
        # Keep the editor registered with the badge it replaces, but never
        # force the user to chase a clipped field beyond the canvas edge.
        edit_x = max(8, min(int(rect.x()), max(8, self._host.width() - edit.width() - 8)))
        edit_y = max(8, min(int(rect.y()), max(8, self._host.height() - edit.height() - 8)))
        edit.move(edit_x, edit_y)
        edit.setFocus()
        edit.returnPressed.connect(lambda: self._apply_sel_dim_editor())
        edit.editingFinished.connect(lambda: self._apply_sel_dim_editor())
        self._host._sel_dim_edit = edit
        self._host._sel_dim_axis = axis

    def _apply_sel_dim_editor(self) -> None:
        if self._host._sel_dim_edit is None or self._host._sel_dim_axis is None:
            return
        text = self._host._sel_dim_edit.text().strip()
        axis = self._host._sel_dim_axis
        # Disconnect editingFinished before dismissing to avoid double-trigger
        try:
            self._host._sel_dim_edit.editingFinished.disconnect()
        except RuntimeError as exc:
            # Qt raises when the editor was already disconnected during
            # teardown; dismissal is still safe and must continue.
            LOGGER.debug("Selection editor was already disconnected: %s", exc)
        self._dismiss_sel_dim_editor()
        if not text:
            return
        try:
            val = _parse_expression(text, self._host._unit_system, is_length=axis != "a")
        except ValueError:
            self._host._show_flash("Enter a valid number or expression", 1200)
            return
        if axis == "a":
            # Absolute angle: any value is valid (normalized by trig)
            self._host._set_selected_line_angle(val)
            self._host._show_flash("Angle updated", 900)
            return
        if val <= 0:
            self._host._show_flash("Value must be greater than zero", 1200)
            return
        if axis == "w":
            self._host._set_selected_width(val)
        elif axis == "h":
            self._host._set_selected_height(val)
        elif axis == "l":
            self._host._set_selected_line_length(val)
        self._host._show_flash("Dimension updated", 900)

    def _dismiss_sel_dim_editor(self) -> None:
        if self._host._sel_dim_edit is not None:
            self._host._sel_dim_edit.hide()
            self._host._sel_dim_edit.deleteLater()
            self._host._sel_dim_edit = None
        self._host._sel_dim_axis = None

    def _update_dim_positions(self, cx: float, cy: float) -> None:
        """Move the dim input widgets near cursor, avoiding snap label overlap.

        Positions the fields below-right of cursor with enough clearance so
        snap indicator icons and labels (drawn at +18, +4 from snap point)
        never get covered.
        """
        vw = max(self._host.width(), 100)
        vh = max(self._host.height(), 100)
        # Default: below-right of cursor
        dx, dy = 28, 22
        # If near right edge, flip to left side
        if cx + dx + 92 > vw:
            dx = -112
        # If near bottom edge, flip above
        if cy + dy + 76 > vh:
            dy = -76
        x = int(cx + dx)
        y = int(cy + dy)
        if self._host._dim_distance_label is not None:
            self._host._dim_distance_label.move(x, y)
        if self._host._dim_distance_edit is not None:
            self._host._dim_distance_edit.move(x, y + 16)
        if self._host._dim_angle_label is not None:
            self._host._dim_angle_label.move(x, y + 44)
        if self._host._dim_angle_edit is not None:
            self._host._dim_angle_edit.move(x, y + 60)

    def _update_dim_values(self, distance: float, angle: float) -> None:
        """Update displayed values in the dim inputs, unless user has typed.

        When a field is focused but untouched, keep its text selected so the
        next keystroke replaces the live value instead of appending to it.
        """
        if self._host._dim_distance_edit is not None and not self._host._dim_distance_dirty:
            # Display units: _apply_dim_input parses this text back with the
            # unit-aware parser, so raw mm here would commit 25.4× too far
            # in inch mode.
            self._host._dim_distance_edit.setText(
                f"{_to_display(distance, self._host._unit_system):.2f}"
            )
            if self._host._dim_distance_edit.hasFocus():
                self._host._dim_distance_edit.selectAll()
        if self._host._dim_angle_edit is not None and not self._host._dim_angle_dirty:
            self._host._dim_angle_edit.setText(f"{angle:.1f}")
            if self._host._dim_angle_edit.hasFocus():
                self._host._dim_angle_edit.selectAll()

    def _typed_draw_angle(self) -> float | None:
        """Return the user-typed segment angle (deg) if the angle field is dirty.

        Returns ``None`` when the field is auto-populated (not dirty) or does not
        parse, so callers only lock to a value the user explicitly entered.
        """
        if not getattr(self._host, "_dim_angle_dirty", False):
            return None
        if self._host._dim_angle_edit is None:
            return None
        text = self._host._dim_angle_edit.text().strip()
        if not text:
            return None
        try:
            return _parse_expression(text, is_length=False)
        except ValueError:
            return None

    def _typed_draw_distance(self) -> float | None:
        """Return the user-typed segment length (mm) if the distance field is dirty.

        Uses the same unit-aware parser as the commit path so the live
        rubber-band preview matches where Enter will actually place the point.
        """
        if not getattr(self._host, "_dim_distance_dirty", False):
            return None
        if self._host._dim_distance_edit is None:
            return None
        text = self._host._dim_distance_edit.text().strip()
        if not text:
            return None
        try:
            return _parse_expression(text, self._host._unit_system, is_length=True)
        except ValueError:
            return None

    def _apply_dim_input(self) -> None:
        """Read distance/angle from the HUD fields and place a point."""
        if not self._host._draw_pts:
            return
        last_wx, last_wy = self._host._draw_pts[-1]
        try:
            dist_text = (
                self._host._dim_distance_edit.text().strip()
                if self._host._dim_distance_edit
                else ""
            )
            angle_text = (
                self._host._dim_angle_edit.text().strip() if self._host._dim_angle_edit else ""
            )
            if angle_text:
                angle_deg = _parse_expression(angle_text, is_length=False)
            elif self._host._cursor_wx is not None and self._host._cursor_wy is not None:
                angle_deg = math.degrees(
                    math.atan2(
                        self._host._cursor_wy - last_wy,
                        self._host._cursor_wx - last_wx,
                    )
                )
            else:
                angle_deg = 0.0
            if dist_text:
                dist = _parse_expression(dist_text, self._host._unit_system, is_length=True)
            elif self._host._cursor_wx is not None and self._host._cursor_wy is not None:
                # Angle-only entry: project the cursor onto the typed-angle ray
                # so the length still tracks the pointer.
                ar = math.radians(angle_deg)
                vx = self._host._cursor_wx - last_wx
                vy = self._host._cursor_wy - last_wy
                dist = max(0.0, vx * math.cos(ar) + vy * math.sin(ar))
            else:
                return
            if dist <= 0:
                return
            angle_rad = math.radians(angle_deg)
            new_x = last_wx + dist * math.cos(angle_rad)
            new_y = last_wy + dist * math.sin(angle_rad)
            self._host._draw_pts.append((new_x, new_y))
            # Reset dirty flags so fields resume auto-updating
            self._host._dim_distance_dirty = False
            self._host._dim_angle_dirty = False
            self._host._refresh_draw_sidebar_state()
            self._host._redraw()
        except ValueError:
            self._host._show_flash("Enter a valid distance and angle", 1000)

    # ── Inference / alignment lines ──────────────────────────────────────────

    def _show_measure_edit(self) -> None:
        """Show a QLineEdit overlay for editing the measured distance."""
        self._dismiss_measure_edit()
        if not self._host._measure_anchor or not self._host._measure_end:
            return
        ax, ay = self._host._measure_anchor
        hx, hy = self._host._measure_end
        dist = math.hypot(hx - ax, hy - ay)
        cax, cay = self._host._w2c(ax, ay)
        chx, chy = self._host._w2c(hx, hy)
        mx, my = (cax + chx) / 2, (cay + chy) / 2

        le = self._make_hud_edit(width=180, height=32)
        display_dist = _to_display(dist, self._host._unit_system)
        le.setText(f"{display_dist:.4g}")
        le.setPlaceholderText(f"Target distance ({_unit_suffix(self._host._unit_system)})")
        le.setToolTip(
            "Enter the real target distance. The first picked point remains fixed.\n"
            "Expressions such as 25.4/2 are accepted."
        )
        le.setAccessibleName("Measure target distance")
        le.move(
            *self._hud_position_near(
                mx,
                my,
                180,
                32,
                offset_x=-90,
                offset_y=-44,
            )
        )
        le.setFocus()
        le.selectAll()
        le.returnPressed.connect(self._apply_measure_scale)
        self._host._measure_edit = le

    def _dismiss_measure_edit(self) -> None:
        """Remove the measure distance QLineEdit overlay."""
        if self._host._measure_edit is not None:
            self._host._measure_edit.hide()
            self._host._measure_edit.deleteLater()
            self._host._measure_edit = None

    def _apply_measure_scale(self) -> None:
        """Read new distance from the edit overlay and scale all polylines."""
        if (
            not self._host._measure_edit
            or not self._host._measure_anchor
            or not self._host._measure_end
        ):
            self._dismiss_measure_edit()
            return
        try:
            new_dist = _parse_expression(
                self._host._measure_edit.text(),
                self._host._unit_system,
                is_length=True,
            )
        except ValueError:
            self._host._show_flash("Enter a positive target distance", 1400)
            self._host._measure_edit.setFocus()
            self._host._measure_edit.selectAll()
            return
        ax, ay = self._host._measure_anchor
        hx, hy = self._host._measure_end
        old_dist = math.hypot(hx - ax, hy - ay)
        if old_dist < 1e-9 or new_dist <= 0:
            self._host._show_flash("Target distance must be greater than zero", 1400)
            self._host._measure_edit.setFocus()
            self._host._measure_edit.selectAll()
            return
        factor = new_dist / old_dist
        if not math.isfinite(factor) or factor > 1_000_000:
            self._host._show_flash("Scale factor is outside the supported range", 1600)
            return
        if abs(factor - 1.0) <= 1e-12:
            self._host._show_flash("Target matches the reference; nothing changed", 1200)
        elif not self._host.scale_by_reference(factor, self._host._measure_anchor):
            self._host._show_flash("Nothing available to scale", 1400)
            return
        else:
            self._host._show_flash(
                f"Scaled by {factor:.4g}× · Undo restores the previous size", 1800
            )
        self._dismiss_measure_edit()
        self._host._measure_locked = False
        self._host._measure_anchor = None
        self._host._measure_hover = None
        self._host._measure_end = None
        self._host._measure_snapped_a = False
        self._host._measure_snapped_b = False
        self._host._redraw()


# ════════════════════════════════════════════════════════════════════════════
# Text-on-path placement/editing
# ════════════════════════════════════════════════════════════════════════════

# ── Text-to-polyline conversion (previously text_shapes.py) ──────────────────


# Render glyphs at a large pixel size, then scale to mm — keeps curve
# flattening smooth regardless of the requested text height.


# ── Text document operations ─────────────────────────────────────────────────


Polyline = list[tuple[float, float]]
_RENDER_PX = 256


def text_to_polylines(
    text: str,
    *,
    family: str,
    height_mm: float,
    bold: bool = False,
    italic: bool = False,
) -> list[Polyline]:
    """Return closed polyline contours for ``text`` (``\\n`` starts a new line).

    ``height_mm`` is the total height of the rendered text block (cap
    height plus descenders for mixed-case input, stacked across every
    line). Coordinates are y-up with the block's bottom-left at the origin.
    """
    text = str(text)
    if not text.strip() or height_mm <= 0:
        return []

    font = QFont(family)
    font.setPixelSize(_RENDER_PX)
    font.setBold(bool(bold))
    font.setItalic(bool(italic))

    # QPainterPath.addText does NOT lay embedded newlines out as separate
    # lines (it places every character on one baseline) — each line needs
    # its own addText() call at a manually-advanced baseline Y.
    line_height = QFontMetrics(font).lineSpacing()
    path = QPainterPath()
    for i, line in enumerate(text.split("\n")):
        if line:
            path.addText(0.0, i * line_height, font, line)
    rect = path.boundingRect()
    if rect.height() <= 0:
        return []
    scale = float(height_mm) / rect.height()

    polys: list[Polyline] = []
    for sub in path.toSubpathPolygons():
        pts: Polyline = [
            (
                (p.x() - rect.x()) * scale,
                (rect.bottom() - p.y()) * scale,  # flip: Qt y-down → canvas y-up
            )
            for p in sub  # type: ignore[attr-defined]  # QPolygonF is iterable at runtime; missing from stubs
        ]
        if len(pts) < 3:
            continue
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        polys.append(pts)
    return polys


def user_fonts_dir() -> Path:
    """Folder scanned for extra .ttf/.otf fonts (drop files in to add fonts)."""
    d = user_data_dir() / "fonts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_user_fonts() -> list[str]:
    """Register every font file in the user fonts folder; return families."""
    families: list[str] = []
    for f in sorted(user_fonts_dir().iterdir()):
        if f.suffix.lower() in {".ttf", ".otf", ".ttc"}:
            font_id = QFontDatabase.addApplicationFont(str(f))
            if font_id >= 0:
                families.extend(QFontDatabase.applicationFontFamilies(font_id))
    return families


def install_font_file(path: str) -> str | None:
    """Copy a font file into the user fonts folder and register it.

    Returns the first family name on success, None on failure.
    """
    src_path = Path(path)
    if src_path.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
        return None
    dest = user_fonts_dir() / src_path.name
    try:
        shutil.copyfile(src_path, dest)
    except OSError:
        return None
    font_id = QFontDatabase.addApplicationFont(str(dest))
    if font_id < 0:
        return None
    fams = QFontDatabase.applicationFontFamilies(font_id)
    return fams[0] if fams else None


class TextService:
    """Own text contour creation, editing, and path attachment."""

    def __init__(self, host) -> None:
        self._host = host

    def add_text_at(
        self,
        wx: float,
        wy: float,
        *,
        text: str,
        family: str,
        height_mm: float,
        bold: bool = False,
        italic: bool = False,
    ) -> int:
        """Place ``text`` as grouped polyline outlines with its bottom-left
        at world (wx, wy). Returns the number of contours created."""
        polys = text_to_polylines(
            text, family=family, height_mm=height_mm, bold=bold, italic=italic
        )
        if not polys:
            return 0
        new_ids = self._place_text_contours(
            polys,
            wx,
            wy,
            {
                "text": text,
                "family": family,
                "height_mm": float(height_mm),
                "bold": bool(bold),
                "italic": bool(italic),
            },
        )
        self._host._sel = set(new_ids)
        self._host._show_flash(f"Text placed ({len(new_ids)} contours)", 900)
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return len(new_ids)

    def _place_text_contours(
        self,
        polys: list[list[tuple[float, float]]],
        wx: float,
        wy: float,
        params: dict[str, Any],
    ) -> list[str]:
        """Create grouped, editable text contours through the command boundary."""
        entities = self._text_entities(polys, wx, wy, params)
        result = self._host._canvas_service.create_entities(entities)
        return list(result.created_ids)

    def _text_entities(
        self,
        polys: list[list[tuple[float, float]]],
        wx: float,
        wy: float,
        params: dict[str, Any],
        *,
        group_id: int | None = None,
    ) -> list[EntityRecord]:
        if len(polys) > 1 and group_id is None:
            group_id = self._host._next_group_id
        return [
            EntityRecord(
                points=[(x + wx, y + wy) for x, y in poly],
                meta={"text_params": dict(params)},
                group=group_id if len(polys) > 1 else None,
                layer=self._host._active_layer,
            )
            for poly in polys
        ]

    def text_params_at(self, entity_id: str) -> dict[str, Any] | None:
        for entity in self._host._entities:
            if entity.id == entity_id:
                params = (entity.meta or {}).get("text_params")
                return dict(params) if isinstance(params, dict) else None
        return None

    def _text_member_ids(self, entity_id: str) -> list[str]:
        for entity in self._host._entities:
            if entity.id == entity_id:
                gid = entity.group
                if gid is None:
                    return [entity_id]
                return [e.id for e in self._host._entities if e.group == gid]
        return [entity_id]

    def rebuild_text(self, entity_id: str, values: dict[str, Any]) -> bool:
        """Replace a text entity's contours with newly rendered ones (same
        bottom-left anchor)."""
        members = self._text_member_ids(entity_id)
        member_entities = [e for e in self._host._entities if e.id in members]
        pts = [pt for entity in member_entities for pt in entity.points]
        if not pts:
            return False
        anchor_x = min(x for x, _ in pts)
        anchor_y = min(y for _, y in pts)
        polys = text_to_polylines(
            values["text"],
            family=values["family"],
            height_mm=float(values["height_mm"]),
            bold=bool(values.get("bold", False)),
            italic=bool(values.get("italic", False)),
        )
        if not polys:
            self._host._show_flash("Text rendered no contours", 1000)
            return False

        # If this text was attached to a path, remember which one so it can
        # be re-flowed after the rebuild replaces the glyph contours.
        existing_params = self.text_params_at(entity_id) or {}
        raw_path_id = existing_params.get("attached_path_id")
        attached_path_id: str | None = None
        if isinstance(raw_path_id, str) and raw_path_id in self._host._entities_by_id:
            attached_path_id = raw_path_id

        member_entity = next((e for e in self._host._entities if e.id == entity_id), None)
        group_id = member_entity.group if member_entity else None
        replacements = self._text_entities(
            polys,
            anchor_x,
            anchor_y,
            values,
            group_id=group_id,
        )
        source_ids = tuple(members)
        self._host._canvas_service.update_entities(
            replacements,
            source_ids=source_ids,
        )
        new_ids = [entity.id for entity in replacements]
        if attached_path_id is not None and new_ids:
            # The contour replacement already owns this user-visible undo step.
            self.attach_text_to_path(new_ids[0], attached_path_id, record_undo=False)
        self._host._sel = set(new_ids)
        self._host._sync_shape_storage_from_entities()
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        self._host._show_flash("Text updated", 800)
        return True

    def attach_text_to_path(self, text_id: str, path_id: str, *, record_undo: bool = True) -> bool:
        """Reposition a text entity's glyph contours to sit tangent to an
        open/closed path, ordered left-to-right along its arc length.

        The path's own geometry is untouched; only the text's contours move.
        """
        path_entity = next((e for e in self._host._entities if e.id == path_id), None)
        if path_entity is None:
            return False
        members = self._text_member_ids(text_id)
        if not members or path_id in members:
            return False
        path_pts = path_entity.points
        if len(path_pts) < 2:
            return False

        member_entities = [e for e in self._host._entities if e.id in members]
        all_pts = [pt for entity in member_entities for pt in entity.points]
        if not all_pts:
            return False
        anchor_x = min(x for x, _ in all_pts)
        anchor_y = min(y for _, y in all_pts)

        seg_lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path_pts, path_pts[1:])]
        total_len = sum(seg_lengths)
        if total_len <= 1e-9:
            return False

        def point_and_angle_at(s: float) -> tuple[float, float, float]:
            s = max(0.0, min(total_len, s))
            acc = 0.0
            for (a, b), seg_len in zip(zip(path_pts, path_pts[1:]), seg_lengths):
                if seg_len > 1e-9 and acc + seg_len >= s:
                    t = (s - acc) / seg_len
                    px = a[0] + (b[0] - a[0]) * t
                    py = a[1] + (b[1] - a[1]) * t
                    return px, py, math.atan2(b[1] - a[1], b[0] - a[0])
                acc += seg_len
            a, b = path_pts[-2], path_pts[-1]
            return path_pts[-1][0], path_pts[-1][1], math.atan2(b[1] - a[1], b[0] - a[0])

        candidates = []
        for member_id in members:
            entity = next((e for e in self._host._entities if e.id == member_id), None)
            if entity is None:
                continue
            entity = deepcopy(entity)
            pts = entity.points
            xs = [x for x, _ in pts]
            local_cx = (min(xs) + max(xs)) / 2.0
            s = local_cx - anchor_x  # glyph mm-position == arc-length position
            px, py, angle = point_and_angle_at(s)
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            new_pts = []
            for x, y in pts:
                dx = x - local_cx
                dy = y - anchor_y  # height above the text's own baseline
                rx = dx * cos_a - dy * sin_a
                ry = dx * sin_a + dy * cos_a
                new_pts.append((px + rx, py + ry))
            entity.points = new_pts
            meta = entity.meta
            if isinstance(meta, dict) and isinstance(meta.get("text_params"), dict):
                meta["text_params"]["attached_path_id"] = path_id
            candidates.append(entity)
        self._host._canvas_service.update_entities(candidates, record=record_undo)
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
        return True

    def prompt_edit_text(self, entity_id: str) -> None:
        """Reopen the text dialog prefilled with an entity's parameters."""
        params = self.text_params_at(entity_id)
        if params is None:
            return
        from simple_stipple.canvas.dialogs.text_dialog import AddTextDialog

        dlg = AddTextDialog(self._host, unit=self._host._unit_system)
        dlg.set_values(params)
        if dlg.exec() != AddTextDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        if not vals["text"].strip():
            return
        self.rebuild_text(entity_id, vals)

    def prompt_add_text(self, wx: float, wy: float) -> None:
        """Open the Add Text dialog and place the result at world (wx, wy)."""
        from simple_stipple.canvas.dialogs.text_dialog import AddTextDialog

        dlg = AddTextDialog(self._host, unit=self._host._unit_system)
        if dlg.exec() != AddTextDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        if not vals["text"].strip():
            self._host._show_flash("No text entered", 900)
            return
        self.add_text_at(wx, wy, **vals)
