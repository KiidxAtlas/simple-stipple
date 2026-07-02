"""Convert text to polyline outlines via the system font engine.

Glyphs are rendered through ``QPainterPath.addText`` and flattened to
closed polyline contours, so canvas text is ordinary geometry: it exports
to DXF, participates in patterns, and edits like anything else. Letter
counters (o, a, p, …) arrive as separate closed contours, exactly like
the image tracer produces them.
"""

from __future__ import annotations

from PySide6.QtGui import QFont, QPainterPath

Polyline = list[tuple[float, float]]

# Render glyphs at a large pixel size, then scale to mm — keeps curve
# flattening smooth regardless of the requested text height.
_RENDER_PX = 256


def text_to_polylines(
    text: str,
    *,
    family: str,
    height_mm: float,
    bold: bool = False,
    italic: bool = False,
) -> list[Polyline]:
    """Return closed polyline contours for ``text``.

    ``height_mm`` is the total height of the rendered text block (cap
    height plus descenders for mixed-case input). Coordinates are y-up
    with the block's bottom-left at the origin.
    """
    text = str(text)
    if not text.strip() or height_mm <= 0:
        return []

    font = QFont(family)
    font.setPixelSize(_RENDER_PX)
    font.setBold(bool(bold))
    font.setItalic(bool(italic))

    path = QPainterPath()
    path.addText(0.0, 0.0, font, text)
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
            for p in sub
        ]
        if len(pts) < 3:
            continue
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        polys.append(pts)
    return polys
