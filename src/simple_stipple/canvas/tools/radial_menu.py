"""Canvas radial quick-menu service.

This module owns radial-menu state, command filtering, hit-testing, dispatch,
and painting. It remains deliberately independent of other canvas tool modes.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF

from simple_stipple.canvas import commands as canvas_commands
from simple_stipple.platform.settings import DEFAULT_RADIAL_MENU_TOOLS, RADIAL_MENU_SHORT_LABELS


class RadialMenuService:
    """Own radial-menu state, hit-testing, dispatch, and painting."""

    def __init__(self, host) -> None:
        self._host = host

    def _toggle_radial_menu(self) -> None:
        if self._host._radial_active:
            self._host._radial_active = False
            self._host._radial_hover_index = None
            self._host._redraw()
            return
        if self._host._cursor_wx is not None and self._host._cursor_wy is not None:
            cx, cy = self._host._w2c(self._host._cursor_wx, self._host._cursor_wy)
        else:
            cx, cy = self._host.width() / 2.0, self._host.height() / 2.0
        self._host._radial_center_c = self._clamped_radial_center(cx, cy)
        self._host._radial_active = True
        self._host._radial_hover_index = None
        self._host._redraw()

    # A quick-launcher wheel: every wedge is a real canvas Command id (draw
    # primitives, edit/selection ops, booleans, view/grid toggles, ...) so
    # the available pool is exactly "everything commands.py knows how to
    # run" — no separate/parallel action list to keep in sync. Which ones
    # appear, and in what order, is user-customizable — see
    # set_radial_menu_tools() — so the wedge count/angle is computed from
    # len(self._host._radial_tools), not a fixed number.
    _RADIAL_OUTER = 104.0
    _RADIAL_INNER = 36.0
    _RADIAL_MIN_TOOLS = 3
    _RADIAL_MAX_TOOLS = 12

    @classmethod
    def _radial_geometry(cls, n: int) -> tuple[float, float]:
        """(outer, inner) radii — grows past 6 wedges so more items still
        leave each label enough room; shared by hit-testing and painting so
        the two can never disagree about where a wedge actually is."""
        grow = max(0, n - 6)
        return cls._RADIAL_OUTER + grow * 9.0, cls._RADIAL_INNER + grow * 2.0

    def _clamped_radial_center(self, cx: float, cy: float) -> QPoint:
        """Keep the cursor-launched wheel reachable at every canvas edge."""
        outer, _inner = self._radial_geometry(len(self._host._radial_tools))
        margin = int(math.ceil(outer + 4.0))

        def _axis(value: float, extent: int) -> int:
            if extent <= margin * 2:
                return extent // 2
            return max(margin, min(int(value), extent - margin))

        return QPoint(_axis(cx, self._host.width()), _axis(cy, self._host.height()))

    def set_radial_menu_tools(self, tools: list[str] | None) -> None:
        """Set which commands appear as radial-menu wedges, and in what order.

        Unknown/hidden ids are dropped and duplicates collapsed (first
        occurrence wins); if fewer than _RADIAL_MIN_TOOLS survive, falls back
        to the default set entirely rather than showing a degenerate menu.
        """
        valid = {c.id for c in canvas_commands.COMMANDS if not c.hidden}
        seen: set[str] = set()
        cleaned: list[str] = []
        for tool_id in tools or []:
            if tool_id in valid and tool_id not in seen:
                seen.add(tool_id)
                cleaned.append(tool_id)
        if len(cleaned) < self._RADIAL_MIN_TOOLS:
            cleaned = list(DEFAULT_RADIAL_MENU_TOOLS)
        self._host._radial_tools = cleaned[: self._RADIAL_MAX_TOOLS]
        if self._host._radial_active:
            self._host._radial_hover_index = None
            self._host._redraw()

    def _radial_index_at(self, x: float, y: float) -> int | None:
        n = len(self._host._radial_tools)
        if n == 0:
            return None
        outer, inner = self._radial_geometry(n)
        dx = x - self._host._radial_center_c.x()
        dy = y - self._host._radial_center_c.y()
        r = math.hypot(dx, dy)
        if r < inner - 4.0 or r > outer + 18.0:
            return None
        slice_deg = 360.0 / n
        angle = (math.degrees(math.atan2(-dy, dx)) + 360.0) % 360.0
        return int((angle + slice_deg / 2.0) // slice_deg) % n

    def _execute_radial_action(self, idx: int) -> None:
        if not (0 <= idx < len(self._host._radial_tools)):
            return
        canvas_commands.run(self._host, self._host._radial_tools[idx])

    def _draw_radial_icon(  # noqa: C901 - command icon catalog is intentionally flat
        self,
        painter: QPainter,
        cmd_id: str,
        cx: float,
        cy: float,
        size: float,
        color: QColor,
        label: str = "",
    ) -> None:
        painter.save()
        painter.setPen(QPen(color, 1.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        half = size / 2.0

        def _two_circles(mode: str) -> None:
            ra = half * 0.62
            ax, bx = cx - half * 0.32, cx + half * 0.32
            path_a, path_b = QPainterPath(), QPainterPath()
            path_a.addEllipse(QPointF(ax, cy), ra, ra)
            path_b.addEllipse(QPointF(bx, cy), ra, ra)
            if mode == "union":
                painter.fillPath(path_a.united(path_b), color)
            elif mode == "subtract":
                painter.fillPath(path_a.subtracted(path_b), color)
                painter.drawPath(path_b)
            elif mode == "intersect":
                painter.drawPath(path_a)
                painter.drawPath(path_b)
                painter.fillPath(path_a.intersected(path_b), color)
            elif mode == "divide":
                painter.drawPath(path_a)
                painter.drawPath(path_b)
                painter.drawLine(QPointF(cx, cy - ra), QPointF(cx, cy + ra))

        if cmd_id in ("canvas.rectangle",):
            painter.drawRoundedRect(QRectF(cx - half, cy - half * 0.7, size, size * 0.7), 2.0, 2.0)
        elif cmd_id == "canvas.circle":
            painter.drawEllipse(QPointF(cx, cy), half, half)
        elif cmd_id == "canvas.polygon":
            pts = [
                QPointF(
                    cx + math.cos(math.radians(60 * k - 90)) * half,
                    cy + math.sin(math.radians(60 * k - 90)) * half,
                )
                for k in range(6)
            ]
            painter.drawPolygon(QPolygonF(pts))
        elif cmd_id == "canvas.line":
            painter.drawLine(
                QPointF(cx - half, cy + half * 0.6), QPointF(cx + half, cy - half * 0.6)
            )
            painter.setBrush(color)
            painter.drawEllipse(QPointF(cx - half, cy + half * 0.6), 1.4, 1.4)
            painter.drawEllipse(QPointF(cx + half, cy - half * 0.6), 1.4, 1.4)
        elif cmd_id == "canvas.arc":
            painter.drawArc(QRectF(cx - half, cy - half, size, size), 0, 90 * 16)
        elif cmd_id == "canvas.ellipse":
            painter.drawEllipse(QRectF(cx - half, cy - half * 0.6, size, size * 0.6))
        elif cmd_id == "canvas.polyline":
            path = QPainterPath()
            path.moveTo(cx - half, cy + half * 0.5)
            path.lineTo(cx - half * 0.15, cy - half * 0.6)
            path.lineTo(cx + half, cy + half * 0.2)
            painter.drawPath(path)
            painter.setBrush(color)
            for px, py in (
                (cx - half, cy + half * 0.5),
                (cx - half * 0.15, cy - half * 0.6),
                (cx + half, cy + half * 0.2),
            ):
                painter.drawEllipse(QPointF(px, py), 1.3, 1.3)
        elif cmd_id == "canvas.spline":
            path = QPainterPath()
            path.moveTo(cx - half, cy)
            path.cubicTo(cx - half * 0.3, cy - half, cx + half * 0.3, cy + half, cx + half, cy)
            painter.drawPath(path)
        elif cmd_id == "mode.pen":
            path = QPainterPath()
            path.moveTo(cx - half, cy + half * 0.5)
            path.cubicTo(
                cx - half * 0.2, cy - half, cx + half * 0.2, cy + half, cx + half, cy - half * 0.5
            )
            painter.drawPath(path)
            painter.setBrush(color)
            painter.drawEllipse(QPointF(cx - half, cy + half * 0.5), 1.4, 1.4)
            painter.drawEllipse(QPointF(cx + half, cy - half * 0.5), 1.4, 1.4)
        elif cmd_id == "mode.draw":
            painter.drawLine(QPointF(cx - half, cy + half), QPointF(cx + half * 0.4, cy - half))
            tip = QPolygonF(
                [
                    QPointF(cx + half * 0.4, cy - half),
                    QPointF(cx + half, cy - half * 0.7),
                    QPointF(cx + half * 0.7, cy - half * 0.1),
                ]
            )
            painter.setBrush(color)
            painter.drawPolygon(tip)
        elif cmd_id == "mode.edit":
            painter.drawRect(QRectF(cx - half, cy - half, size, size))
            painter.setBrush(color)
            for corner in (
                (cx - half, cy - half),
                (cx + half, cy - half),
                (cx - half, cy + half),
                (cx + half, cy + half),
            ):
                painter.drawRect(QRectF(corner[0] - 1.6, corner[1] - 1.6, 3.2, 3.2))
        elif cmd_id == "edit.undo":
            painter.drawArc(QRectF(cx - half, cy - half, size, size), 30 * 16, 260 * 16)
            self._draw_arrowhead(painter, cx - half * 0.75, cy - half * 0.55, 200, color)
        elif cmd_id == "edit.redo":
            painter.drawArc(QRectF(cx - half, cy - half, size, size), 250 * 16, 260 * 16)
            self._draw_arrowhead(painter, cx + half * 0.75, cy - half * 0.55, -20, color)
        elif cmd_id == "clipboard.cut":
            painter.drawLine(
                QPointF(cx - half, cy - half), QPointF(cx + half * 0.2, cy + half * 0.3)
            )
            painter.drawLine(
                QPointF(cx - half, cy + half), QPointF(cx + half * 0.2, cy - half * 0.3)
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx - half, cy - half), 2.2, 2.2)
            painter.drawEllipse(QPointF(cx - half, cy + half), 2.2, 2.2)
            painter.drawLine(QPointF(cx + half * 0.2, cy), QPointF(cx + half, cy))
        elif cmd_id == "clipboard.copy":
            painter.drawRoundedRect(
                QRectF(cx - half, cy - half * 0.75, size * 0.75, size * 0.75), 2.0, 2.0
            )
            painter.drawRoundedRect(
                QRectF(cx - half * 0.25, cy - half * 0.15, size * 0.75, size * 0.75), 2.0, 2.0
            )
        elif cmd_id == "clipboard.paste":
            painter.drawRoundedRect(
                QRectF(cx - half * 0.7, cy - half * 0.8, size * 0.7, size), 1.5, 1.5
            )
            painter.drawRoundedRect(
                QRectF(cx - half * 0.3, cy - half, size * 0.3, size * 0.25), 1.0, 1.0
            )
        elif cmd_id in ("edit.duplicate", "edit.duplicate_offset"):
            painter.drawRoundedRect(
                QRectF(cx - half, cy - half * 0.2, size * 0.65, size * 0.65), 2.0, 2.0
            )
            painter.drawRoundedRect(
                QRectF(cx - half * 0.35, cy - half, size * 0.65, size * 0.65), 2.0, 2.0
            )
            if cmd_id == "edit.duplicate_offset":
                self._draw_arrowhead(painter, cx + half * 0.55, cy - half * 0.55, -45, color)
        elif cmd_id == "edit.array_grid":
            for dx_ in (-half * 0.55, half * 0.55):
                for dy_ in (-half * 0.55, half * 0.55):
                    painter.drawRect(QRectF(cx + dx_ - 3.0, cy + dy_ - 3.0, 6.0, 6.0))
        elif cmd_id == "edit.array_radial":
            painter.setBrush(color)
            for k in range(5):
                a = math.radians(72 * k - 90)
                painter.drawEllipse(
                    QPointF(cx + math.cos(a) * half * 0.75, cy + math.sin(a) * half * 0.75),
                    2.0,
                    2.0,
                )
        elif cmd_id == "edit.delete":
            painter.drawLine(QPointF(cx - half, cy - half), QPointF(cx + half, cy + half))
            painter.drawLine(QPointF(cx - half, cy + half), QPointF(cx + half, cy - half))
        elif cmd_id == "select.all":
            pen = painter.pen()
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(QRectF(cx - half, cy - half * 0.7, size, size * 0.7))
        elif cmd_id == "select.none":
            pen = painter.pen()
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(QRectF(cx - half, cy - half * 0.7, size, size * 0.7))
            painter.setPen(QPen(color, 1.4))
            painter.drawLine(
                QPointF(cx - half, cy + half * 0.7), QPointF(cx + half, cy - half * 0.7)
            )
        elif cmd_id == "select.invert":
            painter.drawRect(QRectF(cx - half, cy - half * 0.5, size * 0.45, size * 0.9))
            painter.setBrush(color)
            painter.drawRect(QRectF(cx + half * 0.1, cy - half * 0.5, size * 0.45, size * 0.9))
        elif cmd_id in ("group.create", "group.dissolve"):
            gap = 3.0 if cmd_id == "group.dissolve" else 0.0
            painter.drawRoundedRect(
                QRectF(cx - half, cy - half * 0.8, size * 0.42 - gap, size * 0.8), 2.0, 2.0
            )
            painter.drawRoundedRect(
                QRectF(cx + gap, cy - half * 0.8, size * 0.42 - gap, size * 0.8), 2.0, 2.0
            )
        elif cmd_id in ("path.close", "path.open"):
            span = 260 * 16 if cmd_id == "path.open" else 350 * 16
            painter.drawArc(QRectF(cx - half, cy - half, size, size), 0, span)
            if cmd_id == "path.close":
                painter.setBrush(color)
                painter.drawEllipse(QPointF(cx + half, cy), 1.6, 1.6)
        elif cmd_id == "path.offset":
            painter.drawRoundedRect(QRectF(cx - half, cy - half, size, size), 2.0, 2.0)
            painter.drawRoundedRect(
                QRectF(cx - half * 0.55, cy - half * 0.55, size * 0.55, size * 0.55), 1.5, 1.5
            )
        elif cmd_id == "construction.toggle":
            pen = painter.pen()
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(
                QPointF(cx - half, cy + half * 0.5), QPointF(cx + half, cy - half * 0.5)
            )
        elif cmd_id in ("vertex.round", "vertex.chamfer"):
            path = QPainterPath()
            path.moveTo(cx - half, cy - half * 0.6)
            if cmd_id == "vertex.round":
                path.lineTo(cx - half * 0.35, cy - half * 0.6)
                path.quadTo(cx + half, cy - half * 0.6, cx + half, cy + half)
            else:
                path.lineTo(cx + half * 0.3, cy - half * 0.6)
                path.lineTo(cx + half, cy + half * 0.15)
                path.lineTo(cx + half, cy + half)
            painter.drawPath(path)
        elif cmd_id in ("text.add", "text.attach_to_path"):
            font = painter.font()
            font.setPointSizeF(size * 0.62)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                QRectF(cx - half, cy - half, size, size), Qt.AlignmentFlag.AlignCenter, "A"
            )
            if cmd_id == "text.attach_to_path":
                painter.drawArc(QRectF(cx - half, cy + half * 0.3, size, size), 200 * 16, 140 * 16)
        elif cmd_id == "path.simplify":
            for k in range(4):
                a = math.radians(90 * k)
                painter.drawLine(
                    QPointF(cx + math.cos(a) * half * 0.4, cy + math.sin(a) * half * 0.4),
                    QPointF(cx + math.cos(a) * half, cy + math.sin(a) * half),
                )
        elif cmd_id == "path.smooth":
            path = QPainterPath()
            path.moveTo(cx - half, cy)
            path.cubicTo(cx - half * 0.5, cy - half, cx - half * 0.15, cy + half, cx, cy)
            path.cubicTo(cx + half * 0.15, cy - half, cx + half * 0.5, cy + half, cx + half, cy)
            painter.drawPath(path)
        elif cmd_id == "path.fit_curve":
            # Rough/dense original points (dots on a jagged path)...
            jagged = [
                (cx - half, cy + half * 0.3),
                (cx - half * 0.45, cy - half * 0.5),
                (cx + half * 0.1, cy + half * 0.6),
                (cx + half * 0.55, cy - half * 0.4),
                (cx + half, cy + half * 0.1),
            ]
            painter.setBrush(color)
            for px, py in jagged:
                painter.drawEllipse(QPointF(px, py), 1.1, 1.1)
            # ...replaced by one smooth fitted curve through the same span.
            path = QPainterPath()
            path.moveTo(jagged[0][0], jagged[0][1])
            path.cubicTo(
                cx - half * 0.3,
                cy - half * 0.7,
                cx + half * 0.3,
                cy + half * 0.7,
                jagged[-1][0],
                jagged[-1][1],
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        elif cmd_id == "boolean.union":
            _two_circles("union")
        elif cmd_id == "boolean.subtract":
            _two_circles("subtract")
        elif cmd_id == "boolean.intersect":
            _two_circles("intersect")
        elif cmd_id == "boolean.divide":
            _two_circles("divide")
        elif cmd_id == "mode.trim":
            painter.drawLine(QPointF(cx - half, cy), QPointF(cx - 3.0, cy))
            painter.drawLine(QPointF(cx + 3.0, cy), QPointF(cx + half, cy))
            painter.drawLine(QPointF(cx - 3.0, cy - 3.0), QPointF(cx + 3.0, cy + 3.0))
            painter.drawLine(QPointF(cx - 3.0, cy + 3.0), QPointF(cx + 3.0, cy - 3.0))
        elif cmd_id == "mode.extend":
            painter.drawLine(QPointF(cx - half, cy), QPointF(cx + half * 0.4, cy))
            self._draw_arrowhead(painter, cx + half * 0.75, cy, 0, color)
        elif cmd_id == "measure.toggle":
            painter.drawRect(QRectF(cx - half, cy - half * 0.45, size, size * 0.45))
            for k in range(1, 4):
                x = cx - half + (size / 4.0) * k
                painter.drawLine(QPointF(x, cy - half * 0.45), QPointF(x, cy - half * 0.1))
        elif cmd_id == "mode.dimension":
            painter.drawLine(
                QPointF(cx - half, cy - half * 0.6), QPointF(cx - half, cy + half * 0.6)
            )
            painter.drawLine(
                QPointF(cx + half, cy - half * 0.6), QPointF(cx + half, cy + half * 0.6)
            )
            painter.drawLine(QPointF(cx - half, cy), QPointF(cx + half, cy))
            self._draw_arrowhead(painter, cx - half, cy, 0, color, size=3.0)
            self._draw_arrowhead(painter, cx + half, cy, 180, color, size=3.0)
        elif cmd_id == "view.fit":
            for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                painter.drawLine(
                    QPointF(cx + sx * half * 0.25, cy + sy * half * 0.25),
                    QPointF(cx + sx * half, cy + sy * half),
                )
        elif cmd_id in ("view.zoom_in", "view.zoom_out"):
            painter.drawEllipse(
                QPointF(cx - half * 0.15, cy - half * 0.15), half * 0.55, half * 0.55
            )
            painter.drawLine(
                QPointF(cx + half * 0.25, cy + half * 0.25), QPointF(cx + half, cy + half)
            )
            r = half * 0.55 * 0.5
            painter.drawLine(
                QPointF(cx - half * 0.15 - r, cy - half * 0.15),
                QPointF(cx - half * 0.15 + r, cy - half * 0.15),
            )
            if cmd_id == "view.zoom_in":
                painter.drawLine(
                    QPointF(cx - half * 0.15, cy - half * 0.15 - r),
                    QPointF(cx - half * 0.15, cy - half * 0.15 + r),
                )
        elif cmd_id == "view.rulers":
            painter.drawLine(QPointF(cx - half, cy), QPointF(cx + half, cy))
            for k in range(5):
                x = cx - half + (size / 4.0) * k
                painter.drawLine(
                    QPointF(x, cy), QPointF(x, cy - (half * 0.5 if k % 2 == 0 else half * 0.25))
                )
        elif cmd_id in ("grid.toggle", "grid.snap", "grid.coarser", "grid.finer"):
            step = size / (2.0 if cmd_id == "grid.coarser" else 4.0)
            x = cx - half
            while x <= cx + half + 0.01:
                painter.drawLine(QPointF(x, cy - half), QPointF(x, cy + half))
                x += step
            y = cy - half
            while y <= cy + half + 0.01:
                painter.drawLine(QPointF(cx - half, y), QPointF(cx + half, y))
                y += step
            if cmd_id == "grid.snap":
                painter.setBrush(color)
                painter.drawEllipse(QPointF(cx, cy), 2.0, 2.0)
        else:
            # Generic fallback: a rounded badge with the label's initials,
            # so every pool entry still gets *some* recognizable glyph.
            painter.drawRoundedRect(QRectF(cx - half, cy - half, size, size), 3.0, 3.0)
            initials = "".join(w[0] for w in label.split()[:2]).upper() or "?"
            font = painter.font()
            font.setPointSizeF(size * 0.44)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                QRectF(cx - half, cy - half, size, size), Qt.AlignmentFlag.AlignCenter, initials
            )
        painter.restore()

    @staticmethod
    def _radial_chord_half(ty: float, cy: float, outer: float) -> float:
        """Half-width of the disc's horizontal chord at label height ``ty`` —
        the widest a label can ever be at that height without spilling past
        the wheel's outer edge, regardless of angle or word length."""
        dy_from_center = ty - cy
        return math.sqrt(max(0.0, outer * outer - dy_from_center * dy_from_center))

    @staticmethod
    def _draw_arrowhead(
        painter: QPainter, x: float, y: float, angle_deg: float, color: QColor, size: float = 3.5
    ) -> None:
        a = math.radians(angle_deg)
        tip = QPointF(x, y)
        back = QPointF(x - math.cos(a) * size * 1.6, y - math.sin(a) * size * 1.6)
        perp = a + math.pi / 2.0
        p1 = QPointF(back.x() + math.cos(perp) * size * 0.6, back.y() + math.sin(perp) * size * 0.6)
        p2 = QPointF(back.x() - math.cos(perp) * size * 0.6, back.y() - math.sin(perp) * size * 0.6)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([tip, p1, p2]))

    def _paint_radial_menu(self, painter: QPainter) -> None:
        tools = self._host._radial_tools
        n = len(tools)
        if n == 0:
            return
        slice_deg = 360.0 / n
        painter.save()
        cx = float(self._host._radial_center_c.x())
        cy = float(self._host._radial_center_c.y())
        outer, inner = self._radial_geometry(n)
        hover = self._host._radial_hover_index
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Soft drop shadow behind the disc.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 90))
        painter.drawEllipse(QRectF(cx - outer + 2.0, cy - outer + 4.0, outer * 2, outer * 2))

        # Base disc.
        painter.setBrush(QColor(19, 23, 33, 235))
        painter.setPen(QPen(QColor("#2f81f7"), 1.4))
        painter.drawEllipse(QRectF(cx - outer, cy - outer, outer * 2, outer * 2))

        if hover is not None:
            # Highlight the wedge under the cursor — a filled pie slice
            # from center to the rim; the hub fill drawn right after
            # punches the middle back out, leaving a ring highlight
            # matching the actual clickable annulus (_radial_index_at).
            rect = QRectF(cx - outer, cy - outer, outer * 2, outer * 2)
            start_deg = hover * slice_deg - slice_deg / 2.0
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(47, 129, 247, 110))
            painter.drawPie(rect, int(round(start_deg * 16)), int(round(slice_deg * 16)))

        # Thin spokes marking the wedge boundaries.
        painter.setPen(QPen(QColor(255, 255, 255, 28), 1.0))
        for i in range(n):
            ang = math.radians(i * slice_deg + slice_deg / 2.0)
            painter.drawLine(
                QPointF(cx + math.cos(ang) * inner, cy - math.sin(ang) * inner),
                QPointF(cx + math.cos(ang) * outer, cy - math.sin(ang) * outer),
            )

        # Center hub.
        painter.setBrush(QColor(12, 16, 24, 245))
        painter.setPen(QPen(QColor("#30363d"), 1.2))
        painter.drawEllipse(QRectF(cx - inner, cy - inner, inner * 2, inner * 2))
        painter.setPen(QColor("#8b949e"))
        painter.drawText(
            QRectF(cx - inner, cy - inner, inner * 2, inner * 2),
            Qt.AlignmentFlag.AlignCenter,
            "Q",
        )

        font = painter.font()
        font.setPointSizeF(max(8.0, font.pointSizeF()))
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        label_pad = 6.0
        for i, tool in enumerate(tools):
            label = RADIAL_MENU_SHORT_LABELS.get(tool) or canvas_commands.get(tool).label
            ang = math.radians(i * slice_deg)
            active = i == hover
            color = QColor("#ffffff") if active else QColor("#c9d1d9")
            icon_r = outer * 0.53
            label_r = outer * 0.77
            ix = cx + math.cos(ang) * icon_r
            iy = cy - math.sin(ang) * icon_r
            ty = cy - math.sin(ang) * label_r
            self._draw_radial_icon(painter, tool, ix, iy, 15.0, color, label=label)
            painter.setPen(color)

            # Hard cap on label width: the horizontal chord of the disc at
            # this label's height, so a long label can never spill past the
            # circle's edge regardless of angle or word length. Computed
            # *before* eliding (not as a post-hoc position clamp) so a too-
            # long label gets shorter rather than sliding back to overlap
            # its own icon.
            chord_half = self._radial_chord_half(ty, cy, outer)
            text_y = ty + fm.ascent() / 2.0
            cos_a = math.cos(ang)
            # Only truly-horizontal wedges need the icon-dodging side anchor
            # below — anywhere else, icon and label already sit at different
            # enough heights (icon_r vs label_r along the same spoke) that
            # centering doesn't collide, and gets a much bigger width budget.
            if cos_a > 0.97:
                # Due east: label reads outward from the icon, not centered
                # over it — a wide word like "Rectangle" would otherwise
                # overlap the icon since both sit on the same horizontal line.
                text_x = ix + 16.0
                max_w = max(20.0, (cx + chord_half - label_pad) - text_x)
                elided = fm.elidedText(label, Qt.TextElideMode.ElideRight, int(max_w))
            elif cos_a < -0.97:
                # Due west: right-anchor so the label ends just before the icon.
                max_w = max(20.0, (ix - 16.0) - (cx - chord_half + label_pad))
                elided = fm.elidedText(label, Qt.TextElideMode.ElideRight, int(max_w))
                text_x = ix - 16.0 - fm.horizontalAdvance(elided)
            else:
                max_w = max(20.0, chord_half * 2.0 - label_pad * 2.0)
                elided = fm.elidedText(label, Qt.TextElideMode.ElideRight, int(max_w))
                text_x = cx + cos_a * label_r - fm.horizontalAdvance(elided) / 2.0
            painter.drawText(QPointF(text_x, text_y), elided)
        painter.restore()
