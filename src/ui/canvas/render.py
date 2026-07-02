"""CanvasRenderer — paint-method mixin for PolylineView.

All ``_paint_*`` helpers and ``_draw_badge`` live here.
PolylineView inherits this mixin via ``class PolylineView(QGraphicsView, CanvasRenderer)``.
Since the methods are resolved through the normal MRO, every ``self.*`` reference
works without any modification.
"""

from __future__ import annotations

import math

from PIL import Image as PILImage
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
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
from PySide6.QtWidgets import QLineEdit

from src.backend.geometry.arc import arc_from_center_start_end, arc_from_three_points
from src.backend.geometry.primitives import (
    build_circle_poly,
    build_ellipse_poly,
    build_polygon_poly,
    build_rect_poly,
)
from src.backend.geometry.spline import build_spline_poly
from src.constants import DIM, POLY, SEL
from src.ui.canvas._constants import (
    BADGE_BG as _BADGE_BG,
    BADGE_DIM as _BADGE_DIM,
    BADGE_TEXT as _BADGE_TEXT,
    DRAW_COLOR as _DRAW_COLOR,
    DRAW_LINE_W as _DRAW_LINE_W,
    DRAW_VERT_R as _DRAW_VERT_R,
    GRID_AXIS as _GRID_AXIS,
    GRID_MAJOR as _GRID_MAJOR,
    GRID_MINOR as _GRID_MINOR,
    GUIDE_COLOR as _GUIDE_COLOR,
    HANDLE as _HANDLE,
    HANDLE_ACTIVE as _HANDLE_ACTIVE,
    HANDLE_HOVER as _HANDLE_HOVER,
    HANDLE_R as _HANDLE_R,
    MEASURE_COLOR as _MEASURE_COLOR,
    ORTHO_COLOR as _ORTHO_COLOR,
    RUBBER_W as _RUBBER_W,
    SELECT_PT as _SELECT_PT,
    SELECT_PT_ACTIVE as _SELECT_PT_ACTIVE,
    SNAP_CLOSE as _SNAP_CLOSE,
)

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

    def _paint_ghost_polys(
        self, painter: QPainter, visible: QRectF
    ) -> None:
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
                and math.hypot(poly[-1][0] - poly[0][0], poly[-1][1] - poly[0][1])
                < 0.5
            ):
                gpath.closeSubpath()
            painter.drawPath(gpath)

    def _paint_main_polys(self, painter: QPainter, visible: QRectF) -> None:
        for idx, poly in enumerate(self._polys):
            if idx in self._hidden_polys:
                continue
            if len(poly) < 2:
                continue
            _poly_rect = self._poly_rect_for_culling(poly)
            if not visible.intersects(_poly_rect):
                continue
            sel = idx in self._sel
            is_construction = idx in self._construction_polys
            is_locked = idx in self._locked_polys
            if sel:
                color = QColor(SEL)
            elif idx in self._accent_polys:
                color = QColor(self._accent_polys[idx])
            elif is_construction:
                color = QColor(_GUIDE_COLOR)
            else:
                color = QColor(POLY)
            if is_locked:
                color = QColor("#8b949e")
            lw = 2.0 if sel else (1.2 if is_construction else 1.5)
            pen = QPen(color, lw)
            if is_construction or is_locked:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            render_poly = poly
            if (
                idx < len(self._entity_kinds)
                and self._entity_kinds[idx] == "spline"
                and len(poly) >= 2
            ):
                meta = self._entity_meta[idx] if idx < len(self._entity_meta) else None
                render_poly = build_spline_poly(
                    poly,
                    segments=int(meta.get("segments", 24)) if meta else 24,
                    closed=bool(meta.get("closed", False)) if meta else False,
                )
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
                    painter.setFont(_FONT_HEL_11_BOLD)
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
            vp = self.viewport()
            vw = max(vp.width(), 100)
            summary_text = f"Total: {total_len:.2f} mm  |  {len(self._draw_pts)} pts"
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
        elif self._draw_primitive == "circle":
            # Center-first: anchor = center, cursor = rim point
            radius = math.hypot(ex - sx, ey - sy)
            poly = build_circle_poly(sx, sy, radius)
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
                painter.drawRoundedRect(
                    QRectF(badge_x - 4, badge_y - 12, tw + 8, 16), 3, 3
                )
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
            painter.drawText(
                QPointF(mid_btm_x - tw_w / 2, max(by0c, by1c) + 19), w_text
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(_BADGE_BG))
            painter.drawRoundedRect(
                QRectF(max(bx0c, bx1c) + 6, mid_rgt_y - 8, tw_h + 8, 16), 3, 3
            )
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
        elif snap_t == "circle_rim":
            # Diamond with an inner dot — signals radius lock
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(_dsx, _dsy), 4, 4)
            painter.setBrush(QBrush(_SNAP_CLOSE))
            painter.drawEllipse(QPointF(_dsx, _dsy), 2, 2)

        # Snap label pill — above the snap point
        mode = getattr(self, "_mode", "")
        if snap_t == "vertex":
            _label = "Endpoint" if mode == "draw" else "Vertex"
        elif snap_t == "midpoint":
            _label = "Midpoint"
        elif snap_t == "edge":
            _label = "On Edge"
        elif snap_t == "circle_rim":
            _label = "Through Point"
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
        """Paint lightweight rotate/scale gizmo handles around selection bounds."""
        top = min(by0, by1)
        bottom = max(by0, by1)
        right = max(bx0, bx1)
        mid_x = (bx0 + bx1) / 2.0

        scale_center = QPointF(right + 12.0, bottom + 12.0)
        rotate_center = QPointF(mid_x, top - 34.0)

        self._gizmo_scale_rect = QRectF(
            scale_center.x() - 6,
            scale_center.y() - 6,
            12,
            12,
        )
        self._gizmo_rotate_rect = QRectF(
            rotate_center.x() - 8,
            rotate_center.y() - 8,
            16,
            16,
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

    def _paint_selection_bbox(self, painter: QPainter, visible: QRectF) -> None:
        if not self._sel or self._mode != "select":
            self._gizmo_scale_rect = None
            self._gizmo_rotate_rect = None
            return
        sel_pts = [
            pt
            for i in self._sel
            if 0 <= i < len(self._polys)
            for pt in self._polys[i]
        ]
        if not sel_pts:
            self._gizmo_scale_rect = None
            self._gizmo_rotate_rect = None
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
        painter.setFont(_FONT_HEL_10)
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
            painter.setFont(_FONT_HEL_11_BOLD)
            painter.drawText(
                QRectF(mx - 100, badge_y - 14, 200, 18),
                Qt.AlignmentFlag.AlignCenter,
                f"{dist:.2f} mm  {angle_deg:.1f}\u00b0",
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
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        vp = self.viewport()
        w = max(vp.width(), 100)
        h = max(vp.height(), 100)

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

        self._paint_ghost_polys(painter, _visible_world)
        self._paint_main_polys(painter, _visible_world)

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
                    (ax, ay), (bx, by) = self._polys[line_idx]
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
            for _dpoly in self._polys:
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
            for _dpoly in self._polys:
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
        elif self._hover_snap is not None and self._hover_snap_type is not None:
            self._paint_snap_overlay(
                painter,
                snap_point=self._hover_snap,
                snap_type=self._hover_snap_type,
            )

        # Rubber-band
        if self._shift_drag and self._band_start and self._lmb_prev:
            pen = QPen(QColor("#ff8800"), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            bx, by = self._band_start.x(), self._band_start.y()
            painter.drawRect(
                QRectF(
                    QPointF(bx, by),
                    QPointF(self._lmb_prev.x(), self._lmb_prev.y()),
                )
            )

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
        n, s = len(self._polys), len(self._sel)
        info = f"{n} polylines" + (f"  ·  {s} selected" if s else "")
        if self._mode == "draw":
            pts_hint = f"  {len(self._draw_pts)} pt(s)" if self._draw_pts else ""
            info += f"  ·  DRAW{pts_hint}"
        elif self._mode == "edit":
            info += "  ·  EDIT"

        painter.setPen(QColor(DIM))
        painter.setFont(QFont("Helvetica", 10))
        painter.drawText(8, 18, info)

        zoom_pct = round(self._scale / max(self._fit_scale, 1e-9) * 100)
        if self._mode == "draw":
            hint = f"[{self._draw_primitive}: click points, Enter=finish, dbl-click=close, Esc=cancel]"
        elif self._mode == "edit":
            hint = "[drag vertex, dbl-click edge=insert, right-click vertex=delete, E=exit]"
        else:
            hint = "[Pan: Space/MMB · F=fit · Cmd/Ctrl+Z=undo · D/E mode · use Precision bar for snap/grid]"
        precision = []
        if self._grid_visible:
            precision.append(f"grid {self._grid_spacing:g}mm")
        if self._grid_snap:
            precision.append("snap")
        if precision:
            hint += "  ·  " + " / ".join(precision)
        painter.setFont(QFont("Helvetica", 9))
        painter.drawText(8, h - 8, f"{zoom_pct}%  {hint}")

        if not self._polys and not self._draw_pts:
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
        if self._cursor_wx is not None:
            painter.setPen(QColor(DIM))
            painter.setFont(QFont("Helvetica", 10))
            text = f"{self._cursor_wx:.2f}, {self._cursor_wy:.2f} mm"
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
            fry = 40
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

        painter.end()

    def _show_flash(self, text: str, duration_ms: int = 1200) -> None:
        """Show a brief flash indicator on the canvas."""
        self._flash_text = text
        if self._flash_timer is not None:
            self._flash_timer.stop()
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._clear_flash)
        self._flash_timer.start(duration_ms)
        self._redraw()

    def _clear_flash(self) -> None:
        self._flash_text = None
        self._flash_timer = None
        self._redraw()

    # ── Auto-dimension HUD (Fusion 360 style) ──────────────────────────────

    _DIM_STYLE = (
        "background: #1a1f2e; color: #ffffff; border: 1px solid #4a9eff;"
        "border-radius: 2px; font-size: 11px; font-family: 'Menlo';"
        "padding: 2px 4px;"
    )

    def _make_hud_edit(
        self,
        placeholder: str = "",
        width: int = 70,
        height: int = 20,
        align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignRight,
    ) -> QLineEdit:
        """Create a styled HUD QLineEdit parented to the viewport."""
        edit = QLineEdit(self.viewport())
        edit.setFixedWidth(width)
        edit.setFixedHeight(height)
        edit.setAlignment(align)
        edit.setStyleSheet(self._DIM_STYLE)
        if placeholder:
            edit.setPlaceholderText(placeholder)
        edit.installEventFilter(self)
        edit.show()
        return edit

    def _show_dim_inputs(self) -> None:
        """Create both distance and angle QLineEdits that float near the cursor."""
        self._dismiss_dim_inputs()
        if not self._draw_pts:
            return

        dist_edit = self._make_hud_edit("d:", 70)
        dist_edit.returnPressed.connect(self._apply_dim_input)
        # textEdited fires only on user keystrokes (not setText), so the dirty
        # flag tracks genuine typing; clearing the field resumes live updates.
        dist_edit.textEdited.connect(
            lambda t: setattr(self, "_dim_distance_dirty", bool(t.strip()))
        )
        self._dim_distance_edit = dist_edit
        self._dim_distance_dirty = False

        angle_edit = self._make_hud_edit("\u2220:", 55)
        angle_edit.returnPressed.connect(self._apply_dim_input)
        angle_edit.textEdited.connect(
            lambda t: setattr(self, "_dim_angle_dirty", bool(t.strip()))
        )
        self._dim_angle_edit = angle_edit
        self._dim_angle_dirty = False

    def _dismiss_dim_inputs(self) -> None:
        """Remove the auto-dimension HUD widgets."""
        if self._dim_distance_edit is not None:
            self._dim_distance_edit.hide()
            self._dim_distance_edit.deleteLater()
            self._dim_distance_edit = None
        if self._dim_angle_edit is not None:
            self._dim_angle_edit.hide()
            self._dim_angle_edit.deleteLater()
            self._dim_angle_edit = None
        self._dim_distance_dirty = False
        self._dim_angle_dirty = False

    # ── Inline selection-badge dimension editor ───────────────────────────────

    def _show_sel_dim_editor(self, axis: str, rect: QRectF) -> None:
        """Show a floating QLineEdit over a selection badge for direct editing.

        ``axis`` is "w"/"h" (bounding-box size) or, for a single selected
        2-point line, "l" (length) / "a" (absolute angle in degrees).
        """
        self._dismiss_sel_dim_editor()
        if axis in ("l", "a"):
            line_idx = self._selected_single_line()
            if line_idx is None:
                return
            (ax, ay), (bx, by) = self._polys[line_idx]
            if axis == "l":
                cur_val = math.hypot(bx - ax, by - ay)
            else:
                cur_val = math.degrees(math.atan2(by - ay, bx - ax))
        else:
            bounds = self._selection_bounds()
            if bounds is None:
                return
            x0, y0, x1, y1 = bounds
            cur_val = (x1 - x0) if axis == "w" else (y1 - y0)

        edit = self._make_hud_edit(
            width=max(int(rect.width()) + 10, 70),
            height=22,
            align=Qt.AlignmentFlag.AlignCenter,
        )
        edit.setText(f"{cur_val:.3f}")
        edit.selectAll()
        edit.move(int(rect.x()), int(rect.y()))
        edit.setFocus()
        edit.returnPressed.connect(lambda: self._apply_sel_dim_editor())
        edit.editingFinished.connect(lambda: self._apply_sel_dim_editor())
        self._sel_dim_edit = edit
        self._sel_dim_axis = axis

    def _apply_sel_dim_editor(self) -> None:
        if self._sel_dim_edit is None or self._sel_dim_axis is None:
            return
        text = self._sel_dim_edit.text().strip()
        axis = self._sel_dim_axis
        # Disconnect editingFinished before dismissing to avoid double-trigger
        try:
            self._sel_dim_edit.editingFinished.disconnect()
        except RuntimeError:
            pass
        self._dismiss_sel_dim_editor()
        try:
            val = float(text)
        except ValueError:
            return
        if axis == "a":
            # Absolute angle: any value is valid (normalized by trig)
            self._set_selected_line_angle(val)
            self._show_flash("Angle updated", 900)
            return
        if val <= 0:
            return
        if axis == "w":
            self._set_selected_width(val)
        elif axis == "h":
            self._set_selected_height(val)
        elif axis == "l":
            self._set_selected_line_length(val)
        self._show_flash("Dimension updated", 900)

    def _dismiss_sel_dim_editor(self) -> None:
        if self._sel_dim_edit is not None:
            self._sel_dim_edit.hide()
            self._sel_dim_edit.deleteLater()
            self._sel_dim_edit = None
        self._sel_dim_axis = None

    def _update_dim_positions(self, cx: float, cy: float) -> None:
        """Move the dim input widgets near cursor, avoiding snap label overlap.

        Positions the fields below-right of cursor with enough clearance so
        snap indicator icons and labels (drawn at +18, +4 from snap point)
        never get covered.
        """
        vp = self.viewport()
        vw = max(vp.width(), 100)
        vh = max(vp.height(), 100)
        # Default: below-right of cursor
        dx, dy = 28, 22
        # If near right edge, flip to left side
        if cx + dx + 80 > vw:
            dx = -100
        # If near bottom edge, flip above
        if cy + dy + 50 > vh:
            dy = -50
        if self._dim_distance_edit is not None:
            self._dim_distance_edit.move(int(cx + dx), int(cy + dy))
        if self._dim_angle_edit is not None:
            self._dim_angle_edit.move(int(cx + dx), int(cy + dy + 24))

    def _update_dim_values(self, distance: float, angle: float) -> None:
        """Update displayed values in the dim inputs, unless user has typed.

        When a field is focused but untouched, keep its text selected so the
        next keystroke replaces the live value instead of appending to it.
        """
        if self._dim_distance_edit is not None and not self._dim_distance_dirty:
            self._dim_distance_edit.setText(f"{distance:.2f}")
            if self._dim_distance_edit.hasFocus():
                self._dim_distance_edit.selectAll()
        if self._dim_angle_edit is not None and not self._dim_angle_dirty:
            self._dim_angle_edit.setText(f"{angle:.1f}")
            if self._dim_angle_edit.hasFocus():
                self._dim_angle_edit.selectAll()

    def _typed_draw_angle(self) -> float | None:
        """Return the user-typed segment angle (deg) if the angle field is dirty.

        Returns ``None`` when the field is auto-populated (not dirty) or does not
        parse, so callers only lock to a value the user explicitly entered.
        """
        if not getattr(self, "_dim_angle_dirty", False):
            return None
        if self._dim_angle_edit is None:
            return None
        text = self._dim_angle_edit.text().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _typed_draw_distance(self) -> float | None:
        """Return the user-typed segment length if the distance field is dirty."""
        if not getattr(self, "_dim_distance_dirty", False):
            return None
        if self._dim_distance_edit is None:
            return None
        text = self._dim_distance_edit.text().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _apply_dim_input(self) -> None:
        """Read distance/angle from the HUD fields and place a point."""
        if not self._draw_pts:
            return
        last_wx, last_wy = self._draw_pts[-1]
        try:
            dist_text = (
                self._dim_distance_edit.text().strip()
                if self._dim_distance_edit
                else ""
            )
            angle_text = (
                self._dim_angle_edit.text().strip() if self._dim_angle_edit else ""
            )
            if angle_text:
                angle_deg = float(angle_text)
            elif self._cursor_wx is not None and self._cursor_wy is not None:
                angle_deg = math.degrees(
                    math.atan2(
                        self._cursor_wy - last_wy,
                        self._cursor_wx - last_wx,
                    )
                )
            else:
                angle_deg = 0.0
            if dist_text:
                dist = float(dist_text)
            elif self._cursor_wx is not None and self._cursor_wy is not None:
                # Angle-only entry: project the cursor onto the typed-angle ray
                # so the length still tracks the pointer.
                ar = math.radians(angle_deg)
                vx = self._cursor_wx - last_wx
                vy = self._cursor_wy - last_wy
                dist = max(0.0, vx * math.cos(ar) + vy * math.sin(ar))
            else:
                return
            if dist <= 0:
                return
            angle_rad = math.radians(angle_deg)
            new_x = last_wx + dist * math.cos(angle_rad)
            new_y = last_wy + dist * math.sin(angle_rad)
            self._draw_pts.append((new_x, new_y))
            # Reset dirty flags so fields resume auto-updating
            self._dim_distance_dirty = False
            self._dim_angle_dirty = False
            self._refresh_draw_sidebar_state()
            self._redraw()
        except ValueError:
            pass

    # ── Inference / alignment lines ──────────────────────────────────────────

    def _show_measure_edit(self) -> None:
        """Show a QLineEdit overlay for editing the measured distance."""
        self._dismiss_measure_edit()
        if not self._measure_anchor or not self._measure_end:
            return
        ax, ay = self._measure_anchor
        hx, hy = self._measure_end
        dist = math.hypot(hx - ax, hy - ay)
        cax, cay = self._w2c(ax, ay)
        chx, chy = self._w2c(hx, hy)
        mx, my = (cax + chx) / 2, (cay + chy) / 2

        le = QLineEdit(self.viewport())
        le.setText(f"{dist:.2f}")
        le.setFixedWidth(100)
        le.setFixedHeight(24)
        le.setAlignment(Qt.AlignmentFlag.AlignCenter)
        le.setStyleSheet(
            "background: #001522; color: #ffffff; border: 1px solid #00d8ff;"
            "border-radius: 3px; font-size: 12px; font-weight: bold;"
        )
        le.move(int(mx - 50), int(my - 40))
        le.show()
        le.setFocus()
        le.selectAll()
        le.returnPressed.connect(self._apply_measure_scale)
        self._measure_edit = le

    def _dismiss_measure_edit(self) -> None:
        """Remove the measure distance QLineEdit overlay."""
        if self._measure_edit is not None:
            self._measure_edit.hide()
            self._measure_edit.deleteLater()
            self._measure_edit = None

    def _apply_measure_scale(self) -> None:
        """Read new distance from the edit overlay and scale all polylines."""
        if not self._measure_edit or not self._measure_anchor or not self._measure_end:
            self._dismiss_measure_edit()
            return
        try:
            new_dist = float(self._measure_edit.text())
        except ValueError:
            self._dismiss_measure_edit()
            return
        ax, ay = self._measure_anchor
        hx, hy = self._measure_end
        old_dist = math.hypot(hx - ax, hy - ay)
        if old_dist < 1e-9 or new_dist <= 0:
            self._dismiss_measure_edit()
            return
        factor = new_dist / old_dist
        self._scale_all(factor)
        self._dismiss_measure_edit()
        self._measure_locked = False
        self._measure_anchor = None
        self._measure_hover = None
        self._measure_end = None
        self._measure_snapped_a = False
        self._measure_snapped_b = False
        self._redraw()

    # ── Clipboard & nudge helpers ─────────────────────────────────────────────

