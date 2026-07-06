"""Small hand-drawn vector icons for plain QPushButtons (toolbar/header
glyph buttons), rendered with QPainter instead of Unicode symbol characters.

Unicode glyphs like "⚙" (gear) or "⌘" depend on the platform's installed
fonts having that exact codepoint; when they don't, Qt falls back to a
generic/wrong glyph (e.g. the settings gear rendering as a plain circle).
Drawing the icon ourselves guarantees it looks the same everywhere.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def icon_from_painter(
    draw_fn: Callable[[QPainter, float, QColor], None],
    *,
    size: int = 18,
    color: str = "#e6edf3",
) -> QIcon:
    """Render ``draw_fn(painter, size, color)`` onto a transparent pixmap
    and wrap it as a QIcon."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw_fn(painter, float(size), QColor(color))
    painter.end()
    return QIcon(pixmap)


def _draw_gear(painter: QPainter, size: float, color: QColor) -> None:
    cx = cy = size / 2.0
    outer_r = size * 0.44
    inner_r = size * 0.30
    hole_r = size * 0.15
    tooth_w_deg = 20.0
    teeth = 8

    path = QPainterPath()
    for k in range(teeth):
        center_deg = k * (360.0 / teeth)
        a0 = math.radians(center_deg - tooth_w_deg / 2.0)
        a1 = math.radians(center_deg + tooth_w_deg / 2.0)
        gap_deg = 360.0 / teeth - tooth_w_deg
        b0 = math.radians(center_deg + tooth_w_deg / 2.0)
        b1 = math.radians(center_deg + tooth_w_deg / 2.0 + gap_deg)

        p_a0 = QPointF(cx + math.cos(a0) * outer_r, cy + math.sin(a0) * outer_r)
        p_a1 = QPointF(cx + math.cos(a1) * outer_r, cy + math.sin(a1) * outer_r)
        p_b0 = QPointF(cx + math.cos(b0) * inner_r, cy + math.sin(b0) * inner_r)
        p_b1 = QPointF(cx + math.cos(b1) * inner_r, cy + math.sin(b1) * inner_r)

        if k == 0:
            path.moveTo(p_a0)
        else:
            path.lineTo(p_a0)
        path.lineTo(p_a1)
        path.lineTo(p_b0)
        path.lineTo(p_b1)
    path.closeSubpath()

    hole = QPainterPath()
    hole.addEllipse(QPointF(cx, cy), hole_r, hole_r)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawPath(path.subtracted(hole))


def _draw_download(painter: QPainter, size: float, color: QColor) -> None:
    cx = size / 2.0
    top = size * 0.22
    shaft_bottom = size * 0.58
    pen = QPen(color, max(1.4, size * 0.09))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(QPointF(cx, top), QPointF(cx, shaft_bottom))

    head = size * 0.18
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPolygon(
        [
            QPointF(cx - head, shaft_bottom - head * 0.6),
            QPointF(cx + head, shaft_bottom - head * 0.6),
            QPointF(cx, shaft_bottom + head * 0.6),
        ]
    )

    tray_y = size * 0.78
    tray_pen = QPen(color, max(1.4, size * 0.09))
    tray_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(tray_pen)
    painter.drawLine(QPointF(size * 0.24, tray_y), QPointF(size * 0.76, tray_y))


def gear_icon(*, size: int = 18, color: str = "#e6edf3") -> QIcon:
    """Settings gear."""
    return icon_from_painter(_draw_gear, size=size, color=color)


def download_icon(*, size: int = 18, color: str = "#e6edf3") -> QIcon:
    """Download/"check for updates" arrow-into-tray."""
    return icon_from_painter(_draw_download, size=size, color=color)


# =============================================================================
# Draw-sidebar icon set — ported from the old ToolPickerDialog.ToolButton
# paintEvent (ellipse/rect/path drawing keyed on a normalized icon rect) plus
# new glyphs for the snapping/mode toggles that sidebar didn't previously
# expose. All follow the same (painter, size, color) -> None contract as
# _draw_gear/_draw_download above.
# =============================================================================

def _icon_rect(size: float, inset_frac: float = 0.16) -> QRectF:
    inset = size * inset_frac
    return QRectF(inset, inset, size - 2 * inset, size - 2 * inset)


def _line_pen(color: QColor, size: float) -> QPen:
    pen = QPen(color, max(1.2, size * 0.09))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _draw_polyline_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(r.left(), r.bottom())
    path.lineTo(r.center().x() - size * 0.05, r.center().y())
    path.lineTo(r.right(), r.top())
    painter.drawPath(path)


def _draw_spline_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(r.left(), r.bottom())
    path.cubicTo(r.left() + r.width() * 0.2, r.top(), r.right() - r.width() * 0.2, r.bottom(), r.right(), r.top())
    painter.drawPath(path)


def _draw_arc_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(r.left(), r.bottom())
    path.quadTo(r.center().x(), r.top() - size * 0.05, r.right(), r.bottom())
    painter.drawPath(path)


def _draw_bezier_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    p0 = QPointF(r.left(), r.bottom())
    p1 = QPointF(r.right(), r.top())
    # Handles offset only partway toward the corners (not a full-height
    # span) so they read as short control stubs, not a second curve.
    c1 = QPointF(r.left() + r.width() * 0.35, r.bottom() - r.height() * 0.75)
    c2 = QPointF(r.right() - r.width() * 0.35, r.top() + r.height() * 0.75)

    handle_pen = QPen(color.darker(160), max(0.8, size * 0.025))
    handle_pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(handle_pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(p0, c1)
    painter.drawLine(p1, c2)

    painter.setPen(_line_pen(color, size))
    path = QPainterPath()
    path.moveTo(p0)
    path.cubicTo(c1, c2, p1)
    painter.drawPath(path)

    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    for pt in (p0, p1):
        painter.drawEllipse(pt, size * 0.055, size * 0.055)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(handle_pen)
    for pt in (c1, c2):
        painter.drawEllipse(pt, size * 0.03, size * 0.03)


def _draw_rectangle_icon(painter: QPainter, size: float, color: QColor) -> None:
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(_icon_rect(size))


def _draw_slot_icon(painter: QPainter, size: float, color: QColor) -> None:
    # Deliberately non-square (wide, short) — a stadium/capsule shape reads
    # as "slot" only when it's clearly elongated, not a circle.
    square = _icon_rect(size)
    height = square.height() * 0.5
    r = QRectF(square.left(), square.center().y() - height / 2.0, square.width(), height)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    radius = height / 2.0
    painter.drawRoundedRect(r, radius, radius)


def _draw_circle_icon(painter: QPainter, size: float, color: QColor) -> None:
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(_icon_rect(size))


def _draw_ellipse_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(r.adjusted(0, r.height() * 0.16, 0, -r.height() * 0.16))


def _draw_polygon_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    cx, cy = r.center().x(), r.center().y()
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    n = 6
    for i in range(n):
        ang = math.radians(-90 + i * 360.0 / n)
        pt = QPointF(cx + math.cos(ang) * r.width() / 2.0, cy + math.sin(ang) * r.height() / 2.0)
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    path.closeSubpath()
    painter.drawPath(path)


def _draw_text_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.drawLine(QPointF(r.left(), r.top()), QPointF(r.right(), r.top()))
    painter.drawLine(QPointF(r.center().x(), r.top()), QPointF(r.center().x(), r.bottom()))


def _draw_grid_snap_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    dot_r = size * 0.06
    for gx in (r.left(), r.center().x(), r.right()):
        for gy in (r.top(), r.center().y(), r.bottom()):
            painter.drawEllipse(QPointF(gx, gy), dot_r, dot_r)


def _draw_angle_snap_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    origin = QPointF(r.left(), r.bottom())
    painter.setPen(_line_pen(color, size))
    painter.drawLine(origin, QPointF(r.right(), r.bottom()))
    painter.drawLine(origin, QPointF(r.right(), r.top()))
    arc_r = r.width() * 0.4
    arc_rect = QRectF(origin.x() - arc_r, origin.y() - arc_r, arc_r * 2, arc_r * 2)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(arc_rect, 0, 45 * 16)


def _draw_constraint_icon(painter: QPainter, size: float, color: QColor) -> None:
    """Axis-lock crosshair — H/V/45 draw-constraint lock (distinct from the
    angle-snap protractor glyph)."""
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    cx, cy = r.center().x(), r.center().y()
    painter.drawLine(QPointF(r.left(), cy), QPointF(r.right(), cy))
    painter.drawLine(QPointF(cx, r.top()), QPointF(cx, r.bottom()))
    dashed = QPen(color.darker(160), max(0.8, size * 0.05))
    dashed.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(dashed)
    painter.drawLine(QPointF(r.left(), r.top()), QPointF(r.right(), r.bottom()))


def _draw_vertex_snap_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(r.left(), r.bottom())
    path.lineTo(r.right(), r.bottom())
    path.lineTo(r.right(), r.top())
    painter.drawPath(path)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(QPointF(r.right(), r.bottom()), size * 0.09, size * 0.09)


def _draw_edge_snap_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    p0 = QPointF(r.left(), r.bottom())
    p1 = QPointF(r.right(), r.top())
    painter.drawLine(p0, p1)
    mid = QPointF((p0.x() + p1.x()) / 2.0, (p0.y() + p1.y()) / 2.0)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(mid, size * 0.09, size * 0.09)


def _draw_master_snap_icon(painter: QPainter, size: float, color: QColor) -> None:
    """Horseshoe magnet — the "all snapping" master toggle."""
    r = _icon_rect(size)
    pen = _line_pen(color, size)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    rect = QRectF(r.left(), r.top(), r.width(), r.height() * 1.3)
    painter.drawArc(rect, 0, 180 * 16)
    leg_bottom = r.top() + r.height() * 0.65
    painter.drawLine(QPointF(r.left(), r.top() + r.height() * 0.35), QPointF(r.left(), leg_bottom))
    painter.drawLine(QPointF(r.right(), r.top() + r.height() * 0.35), QPointF(r.right(), leg_bottom))
    tip_pen = QPen(color, max(2.0, size * 0.16))
    tip_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    painter.setPen(tip_pen)
    painter.drawLine(
        QPointF(r.left(), leg_bottom), QPointF(r.left(), leg_bottom + size * 0.08)
    )
    painter.drawLine(
        QPointF(r.right(), leg_bottom), QPointF(r.right(), leg_bottom + size * 0.08)
    )


def _draw_split_icon(painter: QPainter, size: float, color: QColor) -> None:
    """Scissors — auto-split on draw."""
    r = _icon_rect(size)
    pen = _line_pen(color, size)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(QPointF(r.left(), r.top()), QPointF(r.right(), r.bottom()))
    painter.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.top()))
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QPointF(r.left(), r.top()), size * 0.07, size * 0.07)
    painter.drawEllipse(QPointF(r.left(), r.bottom()), size * 0.07, size * 0.07)


def _draw_construction_icon(painter: QPainter, size: float, color: QColor) -> None:
    """Dashed triangle — construction (reference-only) geometry."""
    r = _icon_rect(size)
    pen = _line_pen(color, size)
    pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(r.center().x(), r.top())
    path.lineTo(r.right(), r.bottom())
    path.lineTo(r.left(), r.bottom())
    path.closeSubpath()
    painter.drawPath(path)


def _draw_dimension_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    pen = _line_pen(color, size)
    painter.setPen(pen)
    y = r.center().y()
    painter.drawLine(QPointF(r.left(), r.top()), QPointF(r.left(), r.bottom()))
    painter.drawLine(QPointF(r.right(), r.top()), QPointF(r.right(), r.bottom()))
    painter.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
    arrow = size * 0.08
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPolygon(
        [
            QPointF(r.left(), y),
            QPointF(r.left() + arrow, y - arrow * 0.6),
            QPointF(r.left() + arrow, y + arrow * 0.6),
        ]
    )
    painter.drawPolygon(
        [
            QPointF(r.right(), y),
            QPointF(r.right() - arrow, y - arrow * 0.6),
            QPointF(r.right() - arrow, y + arrow * 0.6),
        ]
    )


def _draw_measure_icon(painter: QPainter, size: float, color: QColor) -> None:
    """Ruler with tick marks."""
    r = _icon_rect(size)
    pen = _line_pen(color, size)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(r)
    ticks = 4
    for i in range(1, ticks):
        x = r.left() + r.width() * i / ticks
        painter.drawLine(QPointF(x, r.top()), QPointF(x, r.top() + r.height() * 0.4))


def _draw_finish_icon(painter: QPainter, size: float, color: QColor) -> None:
    """Checkmark — finish open polyline."""
    r = _icon_rect(size)
    pen = _line_pen(color, size)
    painter.setPen(pen)
    path = QPainterPath()
    path.moveTo(r.left(), r.center().y())
    path.lineTo(r.left() + r.width() * 0.38, r.bottom())
    path.lineTo(r.right(), r.top())
    painter.drawPath(path)


def _draw_close_path_icon(painter: QPainter, size: float, color: QColor) -> None:
    """An open ring (gap at the top) — distinct from the plain closed
    circle used for the Circle draw tool, reading as "not yet closed"."""
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    gap_deg = 50
    painter.drawArc(r, (90 + gap_deg // 2) * 16, (360 - gap_deg) * 16)


def _draw_undo_point_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    pen = _line_pen(color, size)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    rect = QRectF(r.left(), r.top(), r.width(), r.height())
    painter.drawArc(rect, 20 * 16, 300 * 16)
    tip = QPointF(r.left(), r.top() + r.height() * 0.15)
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPolygon(
        [
            tip,
            QPointF(tip.x() + size * 0.14, tip.y() - size * 0.03),
            QPointF(tip.x() + size * 0.02, tip.y() + size * 0.14),
        ]
    )


def _draw_cancel_icon(painter: QPainter, size: float, color: QColor) -> None:
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.drawLine(QPointF(r.left(), r.top()), QPointF(r.right(), r.bottom()))
    painter.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.top()))


def _draw_select_arrow_icon(painter: QPainter, size: float, color: QColor) -> None:
    """Cursor/selection arrow — back to select mode."""
    r = _icon_rect(size, inset_frac=0.2)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawPolygon(
        [
            QPointF(r.left(), r.top()),
            QPointF(r.left(), r.bottom()),
            QPointF(r.left() + r.width() * 0.55, r.bottom() - r.height() * 0.32),
            QPointF(r.left() + r.width() * 0.78, r.bottom()),
            QPointF(r.left() + r.width() * 0.92, r.bottom() - r.height() * 0.12),
            QPointF(r.left() + r.width() * 0.65, r.bottom() - r.height() * 0.42),
            QPointF(r.right(), r.bottom() - r.height() * 0.42),
        ]
    )


def _draw_smooth_chaikin_icon(painter: QPainter, size: float, color: QColor) -> None:
    """A sharp corner with a small chamfer cut across it — Chaikin's
    corner-cutting, in one glyph."""
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    apex = QPointF(r.center().x(), r.top())
    path = QPainterPath()
    path.moveTo(r.left(), r.bottom())
    path.lineTo(apex)
    path.lineTo(r.right(), r.bottom())
    painter.drawPath(path)
    cut_pen = QPen(color.lighter(140), max(0.8, size * 0.05))
    cut_pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(cut_pen)
    span = size * 0.14
    painter.drawLine(
        QPointF(apex.x() - span, apex.y() + span * 0.8),
        QPointF(apex.x() + span, apex.y() + span * 0.8),
    )


def _draw_smooth_gaussian_icon(painter: QPainter, size: float, color: QColor) -> None:
    """A bell curve — Gaussian neighbor-averaging."""
    r = _icon_rect(size)
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.bottom()))
    left = QPointF(r.left(), r.bottom())
    peak = QPointF(r.center().x(), r.top())
    right = QPointF(r.right(), r.bottom())
    # Control points pulled in horizontally *and* up, so the rise curves
    # gently into a rounded peak instead of meeting at a sharp point.
    path = QPainterPath()
    path.moveTo(left)
    path.cubicTo(
        QPointF(left.x() + r.width() * 0.32, left.y()),
        QPointF(peak.x() - r.width() * 0.22, peak.y() + r.height() * 0.12),
        peak,
    )
    path.cubicTo(
        QPointF(peak.x() + r.width() * 0.22, peak.y() + r.height() * 0.12),
        QPointF(right.x() - r.width() * 0.32, right.y()),
        right,
    )
    painter.drawPath(path)


def _draw_smooth_catmull_icon(painter: QPainter, size: float, color: QColor) -> None:
    """A smooth curve threaded exactly through a few dots — distinct from
    the plain "spline" glyph, which has no through-points, matching
    Catmull-Rom's interpolating (not approximating) behavior."""
    r = _icon_rect(size)
    pts = [
        QPointF(r.left(), r.bottom()),
        QPointF(r.left() + r.width() * 0.35, r.top() + r.height() * 0.25),
        QPointF(r.left() + r.width() * 0.65, r.bottom() - r.height() * 0.25),
        QPointF(r.right(), r.top()),
    ]
    painter.setPen(_line_pen(color, size))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(pts[0])
    path.cubicTo(pts[1], pts[1], pts[2])
    path.cubicTo(pts[2], pts[2], pts[3])
    painter.drawPath(path)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    for pt in pts:
        painter.drawEllipse(pt, size * 0.055, size * 0.055)


_ICON_FACTORIES: dict[str, Callable[[QPainter, float, QColor], None]] = {
    "polyline": _draw_polyline_icon,
    "spline": _draw_spline_icon,
    "arc": _draw_arc_icon,
    "bezier": _draw_bezier_icon,
    "rectangle": _draw_rectangle_icon,
    "slot": _draw_slot_icon,
    "circle": _draw_circle_icon,
    "ellipse": _draw_ellipse_icon,
    "polygon": _draw_polygon_icon,
    "text": _draw_text_icon,
    "grid_snap": _draw_grid_snap_icon,
    "angle_snap": _draw_angle_snap_icon,
    "constraint": _draw_constraint_icon,
    "vertex_snap": _draw_vertex_snap_icon,
    "edge_snap": _draw_edge_snap_icon,
    "master_snap": _draw_master_snap_icon,
    "split": _draw_split_icon,
    "construction": _draw_construction_icon,
    "dimension": _draw_dimension_icon,
    "measure": _draw_measure_icon,
    "finish": _draw_finish_icon,
    "close_path": _draw_close_path_icon,
    "undo_point": _draw_undo_point_icon,
    "cancel": _draw_cancel_icon,
    "select_arrow": _draw_select_arrow_icon,
    "smooth_chaikin": _draw_smooth_chaikin_icon,
    "smooth_gaussian": _draw_smooth_gaussian_icon,
    "smooth_catmull": _draw_smooth_catmull_icon,
}


def tool_icon(name: str, *, size: int = 20, color: str = "#c9d1d9") -> QIcon:
    """Look up one of the draw-sidebar icons by name (see `_ICON_FACTORIES`)."""
    draw_fn = _ICON_FACTORIES[name]
    return icon_from_painter(draw_fn, size=size, color=color)
