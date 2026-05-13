"""Phase 4: Shape-aware rendering pipeline with tessellation caching."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainter, QPainterPath

if TYPE_CHECKING:
    from src.backend.shapes import Shape


class ShapeRenderer:
    """Renders shapes to Qt painter with proper caching."""

    # Tessellation quality settings
    ARC_SEGMENTS = 24
    CIRCLE_SEGMENTS = 64
    ELLIPSE_SEGMENTS = 64
    SPLINE_SEGMENTS = 24

    @staticmethod
    def shape_to_qpainter_path(shape: Shape) -> QPainterPath:
        """Convert shape to Qt painter path for rendering."""
        path = QPainterPath()

        if len(shape.points) == 0:
            return path

        # Move to first point
        pt = shape.points[0]
        path.moveTo(QPointF(pt[0], pt[1]))

        # Line to remaining points
        for pt in shape.points[1:]:
            path.lineTo(QPointF(pt[0], pt[1]))

        # Close if shape is closed (polyline, arc, circle, etc.)
        if (
            hasattr(shape, "closed")
            and shape.closed
            or shape.shape_type in ("arc", "circle", "ellipse", "rectangle")
        ):
            path.closeSubpath()

        return path

    @staticmethod
    def render_shape(
        painter: QPainter,
        shape: Shape,
        color: tuple[int, int, int] = (0, 0, 0),
        width: float = 1.0,
        fill: bool = False,
        fill_color: tuple[int, int, int] = (200, 200, 200),
    ) -> None:
        """Render a single shape to the painter.

        Uses cached tessellation from shape.points property.
        """
        from PySide6.QtGui import QBrush, QColor, QPen

        # Get painter path from shape (uses cached tessellation)
        path = ShapeRenderer.shape_to_qpainter_path(shape)

        # Set up pen and brush
        pen = QPen(QColor(*color))
        pen.setWidthF(width)
        painter.setPen(pen)

        if fill:
            brush = QBrush(QColor(*fill_color))
            painter.setBrush(brush)
        else:
            painter.setBrush(QBrush())

        # Draw the path
        painter.drawPath(path)

    @staticmethod
    def render_shape_with_metadata(
        painter: QPainter,
        shape: Shape,
        construction: bool = False,
        hidden: bool = False,
        locked: bool = False,
        selected: bool = False,
        accent_color: str | None = None,
    ) -> None:
        """Render shape with styling based on metadata."""
        if hidden:
            return  # Don't render hidden shapes

        from PySide6.QtGui import QColor

        # Determine base color
        if accent_color:
            # Parse hex color
            try:
                color = QColor(accent_color)
                rgb = (color.red(), color.green(), color.blue())
            except Exception:
                rgb = (128, 128, 128) if construction else (0, 0, 0)
        elif construction:
            rgb = (128, 128, 128)  # Gray for construction
        elif selected:
            rgb = (0, 100, 200)  # Blue for selected
        else:
            rgb = (0, 0, 0)  # Black for normal

        # Determine line style
        width = 2.0 if selected else 1.5 if locked else 1.0
        alpha = 0.5 if construction else 1.0

        # Render the shape
        pen = painter.pen()
        pen.setColor(QColor(*rgb))
        pen.setWidthF(width)
        painter.setPen(pen)

        path = ShapeRenderer.shape_to_qpainter_path(shape)
        painter.drawPath(path)

    @staticmethod
    def render_control_points(
        painter: QPainter,
        points: list[tuple[float, float]],
        point_size: float = 4.0,
        color: tuple[int, int, int] = (50, 150, 255),
    ) -> None:
        """Render control points for shape editing."""
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QPen

        pen = QPen(QColor(*color))
        pen.setWidthF(1.0)
        painter.setPen(pen)

        for x, y in points:
            rect = QRectF(
                x - point_size / 2, y - point_size / 2, point_size, point_size
            )
            painter.drawEllipse(rect)

    @staticmethod
    def render_snap_indicator(
        painter: QPainter,
        x: float,
        y: float,
        snap_type: str = "point",
        color: tuple[int, int, int] = (0, 255, 0),
        size: float = 8.0,
    ) -> None:
        """Render visual indicator for snap point."""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QColor, QPen

        pen = QPen(QColor(*color))
        pen.setWidthF(2.0)
        painter.setPen(pen)

        if snap_type == "point":
            # Small circle
            painter.drawEllipse(QPointF(x, y), size / 2, size / 2)
        elif snap_type == "axis":
            # Crosshair
            painter.drawLine(QPointF(x - size, y), QPointF(x + size, y))
            painter.drawLine(QPointF(x, y - size), QPointF(x, y + size))
        elif snap_type == "intersection":
            # Larger circle
            painter.drawEllipse(QPointF(x, y), size, size)
