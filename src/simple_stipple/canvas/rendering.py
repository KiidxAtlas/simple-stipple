"""Retained-path and overscan-raster rendering for dense editor documents."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QTransform

from simple_stipple.canvas.constants import GUIDE_COLOR, POLY, SEL
from simple_stipple.core.cad.editor_geometry import polyline_is_closed


class DensePreviewRenderer:
    """Cache exact world-space paths and an overscanned viewport raster.

    The cache belongs to the renderer rather than the view: it responds only
    to already-normalized editor state and never routes events or mutates the
    document.
    """

    MIN_ENTITIES = 2_000
    RASTER_MARGIN = 384

    def __init__(self, host: Any) -> None:
        self._host = host
        self.batches: dict[tuple[str, float, int], QPainterPath] | None = None
        self.raster: QPixmap | None = None
        self.raster_origin: tuple[float, float] | None = None
        self.raster_size: tuple[int, int, float] | None = None
        self.raster_scale: float | None = None

    def invalidate(self) -> None:
        self.batches = None
        self.raster = None
        self.raster_origin = None
        self.raster_size = None
        self.raster_scale = None

    def build_batches(self) -> dict[tuple[str, float, int], QPainterPath]:
        """Build exact dense-preview paths once in world coordinates."""
        batches: dict[tuple[str, float, int], QPainterPath] = {}
        for entity in self._host._entities:
            poly = entity.points
            if entity.hidden or len(poly) < 2:
                continue
            if not self._host._on_active_layer(entity) and entity.id not in self._host._sel:
                layer_hex = (
                    self._host._layer_colors.get(entity.layer) if entity.layer is not None else None
                )
                color = QColor(layer_hex) if layer_hex else QColor(POLY)
                color.setAlpha(140)
                width = 1.2
                style = Qt.PenStyle.DashLine.value
            else:
                selected = entity.id in self._host._sel
                if selected:
                    color = QColor(SEL)
                elif entity.id in self._host._accent_polys:
                    color = QColor(self._host._accent_polys[entity.id])
                elif entity.construction:
                    color = QColor(GUIDE_COLOR)
                elif layer_hex := (
                    self._host._layer_colors.get(entity.layer) if entity.layer is not None else None
                ):
                    color = QColor(layer_hex)
                else:
                    color = QColor(POLY)
                if entity.locked:
                    color = QColor("#8b949e")
                width = 2.0 if selected else (1.2 if entity.construction else 1.5)
                style = (
                    Qt.PenStyle.DashLine.value
                    if entity.construction or entity.locked
                    else Qt.PenStyle.SolidLine.value
                )
            key = (color.name(QColor.NameFormat.HexArgb), width, style)
            path = batches.setdefault(key, QPainterPath())
            render_poly = self._host._flattened_points_by_id(entity.id)
            if len(render_poly) < 2:
                continue
            path.moveTo(*render_poly[0])
            for point in render_poly[1:]:
                path.lineTo(*point)
            if len(poly) >= 3 and polyline_is_closed(poly):
                path.closeSubpath()
        return batches

    def paint_paths(self, painter: QPainter, origin_x: float, origin_y: float) -> None:
        """Paint retained paths with a supplied canvas origin."""
        if self.batches is None:
            self.batches = self.build_batches()
        painter.setWorldTransform(
            QTransform(self._host._scale, 0.0, 0.0, -self._host._scale, origin_x, origin_y)
        )
        for (color_name, width, style), path in self.batches.items():
            pen = QPen(QColor(color_name), width)
            pen.setStyle(Qt.PenStyle(style))
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

    def can_reuse_raster(self, width: int, height: int, dpr: float) -> bool:
        """Whether the current view stays inside the cached overscan area."""
        if (
            self.raster is None
            or self.raster_origin is None
            or self.raster_size != (width, height, dpr)
            or self.raster_scale != self._host._scale
        ):
            return False
        origin_x, origin_y = self.raster_origin
        return (
            abs(self._host._ox - origin_x) <= self.RASTER_MARGIN
            and abs(self._host._oy - origin_y) <= self.RASTER_MARGIN
        )

    def rebuild_raster(self, width: int, height: int, dpr: float) -> None:
        """Rasterize retained paths slightly beyond the viewport."""
        margin = self.RASTER_MARGIN
        image = QImage(
            round((width + margin * 2) * dpr),
            round((height + margin * 2) * dpr),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        cache_painter = QPainter(image)
        cache_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_paths(cache_painter, self._host._ox + margin, self._host._oy + margin)
        cache_painter.end()
        self.raster = QPixmap.fromImage(image)
        self.raster_origin = (self._host._ox, self._host._oy)
        self.raster_size = (width, height, dpr)
        self.raster_scale = self._host._scale

    def paint(self, painter: QPainter) -> None:
        """Render cached paths, using a raster for very large documents."""
        if self.batches is None:
            self.batches = self.build_batches()
        width = max(1, self._host.width())
        height = max(1, self._host.height())
        dpr = painter.device().devicePixelRatioF()
        if len(self._host._entities) >= self.MIN_ENTITIES:
            if not self.can_reuse_raster(width, height, dpr):
                self.rebuild_raster(width, height, dpr)
            if self.raster is not None and self.raster_origin is not None:
                cached_x, cached_y = self.raster_origin
                painter.drawPixmap(
                    QPointF(
                        self._host._ox - cached_x - self.RASTER_MARGIN,
                        self._host._oy - cached_y - self.RASTER_MARGIN,
                    ),
                    self.raster,
                )
                return
        painter.save()
        self.paint_paths(painter, self._host._ox, self._host._oy)
        painter.restore()


__all__ = ["DensePreviewRenderer"]
