"""CanvasRenderer — paint-method mixin for PolylineView.

All ``_paint_*`` helpers and ``_draw_badge`` live here.
PolylineView inherits this mixin via ``class PolylineView(QWidget, CanvasRenderer)``.
Since the methods are resolved through the normal MRO, every ``self.*`` reference
works without any modification.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, cast

from PIL import Image as PILImage
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QLineEdit

from src.backend.geometry import (
    arc_from_center_start_end,
    arc_from_three_points,
    build_circle_poly,
    build_ellipse_poly,
    build_polygon_poly,
    build_rect_poly,
    build_rounded_rect_poly,
    build_spline_poly,
    build_star_poly,
    shape_slot,
)
from src.infra.constants import DIM, POLY, Q_BG, SEL
from src.ui.canvas.constants import BADGE_BG as _BADGE_BG
from src.ui.canvas.constants import BADGE_DIM as _BADGE_DIM
from src.ui.canvas.constants import BADGE_TEXT as _BADGE_TEXT
from src.ui.canvas.constants import DRAW_COLOR as _DRAW_COLOR
from src.ui.canvas.constants import DRAW_LINE_W as _DRAW_LINE_W
from src.ui.canvas.constants import DRAW_VERT_R as _DRAW_VERT_R
from src.ui.canvas.constants import GRID_AXIS as _GRID_AXIS
from src.ui.canvas.constants import GRID_MAJOR as _GRID_MAJOR
from src.ui.canvas.constants import GRID_MINOR as _GRID_MINOR
from src.ui.canvas.constants import GUIDE_COLOR as _GUIDE_COLOR
from src.ui.canvas.constants import HANDLE as _HANDLE
from src.ui.canvas.constants import HANDLE_ACTIVE as _HANDLE_ACTIVE
from src.ui.canvas.constants import HANDLE_HOVER as _HANDLE_HOVER
from src.ui.canvas.constants import HANDLE_R as _HANDLE_R
from src.ui.canvas.constants import MEASURE_COLOR as _MEASURE_COLOR
from src.ui.canvas.constants import ORTHO_COLOR as _ORTHO_COLOR
from src.ui.canvas.constants import RUBBER_W as _RUBBER_W
from src.ui.canvas.constants import SELECT_PT as _SELECT_PT
from src.ui.canvas.constants import SELECT_PT_ACTIVE as _SELECT_PT_ACTIVE
from src.ui.canvas.constants import SNAP_CLOSE as _SNAP_CLOSE
from src.ui.units import format_length as _fmt_len
from src.ui.units import suffix as _unit_suffix
from src.ui.units import to_display as _to_display

if TYPE_CHECKING:
    from typing import Protocol

    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QWidget

    from src.ui.canvas.undo import EntityRecord

    class _RendererHost(Protocol):
        """Structural view of the PolylineView state CanvasRenderer's paint
        helpers read and write. PolylineView is assembled via multiple
        inheritance (``class PolylineView(QWidget, CanvasRenderer)``), so a
        type checker examining this file alone can't see PolylineView's
        attributes — this protocol closes that gap without creating a real
        (circular) runtime dependency on view.py.
        """

        RULER_PX: int
        _accent_polys: dict[int, str]
        _angle_snap_active: bool
        _band_start: QPointF | None
        _bg_h_mm: float
        _bg_pil: PILImage.Image | None
        _bg_w_mm: float
        _cursor_wx: float | None
        _cursor_wy: float | None
        _draw_arc_mode: str
        _draw_arc_pts: list[tuple[float, float]]
        _draw_constraint: str | None
        _draw_construction_mode: bool
        _draw_primitive: str
        _draw_pts: list[tuple[float, float]]
        _draw_shape_anchor_w: tuple[float, float] | None
        _draw_shape_cursor_w: tuple[float, float] | None
        _draw_shape_preview_active: bool
        _draw_snap: tuple[float, float] | None
        _draw_snap_type: str | None
        _edit_dragging: bool
        _edit_poly: int | None
        _edit_selected_verts: set[tuple[int, int]]
        _edit_vert: int | None
        _entities: list[EntityRecord]
        _fit_scale: float
        _ghost_polys: list[list[tuple[float, float]]]
        _ghost_visible: bool
        _operation_preview_polys: list[list[tuple[float, float]]]
        _grid_snap: bool
        _grid_spacing: float
        _grid_visible: bool
        _guides: list[tuple[str, float]]
        _dimensions: list[dict]
        _dim_pending_p1: tuple[float, float] | None
        _dim_pending_p2: tuple[float, float] | None
        _dim_pending_offset: float
        _selected_dimension: int | None
        _dimension_drag: int | None
        _dimension_mode: bool
        _dbtn_rect: tuple[float, float, float, float]
        _hover_poly: int | None
        _hover_snap: tuple[float, float] | None
        _hover_snap_multi: list[tuple[tuple[float, float], str, tuple[float, float]]]
        _hover_snap_type: str | None
        _hover_vert: tuple[int, int] | None
        _img_bounds: tuple[float, float] | None
        _layer_colors: dict[str, str]
        _lmb_prev: QPointF | None
        _mbtn_rect: tuple[float, float, float, float]
        _measure_anchor: tuple[float, float] | None
        _measure_edit: QLineEdit | None
        _measure_end: tuple[float, float] | None
        _measure_hover: tuple[float, float] | None
        _measure_hover_pre: tuple[float, float] | None
        _measure_mode: bool
        _mode: str
        _rulers_visible: bool
        _scale: float
        _sel: set[int]
        _shift_drag: bool
        _show_selection_bbox: bool
        _unit_system: str

        def _on_active_layer(self, e: EntityRecord) -> bool: ...
        def _is_near_start(self) -> bool: ...
        def _w2c(self, x: float, y: float) -> tuple[float, float]: ...
        def _c2w(self, cx: float, cy: float) -> tuple[float, float]: ...
        def _redraw(self) -> None: ...
        def _refresh_draw_sidebar_state(self) -> None: ...
        def _flattened_points(self, idx: int) -> list[tuple[float, float]]: ...
        def _entity_shows_point_handles(self, idx: int) -> bool: ...
        def _scale_all(self, factor: float) -> None: ...
        def _selected_single_line(self) -> int | None: ...
        def _set_selected_height(self, height: float) -> bool: ...
        def _set_selected_width(self, width: float) -> bool: ...
        def _set_selected_line_length(self, length: float) -> bool: ...
        def _set_selected_line_angle(self, angle_deg: float) -> bool: ...

        def _snap_to_polyline(
            self,
            cx: float,
            cy: float,
            *,
            reference_point: tuple[float, float] | None = None,
        ) -> tuple[float, float, str] | None: ...

        def _selection_bounds(
            self, indices: list[int] | None = None
        ) -> tuple[float, float, float, float] | None: ...

        def _dimension_line_points(
            self, dim: dict
        ) -> tuple[tuple[float, float], tuple[float, float]] | None: ...

        @staticmethod
        def _poly_rect_for_culling(
            poly: list[tuple[float, float]], *, epsilon: float = 1e-6
        ) -> QRectF: ...

        # QWidget surface the paint helpers use directly.
        def width(self) -> int: ...
        def height(self) -> int: ...
        def rect(self) -> QRect: ...

    _RendererBase = _RendererHost
else:
    _RendererBase = object

_FONT_HEL_9 = QFont("Helvetica", 9)
_FONT_HEL_9_DEMIBOLD = QFont("Helvetica", 9, QFont.Weight.DemiBold)
_FONT_HEL_10 = QFont("Helvetica", 10)
_FONT_HEL_10_BOLD = QFont("Helvetica", 10, QFont.Weight.Bold)
_FONT_HEL_11_BOLD = QFont("Helvetica", 11, QFont.Weight.Bold)
_FONT_HEL_14_BOLD = QFont("Helvetica", 14, QFont.Weight.Bold)
_FONT_MENLO_9 = QFont("Menlo", 9)


def _pil_to_qpixmap(pil_img: PILImage.Image) -> QPixmap:
    """Convert a PIL Image to QPixmap."""
    if pil_img.mode != "RGBA":
        pil_img = pil_img.convert("RGBA")
    data = pil_img.tobytes("raw", "RGBA")
    qimg = QImage(data, pil_img.width, pil_img.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())


class CanvasRenderer(_RendererBase):
    """Mixin providing all ``_paint_*`` draw helpers for :class:`PolylineView`.

    Do not instantiate directly — inherit alongside ``QWidget``.
    """

    def _paint_bg_image(self, painter: QPainter) -> None:
        if self._bg_w_mm <= 0 or self._bg_h_mm <= 0:
            return

        target_w = max(1, int(self._bg_w_mm * self._scale))
        target_h = max(1, int(self._bg_h_mm * self._scale))
        max_dim = 1200
        if max(target_w, target_h) > max_dim:
            ratio = max_dim / max(target_w, target_h)
            target_w = max(1, int(target_w * ratio))
            target_h = max(1, int(target_h * ratio))
        if (
            self._bg_pixmap is None
            or abs(self._scale - self._bg_cached_scale) > self._bg_cached_scale * 0.01
        ):
            if self._bg_pil is None:
                return
            try:
                resized = self._bg_pil.resize((target_w, target_h), PILImage.Resampling.LANCZOS)
                self._bg_pixmap = _pil_to_qpixmap(resized)
                self._bg_cached_scale = self._scale
            except (OSError, ValueError):
                return

        # Always render background into the exact world bounds rectangle so image
        # stays locked to geometry even when cache is downsampled for performance.
        x0, y0 = self._w2c(0.0, self._bg_h_mm)
        x1, y1 = self._w2c(self._bg_w_mm, 0.0)
        target_rect = QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized()
        source_rect = QRectF(self._bg_pixmap.rect())
        painter.drawPixmap(target_rect, self._bg_pixmap, source_rect)

    def _paint_ghost_polys(self, painter: QPainter, visible: QRectF) -> None:
        if not self._ghost_polys or not self._ghost_visible:
            return
        ghost_color = QColor(POLY)
        ghost_color.setAlpha(90)
        ghost_pen = QPen(ghost_color, 1.0)
        ghost_pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(ghost_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for poly in self._ghost_polys:
            if len(poly) < 2:
                continue
            _gp_rect = self._poly_rect_for_culling(poly)
            if not visible.intersects(_gp_rect):
                continue
            gpath = QPainterPath()
            gx, gy = self._w2c(*poly[0])
            gpath.moveTo(gx, gy)
            for pt in poly[1:]:
                px, py_ = self._w2c(*pt)
                gpath.lineTo(px, py_)
            if (
                len(poly) >= 3
                and math.hypot(poly[-1][0] - poly[0][0], poly[-1][1] - poly[0][1]) < 0.5
            ):
                gpath.closeSubpath()
            painter.drawPath(gpath)

    def _paint_operation_preview(self, painter: QPainter) -> None:
        """Paint transient geometry that is committed only when its HUD accepts."""
        if not self._operation_preview_polys:
            return
        painter.setPen(QPen(QColor("#f2cc60"), 1.6, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for poly in self._operation_preview_polys:
            if len(poly) < 2:
                continue
            path = QPainterPath()
            path.moveTo(*self._w2c(*poly[0]))
            for point in poly[1:]:
                path.lineTo(*self._w2c(*point))
            painter.drawPath(path)

    def _paint_main_polys(self, painter: QPainter, visible: QRectF) -> None:
        for idx, ent in enumerate(self._entities):
            poly = ent.points
            if ent.hidden:
                continue
            if len(poly) < 2:
                continue
            _poly_rect = self._poly_rect_for_culling(poly)
            if not visible.intersects(_poly_rect):
                continue
            if not self._on_active_layer(ent):
                # Non-active layer: dimmed dashed outline, no handles. Uses
                # the layer's assigned color (dimmed) when set, so switching
                # the active layer doesn't lose the multi-layer color context.
                layer_hex = self._layer_colors.get(ent.layer) if ent.layer is not None else None
                ghost_color = QColor(layer_hex) if layer_hex else QColor(POLY)
                ghost_color.setAlpha(140)  # doubled from 70 for better visibility
                ghost_pen = QPen(ghost_color, 1.2)  # was 1.0 — slightly thicker
                ghost_pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(ghost_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                gpath = QPainterPath()
                gx, gy = self._w2c(*poly[0])
                gpath.moveTo(gx, gy)
                for pt in poly[1:]:
                    px, py_ = self._w2c(*pt)
                    gpath.lineTo(px, py_)
                if (
                    len(poly) >= 3
                    and math.hypot(poly[-1][0] - poly[0][0], poly[-1][1] - poly[0][1]) < 0.5
                ):
                    gpath.closeSubpath()
                painter.drawPath(gpath)
                continue
            sel = idx in self._sel
            is_construction = ent.construction
            is_locked = ent.locked
            layer_color = self._layer_colors.get(ent.layer) if ent.layer is not None else None
            if sel:
                color = QColor(SEL)
            elif idx in self._accent_polys:
                color = QColor(self._accent_polys[idx])
            elif is_construction:
                color = QColor(_GUIDE_COLOR)
            elif layer_color:
                color = QColor(layer_color)
            else:
                color = QColor(POLY)
            if is_locked:
                color = QColor("#8b949e")
            hovered = not sel and idx == self._hover_poly and self._mode == "select"
            if hovered:
                color = QColor("#79c0ff")
            lw = 2.0 if sel or hovered else (1.2 if is_construction else 1.5)
            pen = QPen(color, lw)
            if is_construction or is_locked:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            render_poly = self._flattened_points(idx)
            if len(render_poly) < 2:
                continue
            path = QPainterPath()
            sx, sy = self._w2c(*render_poly[0])
            path.moveTo(sx, sy)
            for pt in render_poly[1:]:
                px, py_ = self._w2c(*pt)
                path.lineTo(px, py_)
            if (
                len(poly) >= 3
                and math.hypot(poly[-1][0] - poly[0][0], poly[-1][1] - poly[0][1]) < 0.5
            ):
                path.closeSubpath()
            painter.drawPath(path)

    _RULER_STEPS = (
        0.1,
        0.2,
        0.5,
        1.0,
        2.0,
        5.0,
        10.0,
        20.0,
        50.0,
        100.0,
        200.0,
        500.0,
        1000.0,
    )

    def _chrome_left(self) -> int:
        """Pixels reserved by the left ruler (0 when rulers are hidden)."""
        return self.RULER_PX if self._rulers_visible else 0

    def _chrome_top(self) -> int:
        """Pixels reserved by the top ruler (0 when rulers are hidden)."""
        return self.RULER_PX if self._rulers_visible else 0

    def _paint_dimension_line(
        self,
        painter: QPainter,
        p1: tuple[float, float],
        p2: tuple[float, float],
        offset: float,
        label: str | None,
        *,
        color: QColor,
    ) -> None:
        """Draw one drafting-style dimension: extension lines from p1/p2 out
        to a parallel line offset by ``offset`` mm, with arrowheads and a
        length label at its midpoint."""
        line = self._dimension_line_points({"p1": p1, "p2": p2, "offset": offset})
        if line is None:
            return
        (lax_w, lay_w), (lbx_w, lby_w) = line
        ax, ay = self._w2c(*p1)
        bx, by = self._w2c(*p2)
        lax, lay = self._w2c(lax_w, lay_w)
        lbx, lby = self._w2c(lbx_w, lby_w)

        ext_pen = QPen(color, 1.0, Qt.PenStyle.DashLine)
        painter.setPen(ext_pen)
        painter.drawLine(QPointF(ax, ay), QPointF(lax, lay))
        painter.drawLine(QPointF(bx, by), QPointF(lbx, lby))

        dim_pen = QPen(color, 1.4)
        painter.setPen(dim_pen)
        painter.drawLine(QPointF(lax, lay), QPointF(lbx, lby))

        # Arrowheads at each end of the dimension line, pointing outward.
        ddx, ddy = lbx - lax, lby - lay
        length = math.hypot(ddx, ddy)
        if length > 1e-6:
            ux, uy = ddx / length, ddy / length
            head = 7.0
            for tx, ty, dxu, dyu in (
                (lax, lay, -ux, -uy),
                (lbx, lby, ux, uy),
            ):
                perp_x, perp_y = -dyu, dxu
                p_a = QPointF(
                    tx - dxu * head + perp_x * head * 0.4, ty - dyu * head + perp_y * head * 0.4
                )
                p_b = QPointF(
                    tx - dxu * head - perp_x * head * 0.4, ty - dyu * head - perp_y * head * 0.4
                )
                painter.drawLine(QPointF(tx, ty), p_a)
                painter.drawLine(QPointF(tx, ty), p_b)

        if label is not None:
            mx, my = (lax + lbx) / 2.0, (lay + lby) / 2.0
            self._draw_badge(painter, mx, my - 12, label, 9)

    def _paint_dimensions(self, painter: QPainter, w: int, h: int) -> None:
        for i, dim in enumerate(self._dimensions):
            selected = i == self._selected_dimension
            dragging = i == self._dimension_drag
            color = QColor("#f5a623") if (selected or dragging) else QColor("#8957e5")
            ax, ay = dim["p1"]
            bx, by = dim["p2"]
            length_mm = math.hypot(bx - ax, by - ay)
            label = _fmt_len(length_mm, self._unit_system)
            self._paint_dimension_line(
                painter, dim["p1"], dim["p2"], dim["offset"], label, color=color
            )

        # In-progress placement preview.
        if self._dim_pending_p1 is not None:
            if self._dim_pending_p2 is not None:
                length_mm = math.hypot(
                    self._dim_pending_p2[0] - self._dim_pending_p1[0],
                    self._dim_pending_p2[1] - self._dim_pending_p1[1],
                )
                label = _fmt_len(length_mm, self._unit_system)
                self._paint_dimension_line(
                    painter,
                    self._dim_pending_p1,
                    self._dim_pending_p2,
                    self._dim_pending_offset,
                    label,
                    color=QColor("#39c5cf"),
                )
            elif self._cursor_wx is not None and self._cursor_wy is not None:
                ax, ay = self._w2c(*self._dim_pending_p1)
                cx, cy = self._w2c(self._cursor_wx, self._cursor_wy)
                painter.setPen(QPen(QColor("#39c5cf"), 1.0, Qt.PenStyle.DashLine))
                painter.drawLine(QPointF(ax, ay), QPointF(cx, cy))

    def _paint_guides(self, painter: QPainter, w: int, h: int) -> None:
        if not self._guides:
            return
        pen = QPen(QColor("#39c5cf"), 1.0, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        for i, (orient, coord) in enumerate(self._guides):
            dragging = i == getattr(self, "_guide_drag", None)
            selected = i == getattr(self, "_selected_guide", None)
            if dragging:
                painter.setPen(QPen(QColor("#56d4dd"), 1.6))
            elif selected:
                painter.setPen(QPen(QColor("#f5a623"), 1.8, Qt.PenStyle.DashLine))
            if orient == "v":
                gx, _ = self._w2c(coord, 0.0)
                painter.drawLine(QPointF(gx, 0.0), QPointF(gx, float(h)))
                if dragging:
                    painter.drawText(
                        QPointF(gx + 4, 34), f"x = {_fmt_len(coord, self._unit_system)}"
                    )
            else:
                _, gy = self._w2c(0.0, coord)
                painter.drawLine(QPointF(0.0, gy), QPointF(float(w), gy))
                if dragging:
                    painter.drawText(
                        QPointF(28, gy - 4), f"y = {_fmt_len(coord, self._unit_system)}"
                    )
            if dragging or selected:
                painter.setPen(pen)

    def _ruler_step(self) -> float:
        """Smallest nice step that keeps major ticks ≥ ~55 px apart."""
        for step in self._RULER_STEPS:
            if step * self._scale >= 55.0:
                return step
        return self._RULER_STEPS[-1]

    def _paint_rulers(self, painter: QPainter, w: int, h: int) -> None:
        if not self._rulers_visible:
            return
        r = self.RULER_PX
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(13, 17, 23, 235))
        painter.drawRect(QRectF(0, 0, w, r))
        painter.drawRect(QRectF(0, 0, r, h))
        edge_pen = QPen(QColor("#30363d"), 1.0)
        painter.setPen(edge_pen)
        painter.drawLine(QPointF(0, r), QPointF(float(w), r))
        painter.drawLine(QPointF(r, 0), QPointF(r, float(h)))

        step = self._ruler_step()
        minor = step / 5.0
        tick_pen = QPen(QColor("#484f58"), 1.0)
        text_pen = QPen(QColor("#8b949e"), 1.0)
        font = painter.font()
        font.setPointSizeF(8.0)
        painter.setFont(font)

        # Top ruler (world X)
        wx0, _ = self._c2w(float(r), 0.0)
        wx1, _ = self._c2w(float(w), 0.0)
        start = math.floor(min(wx0, wx1) / minor) * minor
        x = start
        while x <= max(wx0, wx1) + minor:
            cx, _ = self._w2c(x, 0.0)
            if cx >= r:
                is_major = abs(x / step - round(x / step)) < 1e-6
                painter.setPen(tick_pen)
                painter.drawLine(QPointF(cx, r - (10 if is_major else 5)), QPointF(cx, float(r)))
                if is_major:
                    painter.setPen(text_pen)
                    painter.drawText(
                        QPointF(cx + 2, r - 11),
                        f"{_to_display(x, self._unit_system):g}",
                    )
            x += minor

        # Left ruler (world Y)
        _, wy0 = self._c2w(0.0, float(r))
        _, wy1 = self._c2w(0.0, float(h))
        start = math.floor(min(wy0, wy1) / minor) * minor
        y = start
        while y <= max(wy0, wy1) + minor:
            _, cy = self._w2c(0.0, y)
            if cy >= r:
                is_major = abs(y / step - round(y / step)) < 1e-6
                painter.setPen(tick_pen)
                painter.drawLine(QPointF(r - (10 if is_major else 5), cy), QPointF(float(r), cy))
                if is_major:
                    painter.setPen(text_pen)
                    painter.save()
                    painter.translate(r - 12, cy - 2)
                    painter.rotate(-90)
                    painter.drawText(QPointF(0, 0), f"{_to_display(y, self._unit_system):g}")
                    painter.restore()
            y += minor

        # Cursor position markers
        if self._cursor_wx is not None and self._cursor_wy is not None:
            mcx, mcy = self._w2c(self._cursor_wx, self._cursor_wy)
            painter.setPen(QPen(QColor("#79c0ff"), 1.0))
            if mcx > r:
                painter.drawLine(QPointF(mcx, 0.0), QPointF(mcx, float(r)))
            if mcy > r:
                painter.drawLine(QPointF(0.0, mcy), QPointF(float(r), mcy))

    def _paint_grid(self, painter: QPainter, canvas_w: int, canvas_h: int) -> None:
        spacing = max(self._grid_spacing, 0.001)
        if spacing * self._scale < 6:
            return
        wx0, wy_top = self._c2w(0.0, 0.0)
        wx1, wy_bottom = self._c2w(float(canvas_w), float(canvas_h))
        minx, maxx = sorted((wx0, wx1))
        miny, maxy = sorted((wy_bottom, wy_top))
        major_every = 5

        x = math.floor(minx / spacing) * spacing
        while x <= maxx + spacing:
            is_major = round(x / spacing) % major_every == 0
            color = (
                _GRID_AXIS
                if abs(x) < spacing * 0.25
                else (_GRID_MAJOR if is_major else _GRID_MINOR)
            )
            painter.setPen(QPen(color, 1))
            cx, _ = self._w2c(x, 0.0)
            painter.drawLine(QPointF(cx, 0.0), QPointF(cx, float(canvas_h)))
            x += spacing

        y = math.floor(miny / spacing) * spacing
        while y <= maxy + spacing:
            is_major = round(y / spacing) % major_every == 0
            color = (
                _GRID_AXIS
                if abs(y) < spacing * 0.25
                else (_GRID_MAJOR if is_major else _GRID_MINOR)
            )
            painter.setPen(QPen(color, 1))
            _, cy = self._w2c(0.0, y)
            painter.drawLine(QPointF(0.0, cy), QPointF(float(canvas_w), cy))
            y += spacing

    def _paint_edit_handles(self, painter: QPainter) -> None:
        for pi, poly in enumerate(e.points for e in self._entities):
            if not self._entity_shows_point_handles(pi):
                continue
            for vi, pt in enumerate(poly):
                cx, cy = self._w2c(*pt)
                is_hover = self._hover_vert == (pi, vi)
                is_active = self._edit_dragging and self._edit_poly == pi and self._edit_vert == vi
                is_selected = (pi, vi) in self._edit_selected_verts
                if is_active:
                    color = _HANDLE_ACTIVE
                    r = _HANDLE_R + 2
                elif is_hover:
                    color = _HANDLE_HOVER
                    r = _HANDLE_R + 1
                elif is_selected:
                    color = QColor("#79c0ff")
                    r = _HANDLE_R + 1
                else:
                    color = _HANDLE
                    r = _HANDLE_R
                pen = QPen(color, 1.5)
                painter.setPen(pen)
                if is_active or is_hover or is_selected:
                    painter.setBrush(QBrush(color))
                else:
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QPointF(cx, cy), r, r)
        self._paint_bezier_handles(painter, range(len(self._entities)))

    def _paint_select_handles(self, painter: QPainter) -> None:
        """Show selected poly vertices in select mode for direct manipulation."""
        if not self._sel:
            return
        for pi in sorted(self._sel):
            if pi < 0 or pi >= len(self._entities):
                continue
            if not self._entity_shows_point_handles(pi):
                continue
            poly = self._entities[pi].points
            for vi, pt in enumerate(poly):
                cx, cy = self._w2c(*pt)
                is_hover = self._hover_vert == (pi, vi)
                is_active = self._edit_dragging and self._edit_poly == pi and self._edit_vert == vi
                if is_active:
                    color = _SELECT_PT_ACTIVE
                    r = _HANDLE_R + 2
                elif is_hover:
                    color = _SNAP_CLOSE
                    r = _HANDLE_R + 1
                else:
                    color = _SELECT_PT
                    r = _HANDLE_R
                painter.setPen(QPen(color, 1.5))
                painter.setBrush(QBrush(color))
                painter.drawEllipse(QPointF(cx, cy), r, r)
        self._paint_bezier_handles(painter, sorted(self._sel))

    def _paint_bezier_handles(self, painter: QPainter, entity_indices) -> None:
        """Draw independent Bézier handles without adding permanent canvas noise."""
        for entity_index in entity_indices:
            if not 0 <= entity_index < len(self._entities):
                continue
            entity = self._entities[entity_index]
            if entity.kind != "bezier":
                continue
            for anchor_index, side, tip in self._bezier_handles(entity_index):
                anchor = entity.points[anchor_index]
                if math.dist(anchor, tip) <= 1e-9:
                    continue
                ax, ay = self._w2c(*anchor)
                hx, hy = self._w2c(*tip)
                active = self._bezier_handle_drag == (entity_index, anchor_index, side)
                hovered = self._hover_bezier_handle == (entity_index, anchor_index, side)
                color = QColor("#f2cc60") if active else QColor("#56d4dd")
                painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 150), 1.0))
                painter.drawLine(QPointF(ax, ay), QPointF(hx, hy))
                painter.setPen(QPen(color, 1.2))
                painter.setBrush(QBrush(color if active or hovered else QColor("#0d1117")))
                radius = 4.0 if active or hovered else 3.0
                painter.drawRect(QRectF(hx - radius, hy - radius, radius * 2, radius * 2))

    def _paint_in_progress_poly(self, painter: QPainter) -> None:
        draw_color = _GUIDE_COLOR if self._draw_construction_mode else _DRAW_COLOR
        pts_screen = [self._w2c(*pt) for pt in self._draw_pts]
        near_close = self._is_near_start()
        spline_mode = self._draw_primitive == "spline"

        # ── Placed segments (solid, thick) ──
        if len(pts_screen) >= 2 and not spline_mode:
            pen = QPen(draw_color, _DRAW_LINE_W)
            painter.setPen(pen)
            path = QPainterPath()
            path.moveTo(*pts_screen[0])
            for px, py_ in pts_screen[1:]:
                path.lineTo(px, py_)
            painter.drawPath(path)

        # ── Close-polygon preview ──
        if near_close and not spline_mode:
            start_cx, start_cy = self._w2c(*self._draw_pts[0])
            last_cx, last_cy = self._w2c(*self._draw_pts[-1])

            # Draw filled translucent preview of the closed shape
            preview_path = QPainterPath()
            preview_path.moveTo(*pts_screen[0])
            for px, py_ in pts_screen[1:]:
                preview_path.lineTo(px, py_)
            preview_path.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(0, 200, 170, 30)))
            painter.drawPath(preview_path)

            # Closing segment — solid teal line
            pen = QPen(_SNAP_CLOSE, _DRAW_LINE_W)
            painter.setPen(pen)
            painter.drawLine(QPointF(last_cx, last_cy), QPointF(start_cx, start_cy))

            # Prominent close ring — double ring with glow
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(0, 200, 170, 60), 4))
            painter.drawEllipse(QPointF(start_cx, start_cy), 14, 14)
            painter.setPen(QPen(_SNAP_CLOSE, 2.5))
            painter.drawEllipse(QPointF(start_cx, start_cy), 10, 10)

            # "Close" label
            painter.setPen(_SNAP_CLOSE)
            painter.setFont(_FONT_HEL_11_BOLD)
            painter.drawText(QPointF(start_cx + 16, start_cy + 5), "Close")

        # ── Vertex dots (larger, with outline ring) ──
        for i, pt in enumerate(self._draw_pts):
            cx, cy = self._w2c(*pt)
            # Outer ring
            painter.setPen(QPen(QColor(245, 166, 35, 120), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), _DRAW_VERT_R + 2, _DRAW_VERT_R + 2)
            # Filled center
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(draw_color))
            painter.drawEllipse(QPointF(cx, cy), _DRAW_VERT_R, _DRAW_VERT_R)

        # ── Rubber-band line to cursor ──
        if self._cursor_wx is not None and self._cursor_wy is not None and self._draw_pts:
            last = self._w2c(*self._draw_pts[-1])

            # Use effective snap position for rubber-band (visual cursor jump)
            if self._draw_snap is not None and not near_close:
                eff_wx, eff_wy = self._draw_snap
            elif near_close:
                eff_wx, eff_wy = self._draw_pts[0]
            else:
                eff_wx, eff_wy = self._cursor_wx, self._cursor_wy
            cur_c = self._w2c(eff_wx, eff_wy)

            if not near_close and not spline_mode:
                # Constraint color: blue when H/V constrained, amber otherwise
                if self._draw_constraint is not None:
                    rub_color = QColor("#4a9eff")
                else:
                    rub_color = draw_color
                pen = QPen(rub_color, _RUBBER_W)
                painter.setPen(pen)
                painter.drawLine(QPointF(*last), QPointF(*cur_c))

                # H/V constraint icon near midpoint
                if self._draw_constraint is not None:
                    mid_cx = (last[0] + cur_c[0]) / 2
                    mid_cy = (last[1] + cur_c[1]) / 2
                    painter.setPen(QColor("#4a9eff"))
                    painter.setFont(_FONT_HEL_11_BOLD)
                    painter.drawText(QPointF(mid_cx + 8, mid_cy - 6), self._draw_constraint)

        # ── Segment length badge on rubber-band ──
        if (
            self._cursor_wx is not None
            and self._cursor_wy is not None
            and self._draw_pts
            and not near_close
            and not spline_mode
        ):
            last_w = self._draw_pts[-1]
            eff_wx2 = self._draw_snap[0] if self._draw_snap else self._cursor_wx
            eff_wy2 = self._draw_snap[1] if self._draw_snap else self._cursor_wy
            seg_len = math.hypot(eff_wx2 - last_w[0], eff_wy2 - last_w[1])
            if seg_len > 0.01:
                last_c = self._w2c(*last_w)
                cur_c2 = self._w2c(eff_wx2, eff_wy2)
                mid_x = (last_c[0] + cur_c2[0]) / 2
                mid_y = (last_c[1] + cur_c2[1]) / 2
                seg_text = f"{seg_len:.2f}"
                self._draw_badge(painter, mid_x, mid_y - 12, seg_text, 10)

        # ── Cumulative polyline length and point count (top-right badge) ──
        if self._draw_pts:
            total_len = 0.0
            for i in range(1, len(self._draw_pts)):
                px0, py0 = self._draw_pts[i - 1]
                px1, py1 = self._draw_pts[i]
                total_len += math.hypot(px1 - px0, py1 - py0)
            if self._cursor_wx is not None and self._cursor_wy is not None:
                eff_wx3 = self._draw_snap[0] if self._draw_snap else self._cursor_wx
                eff_wy3 = self._draw_snap[1] if self._draw_snap else self._cursor_wy
                total_len += math.hypot(
                    eff_wx3 - self._draw_pts[-1][0],
                    eff_wy3 - self._draw_pts[-1][1],
                )
            vw = max(self.width(), 100)
            summary_text = (
                f"Total: {_fmt_len(total_len, self._unit_system)}  |  {len(self._draw_pts)} pts"
            )
            self._draw_badge(painter, vw - 100, 50, summary_text, 10)

    def _paint_draw_shape_preview(self, painter: QPainter) -> None:
        if not self._draw_shape_preview_active or self._draw_shape_anchor_w is None:
            return

        # Always draw anchor dot (visible even before the second click)
        ax, ay = self._draw_shape_anchor_w
        acx, acy = self._w2c(ax, ay)
        painter.setPen(QPen(QColor(245, 166, 35, 140), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(acx, acy), _DRAW_VERT_R + 2, _DRAW_VERT_R + 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(_DRAW_COLOR))
        painter.drawEllipse(QPointF(acx, acy), _DRAW_VERT_R, _DRAW_VERT_R)

        if self._draw_shape_cursor_w is None:
            return
        sx, sy = self._draw_shape_anchor_w
        ex, ey = self._draw_shape_cursor_w
        cx = (sx + ex) / 2.0
        cy = (sy + ey) / 2.0
        w = abs(ex - sx)
        h = abs(ey - sy)
        if w < 1e-6 or h < 1e-6:
            return

        if self._draw_primitive == "rectangle":
            poly = build_rect_poly(cx, cy, w, h)
        elif self._draw_primitive == "rounded_rectangle":
            poly = build_rounded_rect_poly(cx, cy, w, h, min(w, h) * 0.1)
        elif self._draw_primitive == "circle":
            # Center-first: anchor = center, cursor = rim point
            radius = math.hypot(ex - sx, ey - sy)
            poly = build_circle_poly(sx, sy, radius)
        elif self._draw_primitive == "ellipse":
            poly = build_ellipse_poly(cx, cy, w / 2.0, h / 2.0)
        elif self._draw_primitive == "polygon":
            # Center-first, matching circle: anchor = center, cursor = a
            # rim point. Uses the live-configurable side count so the
            # ghost actually reflects what the sides stepper is set to.
            radius = math.hypot(ex - sx, ey - sy)
            poly = build_polygon_poly(sx, sy, radius, self._draw_polygon_sides)
        elif self._draw_primitive == "star":
            radius = math.hypot(ex - sx, ey - sy)
            poly = build_star_poly(sx, sy, radius, self._draw_star_points)
        elif self._draw_primitive == "slot":
            poly = [(px + cx, py + cy) for px, py in shape_slot(w, h)]
        elif self._draw_primitive == "spline":
            pts = list(self._draw_pts)
            if self._cursor_wx is not None and self._cursor_wy is not None:
                pts.append((self._cursor_wx, self._cursor_wy))
            if len(pts) < 2:
                return
            poly = build_spline_poly(pts, segments=24, closed=False)
        else:
            return

        if len(poly) < 2:
            return

        # Draw shape outline
        pen = QPen(QColor("#f5a623"), 1.5, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        x0, y0 = self._w2c(*poly[0])
        path.moveTo(x0, y0)
        for pt in poly[1:]:
            px, py = self._w2c(*pt)
            path.lineTo(px, py)
        painter.drawPath(path)

        # Dimension annotations — circle gets radius line + R badge;
        # other shapes get W/H badges at bounding box edges.
        has_hud = getattr(self, "_shape_w_edit", None) is not None
        if self._draw_primitive == "circle":
            # Draw a dashed radius line from center to cursor
            radius = math.hypot(ex - sx, ey - sy)
            scx, scy = self._w2c(sx, sy)
            ecx, ecy = self._w2c(ex, ey)
            painter.setPen(QPen(QColor(245, 166, 35, 100), 1.0, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(scx, scy), QPointF(ecx, ecy))
            # Draw small tick marks at the four quadrant intersections
            for angle_deg in (0, 90, 180, 270):
                angle_rad = math.radians(angle_deg)
                qx = sx + radius * math.cos(angle_rad)
                qy = sy + radius * math.sin(angle_rad)
                qcx, qcy = self._w2c(qx, qy)
                dx_t = math.cos(angle_rad + math.pi / 2) * 4
                dy_t = math.sin(angle_rad + math.pi / 2) * 4
                painter.setPen(QPen(QColor(245, 166, 35, 160), 1.2))
                painter.drawLine(
                    QPointF(qcx - dx_t, qcy - dy_t),
                    QPointF(qcx + dx_t, qcy + dy_t),
                )
            # "R: X.XX" badge near cursor (not near anchor), skip if HUD showing
            if not has_hud:
                r_text = f"R  {radius:.2f}"
                painter.setFont(_FONT_HEL_9)
                fm = QFontMetrics(painter.font())
                tw = fm.horizontalAdvance(r_text)
                badge_x = ecx + 10
                badge_y = ecy - 8
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(_BADGE_BG))
                painter.drawRoundedRect(QRectF(badge_x - 4, badge_y - 12, tw + 8, 16), 3, 3)
                painter.setPen(QColor("#f5a623"))
                painter.drawText(QPointF(badge_x, badge_y), r_text)
        elif not has_hud:
            bx0c, by0c = self._w2c(min(sx, ex), max(sy, ey))
            bx1c, by1c = self._w2c(max(sx, ex), min(sy, ey))
            mid_btm_x = (bx0c + bx1c) / 2
            mid_rgt_y = (by0c + by1c) / 2
            painter.setFont(_FONT_HEL_9)
            fm = QFontMetrics(painter.font())
            w_text = f"{w:.2f}"
            h_text = f"{h:.2f}"
            tw_w = fm.horizontalAdvance(w_text)
            tw_h = fm.horizontalAdvance(h_text)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(_BADGE_BG))
            painter.drawRoundedRect(
                QRectF(mid_btm_x - tw_w / 2 - 4, max(by0c, by1c) + 6, tw_w + 8, 16),
                3,
                3,
            )
            painter.setPen(QColor("#f5a623"))
            painter.drawText(QPointF(mid_btm_x - tw_w / 2, max(by0c, by1c) + 19), w_text)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(_BADGE_BG))
            painter.drawRoundedRect(QRectF(max(bx0c, bx1c) + 6, mid_rgt_y - 8, tw_h + 8, 16), 3, 3)
            painter.setPen(QColor("#f5a623"))
            painter.drawText(QPointF(max(bx0c, bx1c) + 10, mid_rgt_y + 5), h_text)

    def _paint_arc_preview(self, painter: QPainter) -> None:
        if self._draw_primitive != "arc":
            return
        if not self._draw_arc_pts:
            return

        pen = QPen(QColor("#f5a623"), 1.5, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Draw placed points
        for wx, wy in self._draw_arc_pts:
            cx, cy = self._w2c(wx, wy)
            painter.drawEllipse(QPointF(cx, cy), 3.0, 3.0)

        # Draw helper segment(s) to cursor
        cur = None
        if self._draw_snap is not None:
            cur = self._draw_snap
        elif self._cursor_wx is not None and self._cursor_wy is not None:
            cur = (self._cursor_wx, self._cursor_wy)

        if len(self._draw_arc_pts) == 1 and cur is not None:
            ax, ay = self._w2c(*self._draw_arc_pts[0])
            bx, by = self._w2c(*cur)
            painter.drawLine(QPointF(ax, ay), QPointF(bx, by))
            return

        if len(self._draw_arc_pts) >= 2 and cur is not None:
            p0 = self._draw_arc_pts[0]
            p1 = self._draw_arc_pts[1]
            p2 = cur
            a0x, a0y = self._w2c(*p0)
            a1x, a1y = self._w2c(*p1)
            if self._draw_arc_mode == "center-start-end":
                painter.drawLine(QPointF(a0x, a0y), QPointF(a1x, a1y))
                c2x, c2y = self._w2c(*p2)
                painter.drawLine(QPointF(a0x, a0y), QPointF(c2x, c2y))
                arc_poly = arc_from_center_start_end(p0, p1, p2, 30)
            else:
                # Show baseline between first two points
                painter.drawLine(QPointF(a0x, a0y), QPointF(a1x, a1y))
                arc_poly = arc_from_three_points(p0, p1, p2, 30)
            if len(arc_poly) >= 2:
                path = QPainterPath()
                sx, sy = self._w2c(*arc_poly[0])
                path.moveTo(sx, sy)
                for pt in arc_poly[1:]:
                    px, py = self._w2c(*pt)
                    path.lineTo(px, py)
                painter.drawPath(path)

    def _paint_spline_preview(self, painter: QPainter) -> None:
        if self._draw_primitive != "spline" or len(self._draw_pts) < 2:
            return

        pts = list(self._draw_pts)
        if self._cursor_wx is not None and self._cursor_wy is not None:
            pts.append((self._cursor_wx, self._cursor_wy))

        spline_poly = build_spline_poly(pts, segments=24, closed=False)
        if len(spline_poly) < 2:
            return

        draw_color = _GUIDE_COLOR if self._draw_construction_mode else _DRAW_COLOR
        painter.setPen(QPen(draw_color, _DRAW_LINE_W))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        path = QPainterPath()
        sx, sy = self._w2c(*spline_poly[0])
        path.moveTo(sx, sy)
        for pt in spline_poly[1:]:
            px, py = self._w2c(*pt)
            path.lineTo(px, py)
        painter.drawPath(path)

    def _paint_draw_preview_badges(self, painter: QPainter) -> None:
        outcomes = self._draw_preview_outcomes()
        if not outcomes or self._cursor_wx is None or self._cursor_wy is None:
            return
        cx, cy = self._w2c(self._cursor_wx, self._cursor_wy)
        y = cy - 42
        for label in outcomes:
            self._draw_badge(painter, cx + 36, y, label, 10)
            y -= 20

    def _draw_preview_outcomes(self) -> list[str]:
        """Return a list of badge labels to display during drawing preview.

        Returns an empty list by default; subclasses can override for
        custom badge behaviour (e.g. showing snap types).
        """
        return []

    def _paint_snap_overlay(
        self,
        painter: QPainter,
        *,
        snap_point: tuple[float, float] | None = None,
        snap_type: str | None = None,
    ) -> None:
        """Draw snap ring, type indicator, and label — rendered LAST so always visible."""
        point = self._draw_snap if snap_point is None else snap_point
        if point is None:
            return
        _dsx, _dsy = self._w2c(*point)
        snap_t = (self._draw_snap_type if snap_type is None else snap_type) or ""

        # Outer glow ring
        painter.setPen(QPen(QColor(0, 200, 170, 60), 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(_dsx, _dsy), 11, 11)
        # Inner teal snap ring
        painter.setPen(QPen(_SNAP_CLOSE, 2.0))
        painter.drawEllipse(QPointF(_dsx, _dsy), 7, 7)

        # Snap type indicator shape
        painter.setPen(QPen(_SNAP_CLOSE, 2.0))
        painter.setBrush(QBrush(_SNAP_CLOSE))
        if snap_t == "vertex":
            path = QPainterPath()
            path.moveTo(_dsx, _dsy - 6)
            path.lineTo(_dsx + 6, _dsy)
            path.lineTo(_dsx, _dsy + 6)
            path.lineTo(_dsx - 6, _dsy)
            path.closeSubpath()
            painter.drawPath(path)
        elif snap_t == "midpoint":
            path = QPainterPath()
            path.moveTo(_dsx, _dsy - 6)
            path.lineTo(_dsx + 5, _dsy + 4)
            path.lineTo(_dsx - 5, _dsy + 4)
            path.closeSubpath()
            painter.drawPath(path)
        elif snap_t == "intersection":
            painter.drawLine(QPointF(_dsx - 5, _dsy), QPointF(_dsx + 5, _dsy))
            painter.drawLine(QPointF(_dsx, _dsy - 5), QPointF(_dsx, _dsy + 5))
        elif snap_t == "center":
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(_dsx, _dsy), 5, 5)
            painter.drawLine(QPointF(_dsx - 6, _dsy), QPointF(_dsx + 6, _dsy))
            painter.drawLine(QPointF(_dsx, _dsy - 6), QPointF(_dsx, _dsy + 6))
        elif snap_t == "grid":
            painter.drawLine(QPointF(_dsx - 5, _dsy), QPointF(_dsx + 5, _dsy))
            painter.drawLine(QPointF(_dsx, _dsy - 5), QPointF(_dsx, _dsy + 5))
        elif snap_t == "perpendicular":
            painter.drawLine(QPointF(_dsx, _dsy + 5), QPointF(_dsx, _dsy - 4))
            painter.drawLine(QPointF(_dsx - 5, _dsy + 5), QPointF(_dsx + 5, _dsy + 5))
        elif snap_t == "edge":
            painter.setPen(QPen(_SNAP_CLOSE, 1.5))
            painter.drawLine(QPointF(_dsx - 4, _dsy - 4), QPointF(_dsx + 4, _dsy + 4))
            painter.drawLine(QPointF(_dsx - 4, _dsy + 4), QPointF(_dsx + 4, _dsy - 4))
        elif snap_t == "tangent":
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(_dsx, _dsy), 5, 5)
            painter.drawLine(QPointF(_dsx - 6, _dsy + 5), QPointF(_dsx + 6, _dsy + 5))
        elif snap_t == "extension":
            painter.setPen(QPen(_SNAP_CLOSE, 1.5, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(_dsx - 6, _dsy), QPointF(_dsx + 6, _dsy))
        elif snap_t == "circle_rim":
            # Diamond with an inner dot — signals radius lock
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(_dsx, _dsy), 4, 4)
            painter.setBrush(QBrush(_SNAP_CLOSE))
            painter.drawEllipse(QPointF(_dsx, _dsy), 2, 2)

        # Snap label pill — above the snap point. Always "Vertex" regardless
        # of mode — it used to say "Endpoint" while drawing and "Vertex"
        # everywhere else for the exact same snap_t=="vertex" hit (including
        # interior polyline vertices, not just line endpoints), which read
        # as inconsistent/buggy.
        if snap_t == "vertex":
            _label = "Vertex"
        elif snap_t == "midpoint":
            _label = "Midpoint"
        elif snap_t == "edge":
            _label = "On Edge"
        elif snap_t == "circle_rim":
            _label = "Through Point"
        elif snap_t == "tangent":
            _label = "Tangent"
        elif snap_t == "extension":
            _label = "Extension"
        else:
            _label = ""
        if _label:
            cache = getattr(self, "_snap_label_badge_cache", None)
            if not isinstance(cache, dict):
                cache = {}
                self._snap_label_badge_cache = cache

            badge = cache.get(_label)
            if badge is None:
                font = _FONT_HEL_9_DEMIBOLD
                fm = QFontMetrics(font)
                tw = fm.horizontalAdvance(_label)
                badge_w = math.ceil(tw + 8)
                badge_h = 16
                badge = QPixmap(badge_w, badge_h)
                badge.fill(Qt.GlobalColor.transparent)

                badge_painter = QPainter(badge)
                badge_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                badge_painter.setPen(Qt.PenStyle.NoPen)
                badge_painter.setBrush(QBrush(QColor(0, 30, 40, 210)))
                badge_painter.drawRoundedRect(QRectF(0, 0, badge_w, badge_h), 3, 3)
                badge_painter.setPen(_SNAP_CLOSE)
                badge_painter.setFont(font)
                badge_painter.drawText(
                    QRectF(0, 0, badge_w, badge_h),
                    Qt.AlignmentFlag.AlignCenter,
                    _label,
                )
                badge_painter.end()
                cache[_label] = badge

            badge_x = round(_dsx - badge.width() / 2.0)
            badge_y = round(_dsy - 24)
            painter.drawPixmap(badge_x, badge_y, badge)

    def _draw_badge(
        self, painter: QPainter, cx: float, cy: float, text: str, font_size: int
    ) -> QRectF:
        """Draw a text badge with semi-transparent background centered at (cx, cy).

        Returns the bounding QRectF of the drawn badge (for hit-testing).
        """
        font = QFont("Helvetica", font_size)
        painter.setFont(font)
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(text)
        th = fm.height()
        pad = 4
        rx = cx - tw / 2 - pad
        ry = cy - th / 2 - pad / 2
        rect = QRectF(rx, ry, tw + 2 * pad, th + pad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(_BADGE_BG))
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(_BADGE_TEXT if font_size >= 10 else _BADGE_DIM)
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignCenter,
            text,
        )
        return rect

    def _paint_transform_gizmo(
        self,
        painter: QPainter,
        bx0: float,
        by0: float,
        bx1: float,
        by1: float,
    ) -> None:
        """Paint lightweight rotate/scale gizmo handles around selection bounds.

        Redesigned for better visibility and usability:
        - Larger, more visible handles (8px visual, 12px hit area)
        - Clearer rotate handle with rotation icon
        - Improved color scheme matching GitHub dark theme
        - Dashed connection line from selection to rotate handle
        """
        top = min(by0, by1)
        bottom = max(by0, by1)
        right = max(bx0, bx1)
        mid_x = (bx0 + bx1) / 2.0

        left = min(bx0, bx1)
        mid_y = (by0 + by1) / 2.0
        rotate_center = QPointF(mid_x, top - 48.0)  # Moved further up for clarity

        self._gizmo_scale_rect = None
        self._gizmo_rotate_rect = QRectF(
            rotate_center.x() - 10,
            rotate_center.y() - 10,
            20,
            20,
        )

        # Selection frame with improved styling.
        frame_pen = QPen(QColor("#58a6ff"), 1.2)
        painter.setPen(frame_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(QPointF(left, top), QPointF(right, bottom)))

        # Larger handles with better hit areas (12px visual, 16px hit).
        hs = 6.0
        handles = [
            ("nw", left, top),
            ("n", mid_x, top),
            ("ne", right, top),
            ("e", right, mid_y),
            ("se", right, bottom),
            ("s", mid_x, bottom),
            ("sw", left, bottom),
            ("w", left, mid_y),
        ]
        self._gizmo_handle_rects = []

        # Familiar transform handles: square corners resize both axes;
        # compact edge handles resize one axis. Hit targets remain generous.
        handle_pen = QPen(QColor("#79c0ff"), 1.5)
        handle_brush = QBrush(QColor("#0d1117"))
        painter.setPen(handle_pen)
        painter.setBrush(handle_brush)

        for name, hx, hy in handles:
            rect = QRectF(hx - hs, hy - hs, hs * 2, hs * 2)
            # Generous hit area (16px), tight visual (12px).
            self._gizmo_handle_rects.append((name, rect.adjusted(-4, -4, 4, 4)))
            if len(name) == 2:
                painter.drawRoundedRect(rect, 2, 2)
            elif name in ("n", "s"):
                painter.drawRoundedRect(rect.adjusted(-2, 2, 2, -2), 2, 2)
            else:
                painter.drawRoundedRect(rect.adjusted(2, -2, -2, 2), 2, 2)

        # Rotate handle: orange circle with rotation icon.
        rotate_pen = QPen(QColor("#f5a623"), 1.5)
        rotate_brush = QBrush(QColor("#3a2b16"))
        painter.setPen(rotate_pen)
        painter.setBrush(rotate_brush)
        painter.drawEllipse(self._gizmo_rotate_rect)

        # Dashed line from selection top-center to rotate handle.
        painter.setPen(QPen(QColor("#4a9eff"), 1.0, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(mid_x, top), QPointF(rotate_center.x(), rotate_center.y()))

        # Move handle: a small 4-way arrow icon at the selection's center,
        # offering an unambiguous "grab here to drag" target distinct from
        # clicking the shape body (useful for thin/overlapping shapes).
        move_size = 11.0
        self._gizmo_move_rect = QRectF(
            mid_x - move_size, mid_y - move_size, move_size * 2, move_size * 2
        )
        move_pen = QPen(QColor("#79c0ff"), 1.5)
        move_brush = QBrush(QColor(13, 17, 23, 235))
        painter.setPen(move_pen)
        painter.setBrush(move_brush)
        painter.drawEllipse(QPointF(mid_x, mid_y), move_size, move_size)
        painter.setPen(QPen(QColor("#79c0ff"), 1.6))
        arm = move_size * 0.62
        head = move_size * 0.3
        # Four short arrow arms pointing N/E/S/W from the center.
        for ddx, ddy in ((0.0, -1.0), (1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)):
            tip_x, tip_y = mid_x + ddx * arm, mid_y + ddy * arm
            painter.drawLine(QPointF(mid_x, mid_y), QPointF(tip_x, tip_y))
            # Perpendicular arrowhead ticks.
            perp_x, perp_y = -ddy, ddx
            painter.drawLine(
                QPointF(tip_x, tip_y),
                QPointF(
                    tip_x - ddx * head + perp_x * head,
                    tip_y - ddy * head + perp_y * head,
                ),
            )
            painter.drawLine(
                QPointF(tip_x, tip_y),
                QPointF(
                    tip_x - ddx * head - perp_x * head,
                    tip_y - ddy * head - perp_y * head,
                ),
            )

    def _paint_selection_bbox(self, painter: QPainter, visible: QRectF) -> None:
        if not self._sel or self._mode != "select":
            self._gizmo_scale_rect = None
            self._gizmo_rotate_rect = None
            self._gizmo_move_rect = None
            self._gizmo_handle_rects = []
            return
        sel_pts = [
            pt for i in self._sel if 0 <= i < len(self._entities) for pt in self._entities[i].points
        ]
        if not sel_pts:
            self._gizmo_scale_rect = None
            self._gizmo_rotate_rect = None
            self._gizmo_move_rect = None
            self._gizmo_handle_rects = []
            return
        xs, ys = zip(*sel_pts)
        bx0, by0 = self._w2c(min(xs), max(ys))
        bx1, by1 = self._w2c(max(xs), min(ys))
        if self._show_selection_bbox:
            pad = 4
            pen = QPen(QColor(SEL), 1.0, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(
                QRectF(
                    bx0 - pad,
                    by0 - pad,
                    bx1 - bx0 + 2 * pad,
                    by1 - by0 + 2 * pad,
                )
            )
        self._paint_transform_gizmo(painter, bx0, by0, bx1, by1)

    def _paint_inference_lines(self, painter: QPainter) -> None:
        """Draw dotted inference lines showing H/V alignment with existing endpoints."""
        if self._cursor_wx is None or self._cursor_wy is None or not self._draw_pts:
            return
        cur_wx, cur_wy = self._cursor_wx, self._cursor_wy
        cur_cx, cur_cy = self._w2c(cur_wx, cur_wy)

        # Collect all candidate endpoints (existing polyline endpoints + last draw point)
        candidates: list[tuple[float, float]] = [
            pt for poly in (e.points for e in self._entities) for pt in poly
        ]
        candidates.extend(self._draw_pts)

        # Threshold: 3 degrees expressed as a ratio for quick check
        _ANGLE_THRESH = math.tan(math.radians(3.0))

        hits: list[tuple[float, tuple[float, float]]] = []  # (distance, endpoint)

        for ep in candidates:
            ex, ey = ep
            dx = abs(cur_wx - ex)
            dy = abs(cur_wy - ey)
            dist = math.hypot(dx, dy)
            if dist < 1e-6:
                continue

            # Check near-horizontal alignment (dy/dx < tan(3deg))
            if dx > 1e-9 and dy / dx < _ANGLE_THRESH:
                hits.append((dist, ep))
                continue
            # Check near-vertical alignment (dx/dy < tan(3deg))
            if dy > 1e-9 and dx / dy < _ANGLE_THRESH:
                hits.append((dist, ep))

        # Limit to 3 nearest
        hits.sort(key=lambda h: h[0])
        shown = 0
        seen: set[tuple[float, float]] = set()
        inf_pen = QPen(QColor("#4a9eff30"), 0.5, Qt.PenStyle.DashLine)
        painter.setPen(inf_pen)

        for _d, ep in hits:
            if ep in seen:
                continue
            seen.add(ep)
            if shown >= 3:
                break
            ex, ey = ep
            ecx, ecy = self._w2c(ex, ey)
            dx = abs(cur_wx - ex)
            dy = abs(cur_wy - ey)
            if dx > 1e-9 and dy / dx < _ANGLE_THRESH:
                # Horizontal inference line
                painter.drawLine(QPointF(ecx, ecy), QPointF(cur_cx, ecy))
                shown += 1
            elif dy > 1e-9 and dx / dy < _ANGLE_THRESH:
                # Vertical inference line
                painter.drawLine(QPointF(ecx, ecy), QPointF(ecx, cur_cy))
                shown += 1

    def _paint_geometry_health(self, painter: QPainter) -> None:
        """Overlay locatable topology findings without modifying geometry."""
        if not getattr(self, "_geometry_health_visible", False):
            return
        from src.backend.preflight import analyze_geometry

        polylines = [
            entity.points
            for entity in self._entities
            if not entity.hidden and len(entity.points) >= 1
        ]
        report = analyze_geometry(polylines)
        colors = {
            "error": QColor("#f85149"),
            "warning": QColor("#d29922"),
            "info": QColor("#58a6ff"),
        }
        counts = {"error": 0, "warning": 0, "info": 0}
        for issue in report.issues[:500]:
            counts[issue.severity] = counts.get(issue.severity, 0) + 1
            x, y = self._w2c(*issue.point)
            color = colors.get(issue.severity, colors["warning"])
            painter.setPen(QPen(color, 1.8))
            painter.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 35)))
            if issue.severity == "error":
                painter.drawEllipse(QPointF(x, y), 6, 6)
                painter.drawLine(QPointF(x - 3, y - 3), QPointF(x + 3, y + 3))
                painter.drawLine(QPointF(x - 3, y + 3), QPointF(x + 3, y - 3))
            elif issue.severity == "info":
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QPointF(x, y), 5, 5)
            else:
                path = QPainterPath()
                path.moveTo(x, y - 6)
                path.lineTo(x + 6, y + 5)
                path.lineTo(x - 6, y + 5)
                path.closeSubpath()
                painter.drawPath(path)

        parts = []
        if counts["error"]:
            parts.append(f"{counts['error']} error")
        if counts["warning"]:
            parts.append(f"{counts['warning']} warning")
        if counts["info"]:
            parts.append(f"{counts['info']} endpoint")
        summary = "Geometry healthy" if not parts else " · ".join(parts)
        painter.setFont(_FONT_HEL_9_DEMIBOLD)
        metrics = QFontMetrics(painter.font())
        width = metrics.horizontalAdvance(summary) + 14
        x = self._chrome_left() + 8
        y = self._chrome_top() + 8
        painter.setPen(QPen(QColor("#30363d"), 1))
        painter.setBrush(QBrush(QColor(13, 17, 23, 220)))
        painter.drawRoundedRect(QRectF(x, y, width, 22), 4, 4)
        painter.setPen(colors["error"] if counts["error"] else QColor("#8b949e"))
        painter.drawText(QRectF(x + 7, y, width - 14, 22), Qt.AlignmentFlag.AlignVCenter, summary)

    def _paint_curvature_overlay(self, painter: QPainter) -> None:
        """Color vertices by local turning curvature; blue is low, red is high."""
        if not getattr(self, "_curvature_visible", False):
            return
        indices = self._sel or {
            index for index, entity in enumerate(self._entities) if not entity.hidden
        }
        samples: list[tuple[float, float, float]] = []
        for index in indices:
            if not 0 <= index < len(self._entities):
                continue
            points = self._entities[index].points
            for previous, point, following in zip(points, points[1:], points[2:]):
                ax, ay = point[0] - previous[0], point[1] - previous[1]
                bx, by = following[0] - point[0], following[1] - point[1]
                alen, blen = math.hypot(ax, ay), math.hypot(bx, by)
                if min(alen, blen) <= 1e-9:
                    continue
                turn = abs(math.atan2(ax * by - ay * bx, ax * bx + ay * by))
                samples.append((point[0], point[1], turn / ((alen + blen) / 2.0)))
        if not samples:
            return
        ordered = sorted(value for _x, _y, value in samples)
        reference = max(ordered[len(ordered) // 2], 1e-6)
        painter.setPen(Qt.PenStyle.NoPen)
        for wx, wy, curvature in samples[:2000]:
            strength = max(0.0, min(1.0, curvature / (reference * 3.0)))
            color = QColor.fromHsvF(0.58 - 0.58 * strength, 0.8, 1.0, 0.9)
            painter.setBrush(QBrush(color))
            cx, cy = self._w2c(wx, wy)
            radius = 2.5 + 2.5 * strength
            painter.drawEllipse(QPointF(cx, cy), radius, radius)

    def _paint_constraint_badges(self, painter: QPainter) -> None:
        """Show compact constraint roles only around the active selection."""
        constraints = getattr(self, "_constraints", ())
        if not constraints or not self._sel:
            return
        selected_ids = {
            self._entities[index].id for index in self._sel if 0 <= index < len(self._entities)
        }
        entity_by_id = {entity.id: entity for entity in self._entities}
        symbols = {
            "horizontal": "H",
            "vertical": "V",
            "parallel": "∥",
            "perpendicular": "⊥",
            "equal_length": "=",
            "coincident": "●",
            "fixed": "■",
        }
        painter.setFont(_FONT_HEL_9_DEMIBOLD)
        shown = 0
        for constraint in constraints:
            if not selected_ids.intersection(constraint.entity_ids):
                continue
            for entity_id in constraint.entity_ids:
                entity = entity_by_id.get(entity_id)
                if entity is None or not entity.points:
                    continue
                mid = entity.points[len(entity.points) // 2]
                cx, cy = self._w2c(*mid)
                rect = QRectF(cx + 6, cy - 15, 16, 16)
                painter.setPen(QPen(QColor("#a371f7"), 1))
                painter.setBrush(QBrush(QColor(31, 24, 55, 220)))
                painter.drawRoundedRect(rect, 3, 3)
                painter.setPen(QColor("#d2a8ff"))
                painter.drawText(
                    rect, Qt.AlignmentFlag.AlignCenter, symbols.get(constraint.kind, "?")
                )
                shown += 1
                if shown >= 64:
                    return

    def _paint_measure_button(self, painter: QPainter, canvas_w: int) -> None:
        pad, bh, bw = 6, 22, 114
        label = "\u2715 Measure [M]" if self._measure_mode else "\u2295 Measure [M]"
        color = _MEASURE_COLOR if self._measure_mode else QColor(DIM)
        bg = QColor("#002233") if self._measure_mode else QColor("#14141e")
        top = pad + self._chrome_top()
        x1, y1 = canvas_w - bw - pad, top
        x2, y2 = canvas_w - pad, top + bh
        painter.setPen(QPen(color, 1))
        painter.setBrush(QBrush(bg))
        painter.drawRect(QRectF(x1, y1, bw, bh))
        painter.setFont(_FONT_HEL_10)
        painter.setPen(color)
        painter.drawText(QRectF(x1, y1, bw, bh), Qt.AlignmentFlag.AlignCenter, label)
        self._mbtn_rect = (x1, y1, x2, y2)

    def _paint_dimension_button(self, painter: QPainter, canvas_w: int) -> None:
        pad, bh, bw, gap = 6, 22, 114, 6
        label = (
            "\u2715 Dimension [\u21e7M]" if self._dimension_mode else "\u2295 Dimension [\u21e7M]"
        )
        color = QColor("#8957e5") if self._dimension_mode else QColor(DIM)
        bg = QColor("#1c1233") if self._dimension_mode else QColor("#14141e")
        top = pad + self._chrome_top()
        # Sits immediately to the left of the Measure button.
        mx1, _my1, _mx2, _my2 = self._mbtn_rect
        x2 = mx1 - gap
        x1 = x2 - bw
        y1, y2 = top, top + bh
        painter.setPen(QPen(color, 1))
        painter.setBrush(QBrush(bg))
        painter.drawRect(QRectF(x1, y1, bw, bh))
        painter.setFont(_FONT_HEL_10)
        painter.setPen(color)
        painter.drawText(QRectF(x1, y1, bw, bh), Qt.AlignmentFlag.AlignCenter, label)
        self._dbtn_rect = (x1, y1, x2, y2)

    def _paint_measure_overlay(self, painter: QPainter) -> None:
        if self._measure_anchor is None or self._measure_hover is None:
            return
        ax, ay = self._measure_anchor
        hx, hy = self._measure_hover
        cax, cay = self._w2c(ax, ay)
        chx, chy = self._w2c(hx, hy)
        dist = math.hypot(hx - ax, hy - ay)
        dx = abs(hx - ax)
        dy = abs(hy - ay)
        angle_deg = math.degrees(math.atan2(hy - ay, hx - ax)) if dist > 1e-9 else 0.0

        pen = QPen(_MEASURE_COLOR, 1.5, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(cax, cay), QPointF(chx, chy))

        # Snap rings on anchor
        if self._measure_snapped_a:
            painter.setPen(QPen(_SNAP_CLOSE, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cax, cay), 8, 8)

        r = 5
        painter.setPen(QPen(_MEASURE_COLOR, 2))
        painter.setBrush(QBrush(QColor("#001522")))
        painter.drawEllipse(QPointF(cax, cay), r, r)
        painter.setPen(QPen(_MEASURE_COLOR, 1))
        painter.drawLine(QPointF(cax - 8, cay), QPointF(cax + 8, cay))
        painter.drawLine(QPointF(cax, cay - 8), QPointF(cax, cay + 8))

        # Snap ring on hover/end
        if self._measure_snapped_b or (
            not self._measure_locked and self._snap_to_polyline(chx, chy) is not None
        ):
            painter.setPen(QPen(_SNAP_CLOSE, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(chx, chy), 8, 8)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(_MEASURE_COLOR))
        painter.drawEllipse(QPointF(chx, chy), 3, 3)

        mx, my = (cax + chx) / 2, (cay + chy) / 2
        badge_y = my - 28

        if not self._measure_locked:
            painter.setPen(QPen(_MEASURE_COLOR, 1))
            painter.setBrush(QBrush(QColor("#001522")))
            painter.drawRect(QRectF(mx - 100, badge_y - 14, 200, 32))
            painter.setPen(QColor("#ffffff"))
            painter.setFont(_FONT_HEL_11_BOLD)
            painter.drawText(
                QRectF(mx - 100, badge_y - 14, 200, 18),
                Qt.AlignmentFlag.AlignCenter,
                f"{_fmt_len(dist, self._unit_system)}  {angle_deg:.1f}\u00b0",
            )
            painter.setPen(_MEASURE_COLOR)
            painter.setFont(_FONT_HEL_9)
            painter.drawText(
                QRectF(mx - 100, badge_y, 200, 18),
                Qt.AlignmentFlag.AlignCenter,
                f"\u0394x {dx:.2f}  \u0394y {dy:.2f}  [\u21e7=snap angle]",
            )
        else:
            # When locked, draw the delta info below the badge
            painter.setPen(QPen(_MEASURE_COLOR, 1))
            painter.setBrush(QBrush(QColor("#001522")))
            painter.drawRect(QRectF(mx - 120, badge_y + 8, 240, 18))
            painter.setPen(_MEASURE_COLOR)
            painter.setFont(_FONT_HEL_9)
            painter.drawText(
                QRectF(mx - 120, badge_y + 8, 240, 18),
                Qt.AlignmentFlag.AlignCenter,
                f"\u0394x {dx:.2f}  \u0394y {dy:.2f}  {angle_deg:.1f}\u00b0  \u00b7  click to reset",
            )

    def paintEvent(self, event, /):
        painter = QPainter(cast("QWidget", self))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = max(self.width(), 100)
        h = max(self.height(), 100)
        painter.fillRect(self.rect(), Q_BG)

        if self._grid_visible:
            self._paint_grid(painter, w, h)
            # H. Origin crosshair at world (0,0)
            ox_cx, ox_cy = self._w2c(0.0, 0.0)
            o_pen = QPen(_GRID_AXIS, 1)
            painter.setPen(o_pen)
            painter.drawLine(QPointF(ox_cx - 10, ox_cy), QPointF(ox_cx + 10, ox_cy))
            painter.drawLine(QPointF(ox_cx, ox_cy - 10), QPointF(ox_cx, ox_cy + 10))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(ox_cx, ox_cy), 3, 3)

        # A. Full-viewport cursor crosshair (draw/measure mode)
        if (
            (self._mode == "draw" or self._measure_mode)
            and self._cursor_wx is not None
            and self._cursor_wy is not None
        ):
            _ch_cx, _ch_cy = self._w2c(self._cursor_wx, self._cursor_wy)
            _ch_pen = QPen(QColor("#2a3a4a"), 0.5)
            painter.setPen(_ch_pen)
            painter.drawLine(QPointF(_ch_cx, 0.0), QPointF(_ch_cx, float(h)))
            painter.drawLine(QPointF(0.0, _ch_cy), QPointF(float(w), _ch_cy))

        # Background image overlay
        if self._bg_pil and self._bg_w_mm > 0 and self._bg_h_mm > 0:
            self._paint_bg_image(painter)

        # Image bounds reference rectangle
        if self._img_bounds:
            bw, bh = self._img_bounds
            cx0, cy0 = self._w2c(0.0, 0.0)
            cx1, cy1 = self._w2c(bw, bh)
            pen = QPen(QColor("#334466"), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(QPointF(cx0, cy0), QPointF(cx1, cy1)))

        # Compute visible world-space rectangle for frustum culling
        _tl_wx, _tl_wy = self._c2w(0.0, 0.0)
        _br_wx, _br_wy = self._c2w(float(w), float(h))
        _visible_world = QRectF(
            QPointF(min(_tl_wx, _br_wx), min(_tl_wy, _br_wy)),
            QPointF(max(_tl_wx, _br_wx), max(_tl_wy, _br_wy)),
        )

        self._paint_guides(painter, w, h)
        self._paint_dimensions(painter, w, h)
        self._paint_ghost_polys(painter, _visible_world)
        self._paint_main_polys(painter, _visible_world)
        self._paint_operation_preview(painter)

        self._paint_selection_bbox(painter, _visible_world)

        # Select-mode dimensions (Fusion-like quick readout)
        if self._mode == "select" and self._sel:
            sel_bounds = self._selection_bounds()
            if sel_bounds is not None:
                x0, y0, x1, y1 = sel_bounds
                width = x1 - x0
                height = y1 - y0
                cx0, cy0 = self._w2c(x0, y0)
                cx1, cy1 = self._w2c(x1, y1)
                mx = (cx0 + cx1) / 2.0
                my = (cy0 + cy1) / 2.0
                self._sel_badge_w_rect = self._draw_badge(
                    painter, mx, min(cy0, cy1) - 14, f"W {width:.2f}", 9
                )
                self._sel_badge_h_rect = self._draw_badge(
                    painter, max(cx0, cx1) + 26, my, f"H {height:.2f}", 9
                )
                self._sel_badge_l_rect = None
                self._sel_badge_a_rect = None
                line_idx = self._selected_single_line()
                if line_idx is not None:
                    (ax, ay), (bx, by) = self._entities[line_idx].points
                    llen = math.hypot(bx - ax, by - ay)
                    ang = math.degrees(math.atan2(by - ay, bx - ax))
                    self._sel_badge_l_rect = self._draw_badge(
                        painter, mx - 34, max(cy0, cy1) + 16, f"L {llen:.2f}", 9
                    )
                    self._sel_badge_a_rect = self._draw_badge(
                        painter, mx + 34, max(cy0, cy1) + 16, f"∠ {ang:.1f}°", 9
                    )
            else:
                self._sel_badge_w_rect = None
                self._sel_badge_h_rect = None
                self._sel_badge_l_rect = None
                self._sel_badge_a_rect = None
        else:
            self._sel_badge_w_rect = None
            self._sel_badge_h_rect = None
            self._sel_badge_l_rect = None
            self._sel_badge_a_rect = None

        # Edit mode: vertex handles
        if self._mode == "edit":
            self._paint_edit_handles(painter)
        elif self._mode == "select" and self._sel:
            self._paint_select_handles(painter)

        # Construction lines (Feature 15)
        # Guide/measure lines (non-exported)
        # Ortho constraint line (Feature 6)
        if self._mode == "draw" and self._angle_snap_active and self._draw_pts:
            ortho_pen = QPen(_ORTHO_COLOR, 0.5, Qt.PenStyle.DashLine)
            painter.setPen(ortho_pen)
            anchor_cx, anchor_cy = self._w2c(*self._draw_pts[-1])
            if self._cursor_wx is not None and self._cursor_wy is not None:
                dx_o = self._cursor_wx - self._draw_pts[-1][0]
                dy_o = self._cursor_wy - self._draw_pts[-1][1]
                d_o = math.hypot(dx_o, dy_o)
                if d_o > 1e-9:
                    # Extend construction line across viewport
                    ext = max(w, h) * 2.0
                    # Actually use canvas coords directly
                    cur_cx, cur_cy = self._w2c(self._cursor_wx, self._cursor_wy)
                    cdx = cur_cx - anchor_cx
                    cdy = cur_cy - anchor_cy
                    cd = math.hypot(cdx, cdy)
                    if cd > 1e-9:
                        cnx, cny = cdx / cd, cdy / cd
                        painter.drawLine(
                            QPointF(anchor_cx - cnx * ext, anchor_cy - cny * ext),
                            QPointF(anchor_cx + cnx * ext, anchor_cy + cny * ext),
                        )

        # Draw mode: dim vertex guides + endpoint highlights
        if self._mode == "draw":
            _dim_dot = QColor("#4a5a6a")
            _helper_count = 0
            _helper_cap = 1800
            for _dpoly in (e.points for e in self._entities):
                if not _visible_world.intersects(self._poly_rect_for_culling(_dpoly)):
                    continue
                for _dpt in _dpoly:
                    if _helper_count >= _helper_cap:
                        break
                    _dcx, _dcy = self._w2c(*_dpt)
                    painter.setPen(QPen(QColor("#3a5a6a"), 1.0))
                    painter.setBrush(QBrush(_dim_dot))
                    painter.drawEllipse(QPointF(_dcx, _dcy), 3, 3)
                    _helper_count += 1
                if _helper_count >= _helper_cap:
                    break
            # Highlight endpoints of existing polylines (connection targets)
            _ep_color = QColor("#5a8aaa")
            for _dpoly in (e.points for e in self._entities):
                if not _visible_world.intersects(self._poly_rect_for_culling(_dpoly)):
                    continue
                if len(_dpoly) >= 2:
                    for _ept in (_dpoly[0], _dpoly[-1]):
                        _ecx, _ecy = self._w2c(*_ept)
                        painter.setPen(QPen(_ep_color, 1.5))
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.drawEllipse(QPointF(_ecx, _ecy), 5, 5)

        # C. Inference / alignment lines
        if self._mode == "draw" and self._draw_pts and self._cursor_wx is not None:
            self._paint_inference_lines(painter)

        self._paint_geometry_health(painter)
        self._paint_curvature_overlay(painter)
        self._paint_constraint_badges(painter)

        if self._mode == "draw":
            self._paint_draw_shape_preview(painter)
            self._paint_arc_preview(painter)
            self._paint_spline_preview(painter)
            self._paint_draw_preview_badges(painter)

        # In-progress draw polygon (BEFORE snap indicators so snaps render on top)
        if self._draw_pts:
            self._paint_in_progress_poly(painter)

        # Snap indicator — drawn LAST so it's always visible on top
        if self._mode == "draw" and self._draw_snap is not None:
            self._paint_snap_overlay(painter)
        elif self._hover_snap_multi:
            # Multi-touch snaps from a whole-shape drag (see
            # _object_snap_adjust): each entry's `dragged_point` is that
            # vertex's ACTUAL final position once release applies the
            # combined (adj_dx, adj_dy) — which can differ from this
            # match's own `snap_point` when a DIFFERENT match supplied the
            # other axis's adjustment. The ring MUST be drawn at
            # dragged_point (what you actually get), not snap_point (what
            # this one match alone implied) — drawing it at snap_point
            # made the indicator look right during the drag but land
            # somewhere else entirely once you released the mouse. The
            # dashed line still connects to the real target so the
            # alignment reference stays visible.
            guide_pen = QPen(QColor(0, 200, 170, 150), 1.0, Qt.PenStyle.DashLine)
            for snap_point, snap_type, dragged_point in self._hover_snap_multi:
                tx, ty = self._w2c(*snap_point)
                fx, fy = self._w2c(*dragged_point)
                if abs(tx - fx) > 0.5 or abs(ty - fy) > 0.5:
                    painter.setPen(guide_pen)
                    painter.drawLine(QPointF(fx, fy), QPointF(tx, ty))
                self._paint_snap_overlay(painter, snap_point=dragged_point, snap_type=snap_type)
        elif self._hover_snap is not None and self._hover_snap_type is not None:
            self._paint_snap_overlay(
                painter,
                snap_point=self._hover_snap,
                snap_type=self._hover_snap_type,
            )

        # Rubber-band. Window select (left→right) draws solid blue;
        # crossing select (right→left) draws dashed green — CAD convention.
        if self._shift_drag and self._band_start and self._lmb_prev:
            bx, by = self._band_start.x(), self._band_start.y()
            window = self._lmb_prev.x() >= bx
            if window:
                pen = QPen(QColor("#2f81f7"), 1, Qt.PenStyle.SolidLine)
                fill = QColor(47, 129, 247, 26)
            else:
                pen = QPen(QColor("#3fb950"), 1, Qt.PenStyle.DashLine)
                fill = QColor(63, 185, 80, 26)
            painter.setPen(pen)
            painter.setBrush(fill)
            painter.drawRect(
                QRectF(
                    QPointF(bx, by),
                    QPointF(self._lmb_prev.x(), self._lmb_prev.y()),
                )
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)

        if self._lasso_active and len(self._lasso_points) >= 2:
            painter.setPen(QPen(QColor("#3fb950"), 1.5, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(63, 185, 80, 22))
            painter.drawPolygon(QPolygonF(self._lasso_points))
            painter.setBrush(Qt.BrushStyle.NoBrush)

        if self._knife_start_w is not None and self._knife_end_w is not None:
            sx, sy = self._w2c(*self._knife_start_w)
            ex, ey = self._w2c(*self._knife_end_w)
            painter.setPen(QPen(QColor("#f85149"), 1.5, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(sx, sy), QPointF(ex, ey))

        # Measure overlay
        if self._measure_mode and self._measure_anchor and self._measure_hover:
            self._paint_measure_overlay(painter)

        # Pre-anchor measure snap indicator
        if (
            self._measure_mode
            and self._measure_anchor is None
            and self._measure_hover_pre is not None
        ):
            _mpx, _mpy = self._w2c(*self._measure_hover_pre)
            painter.setPen(QPen(_SNAP_CLOSE, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(_mpx, _mpy), 6, 6)

        # Info overlay
        n, s = len(self._entities), len(self._sel)
        info = f"{n} polylines" + (f"  ·  {s} selected" if s else "")
        if self._mode == "draw":
            pts_hint = f"  {len(self._draw_pts)} pt(s)" if self._draw_pts else ""
            info += f"  ·  DRAW{pts_hint}"
        elif self._mode == "edit":
            info += "  ·  EDIT"

        painter.setPen(QColor(DIM))
        painter.setFont(QFont("Helvetica", 10))
        info_x = self._chrome_left() + 8
        sidebar = getattr(self, "_draw_sidebar", None)
        if getattr(self, "_draw_sidebar_visible", False) and sidebar is not None:
            info_x = sidebar.x() + sidebar.width() + 12
        painter.drawText(info_x, self._chrome_top() + 18, info)

        hint: str | None = None
        if self._mode == "draw":
            hint = f"{self._draw_primitive}: click points · Enter finish · double-click close · Esc cancel"
        elif self._mode == "edit":
            hint = "Drag vertex · double-click edge to insert · right-click to delete · E exit"
        if hint:
            painter.setFont(QFont("Helvetica", 9))
            painter.drawText(self._chrome_left() + 8, h - 8, hint)

        if not self._entities and not self._draw_pts:
            message = getattr(self, "_empty_message", "No polylines loaded")
            title, _, hint = message.partition("\n")
            painter.setPen(QColor("#3b4a6a"))
            painter.setFont(QFont("Helvetica", 13))
            offset = -10 if hint else 0
            painter.drawText(
                QRectF(0, offset, w, h),
                Qt.AlignmentFlag.AlignCenter,
                title,
            )
            if hint:
                painter.setPen(QColor("#2c3a55"))
                painter.setFont(QFont("Helvetica", 10))
                painter.drawText(
                    QRectF(0, 16, w, h),
                    Qt.AlignmentFlag.AlignCenter,
                    hint,
                )

        # Cursor position
        if self._cursor_wx is not None and self._cursor_wy is not None:
            painter.setPen(QColor(DIM))
            painter.setFont(QFont("Helvetica", 10))
            text = (
                f"{_to_display(self._cursor_wx, self._unit_system):.2f}, "
                f"{_to_display(self._cursor_wy, self._unit_system):.2f} "
                f"{_unit_suffix(self._unit_system)}"
            )
            fm = QFontMetrics(painter.font())
            tw = fm.horizontalAdvance(text)
            painter.drawText(w - tw - 8, h - 8, text)

        # Flash indicator
        if self._flash_text:
            painter.setFont(QFont("Helvetica", 12, QFont.Weight.Bold))
            fm = QFontMetrics(painter.font())
            ftw = fm.horizontalAdvance(self._flash_text)
            fth = fm.height()
            fpad = 8
            frx = w / 2 - ftw / 2 - fpad
            fry = 40 + self._chrome_top()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(20, 24, 36, 220)))
            painter.drawRoundedRect(QRectF(frx, fry, ftw + 2 * fpad, fth + fpad), 4, 4)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(
                QRectF(frx, fry, ftw + 2 * fpad, fth + fpad),
                Qt.AlignmentFlag.AlignCenter,
                self._flash_text,
            )

        # Measure button
        self._paint_measure_button(painter, w)
        self._paint_dimension_button(painter, w)

        painter.end()

    def _paint_chrome_rulers(self, painter: QPainter) -> None:
        """Rulers paint over everything else (chrome layer)."""
        self._paint_rulers(painter, max(self.width(), 100), max(self.height(), 100))
