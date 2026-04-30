"""CanvasRenderer — paint-method mixin for PolylineView.

All ``_paint_*`` helpers and ``_draw_badge`` live here.
PolylineView inherits this mixin via ``class PolylineView(QGraphicsView, CanvasRenderer)``.
Since the methods are resolved through the normal MRO, every ``self.*`` reference
works without any modification.
"""

from __future__ import annotations

import math

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
)

from src.backend.geometry.arc import arc_from_center_start_end, arc_from_three_points
from src.backend.geometry.primitives import (
    build_circle_poly,
    build_ellipse_poly,
    build_polygon_poly,
    build_rect_poly,
)
from src.backend.geometry.spline import build_spline_poly
from src.constants import DIM
from src.ui.canvas._constants import (
    BADGE_BG as _BADGE_BG,
)
from src.ui.canvas._constants import (
    BADGE_DIM as _BADGE_DIM,
)
from src.ui.canvas._constants import (
    BADGE_TEXT as _BADGE_TEXT,
)
from src.ui.canvas._constants import (
    DRAW_COLOR as _DRAW_COLOR,
)
from src.ui.canvas._constants import (
    DRAW_LINE_W as _DRAW_LINE_W,
)
from src.ui.canvas._constants import (
    DRAW_VERT_R as _DRAW_VERT_R,
)
from src.ui.canvas._constants import (
    GRID_AXIS as _GRID_AXIS,
)
from src.ui.canvas._constants import (
    GRID_MAJOR as _GRID_MAJOR,
)
from src.ui.canvas._constants import (
    GRID_MINOR as _GRID_MINOR,
)
from src.ui.canvas._constants import (
    GUIDE_COLOR as _GUIDE_COLOR,
)
from src.ui.canvas._constants import (
    HANDLE as _HANDLE,
)
from src.ui.canvas._constants import (
    HANDLE_ACTIVE as _HANDLE_ACTIVE,
)
from src.ui.canvas._constants import (
    HANDLE_HOVER as _HANDLE_HOVER,
)
from src.ui.canvas._constants import (
    HANDLE_R as _HANDLE_R,
)
from src.ui.canvas._constants import (
    MEASURE_COLOR as _MEASURE_COLOR,
)
from src.ui.canvas._constants import (
    RUBBER_W as _RUBBER_W,
)
from src.ui.canvas._constants import (
    SELECT_PT as _SELECT_PT,
)
from src.ui.canvas._constants import (
    SELECT_PT_ACTIVE as _SELECT_PT_ACTIVE,
)
from src.ui.canvas._constants import (
    SNAP_CLOSE as _SNAP_CLOSE,
)


def _pil_to_qpixmap(pil_img: PILImage.Image) -> QPixmap:
    """Convert a PIL Image to QPixmap."""
    if pil_img.mode != "RGBA":
        pil_img = pil_img.convert("RGBA")
    data = pil_img.tobytes("raw", "RGBA")
    qimg = QImage(data, pil_img.width, pil_img.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())


class CanvasRenderer:
    """Mixin providing all ``_paint_*`` draw helpers for :class:`PolylineView`.

    Do not instantiate directly — inherit alongside ``QGraphicsView``.
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
                resized = self._bg_pil.resize(
                    (target_w, target_h), PILImage.Resampling.LANCZOS
                )
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
        for pi, poly in enumerate(self._polys):
            kind = (
                self._entity_kinds[pi] if pi < len(self._entity_kinds) else "polyline"
            )
            if kind in {"arc", "circle", "ellipse"}:
                continue
            for vi, pt in enumerate(poly):
                cx, cy = self._w2c(*pt)
                is_hover = self._hover_vert == (pi, vi)
                is_active = (
                    self._edit_dragging
                    and self._edit_poly == pi
                    and self._edit_vert == vi
                )
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

    def _paint_select_handles(self, painter: QPainter) -> None:
        """Show selected poly vertices in select mode for direct manipulation."""
        if not self._sel:
            return
        for pi in sorted(self._sel):
            if pi < 0 or pi >= len(self._polys):
                continue
            kind = (
                self._entity_kinds[pi] if pi < len(self._entity_kinds) else "polyline"
            )
            if kind in {"arc", "circle", "ellipse"}:
                continue
            poly = self._polys[pi]
            for vi, pt in enumerate(poly):
                cx, cy = self._w2c(*pt)
                is_hover = self._hover_vert == (pi, vi)
                is_active = (
                    self._edit_dragging
                    and self._edit_poly == pi
                    and self._edit_vert == vi
                )
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
            painter.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
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
        if (
            self._cursor_wx is not None
            and self._cursor_wy is not None
            and self._draw_pts
        ):
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
                    painter.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
                    painter.drawText(
                        QPointF(mid_cx + 8, mid_cy - 6), self._draw_constraint
                    )

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
                painter.setFont(QFont("Helvetica", 9))
                fm = QFontMetrics(painter.font())
                tw = fm.horizontalAdvance(seg_text)
                # Background pill
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(_BADGE_BG))
                painter.drawRoundedRect(
                    QRectF(mid_x - tw / 2 - 4, mid_y - 8 - 12, tw + 8, 16), 3, 3
                )
                painter.setPen(QColor("#ffffff"))
                painter.drawText(QPointF(mid_x - tw / 2, mid_y - 8 - 1), seg_text)

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
            vp = self.viewport()
            vw = max(vp.width(), 100)
            summary_text = f"Total: {total_len:.2f} mm  |  {len(self._draw_pts)} pts"
            self._draw_badge(painter, vw - 100, 50, summary_text, 10)

    def _paint_draw_shape_preview(self, painter: QPainter) -> None:
        if (
            not self._draw_shape_preview_active
            or self._draw_shape_anchor_w is None
            or self._draw_shape_cursor_w is None
        ):
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
        elif self._draw_primitive == "circle":
            poly = build_circle_poly(cx, cy, min(w, h) / 2.0)
        elif self._draw_primitive == "ellipse":
            poly = build_ellipse_poly(cx, cy, w / 2.0, h / 2.0)
        elif self._draw_primitive == "polygon":
            poly = build_polygon_poly(cx, cy, min(w, h) / 2.0, 6)
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

        # Snap label pill — above the snap point
        mode = getattr(self, "_mode", "")
        if snap_t == "vertex":
            _label = "Endpoint" if mode == "draw" else "Vertex"
        elif snap_t == "midpoint":
            _label = "Midpoint"
        elif snap_t == "edge":
            _label = "On Edge"
        else:
            _label = ""
        if _label:
            cache = getattr(self, "_snap_label_badge_cache", None)
            if not isinstance(cache, dict):
                cache = {}
                self._snap_label_badge_cache = cache

            badge = cache.get(_label)
            if badge is None:
                font = QFont("Helvetica", 9, QFont.Weight.DemiBold)
                fm = QFontMetrics(font)
                tw = fm.horizontalAdvance(_label)
                badge_w = int(math.ceil(tw + 8))
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

            badge_x = int(round(_dsx - badge.width() / 2.0))
            badge_y = int(round(_dsy - 24))
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
        """Paint lightweight rotate/scale gizmo handles around selection bounds."""
        top = min(by0, by1)
        bottom = max(by0, by1)
        right = max(bx0, bx1)
        mid_x = (bx0 + bx1) / 2.0

        scale_center = QPointF(right + 12.0, bottom + 12.0)
        rotate_center = QPointF(mid_x, top - 18.0)

        self._gizmo_scale_rect = QRectF(
            scale_center.x() - 6,
            scale_center.y() - 6,
            12,
            12,
        )
        self._gizmo_rotate_rect = QRectF(
            rotate_center.x() - 6,
            rotate_center.y() - 6,
            12,
            12,
        )

        painter.setPen(QPen(QColor("#79c0ff"), 1.2))
        painter.setBrush(QBrush(QColor("#1f3a6e")))
        painter.drawRect(self._gizmo_scale_rect)

        painter.setPen(QPen(QColor("#f5a623"), 1.2))
        painter.setBrush(QBrush(QColor("#3a2b16")))
        painter.drawEllipse(self._gizmo_rotate_rect)

        painter.setPen(QPen(QColor("#4a9eff"), 1.0, Qt.PenStyle.DashLine))
        painter.drawLine(
            QPointF(mid_x, top), QPointF(rotate_center.x(), rotate_center.y())
        )

    def _paint_inference_lines(self, painter: QPainter, vp_w: int, vp_h: int) -> None:
        """Draw dotted inference lines showing H/V alignment with existing endpoints."""
        if self._cursor_wx is None or self._cursor_wy is None or not self._draw_pts:
            return
        cur_wx, cur_wy = self._cursor_wx, self._cursor_wy
        cur_cx, cur_cy = self._w2c(cur_wx, cur_wy)

        # Collect all candidate endpoints (existing polyline endpoints + last draw point)
        candidates: list[tuple[float, float]] = [
            pt for poly in self._polys for pt in poly
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

    def _paint_measure_button(self, painter: QPainter, canvas_w: int) -> None:
        pad, bh, bw = 6, 22, 114
        label = "\u2715 Measure [M]" if self._measure_mode else "\u2295 Measure [M]"
        color = _MEASURE_COLOR if self._measure_mode else QColor(DIM)
        bg = QColor("#002233") if self._measure_mode else QColor("#14141e")
        x1, y1 = canvas_w - bw - pad, pad
        x2, y2 = canvas_w - pad, pad + bh
        painter.setPen(QPen(color, 1))
        painter.setBrush(QBrush(bg))
        painter.drawRect(QRectF(x1, y1, bw, bh))
        painter.setFont(QFont("Helvetica", 10))
        painter.setPen(color)
        painter.drawText(QRectF(x1, y1, bw, bh), Qt.AlignmentFlag.AlignCenter, label)
        self._mbtn_rect = (x1, y1, x2, y2)

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
            painter.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
            painter.drawText(
                QRectF(mx - 100, badge_y - 14, 200, 18),
                Qt.AlignmentFlag.AlignCenter,
                f"{dist:.2f} mm  {angle_deg:.1f}\u00b0",
            )
            painter.setPen(_MEASURE_COLOR)
            painter.setFont(QFont("Helvetica", 9))
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
            painter.setFont(QFont("Helvetica", 9))
            painter.drawText(
                QRectF(mx - 120, badge_y + 8, 240, 18),
                Qt.AlignmentFlag.AlignCenter,
                f"\u0394x {dx:.2f}  \u0394y {dy:.2f}  {angle_deg:.1f}\u00b0  \u00b7  click to reset",
            )
