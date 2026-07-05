"""Convert text to polyline outlines via the system font engine.

Glyphs are rendered through ``QPainterPath.addText`` and flattened to
closed polyline contours, so canvas text is ordinary geometry: it exports
to DXF, participates in patterns, and edits like anything else. Letter
counters (o, a, p, …) arrive as separate closed contours, exactly like
the image tracer produces them.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics, QPainterPath

from src.paths import user_data_dir

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
