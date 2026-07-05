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

from PySide6.QtCore import QPointF, Qt
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
