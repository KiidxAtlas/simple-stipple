"""DXF → SVG converter.

Converts all LWPOLYLINE entities in a DXF file to a single SVG file.
Coordinates are preserved in mm; the SVG viewBox matches the bounding box
of the polylines.

Usage::

    from src.backend.dxf.svg import dxf_to_svg
    dxf_to_svg("input.dxf", "output.svg")
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from src.backend.dxf.io import (
    load_dxf_polylines_with_report,
    summarize_dxf_import_report,
)


def _bbox(
    polys: list[list[tuple[float, float]]],
) -> tuple[float, float, float, float]:
    all_pts = [pt for p in polys for pt in p]
    if not all_pts:
        return 0.0, 0.0, 1.0, 1.0
    xs, ys = zip(*all_pts)
    return min(xs), min(ys), max(xs), max(ys)


def _poly_to_svg_d(
    pts: list[tuple[float, float]],
    y_flip: float,
) -> str:
    """Convert polyline points to SVG path 'd' attribute (Y-flipped)."""
    parts: list[str] = []
    for i, (x, y) in enumerate(pts):
        cmd = "M" if i == 0 else "L"
        parts.append(f"{cmd}{x:.4f},{y_flip - y:.4f}")
    if len(pts) >= 3:
        # Use epsilon-based closure detection so float drift through DXF
        # round-trips still emits a proper Z command.
        fx, fy = pts[0]
        lx, ly = pts[-1]
        if abs(fx - lx) <= 1e-4 and abs(fy - ly) <= 1e-4:
            parts.append("Z")
    return " ".join(parts)


def write_polylines_svg(
    polys: list[list[tuple[float, float]]],
    output_path: str | Path,
    *,
    stroke: str = "#000000",
    stroke_width: float = 0.5,
    padding: float = 2.0,
) -> dict:
    """Write polylines to a single-group SVG (one consolidated layer).

    Coordinates are mm, y-up; the viewBox hugs the drawing plus padding.
    Returns ``{"polylines", "width_mm", "height_mm"}``.
    """
    from ..io.persistence import atomic_write_via

    if not polys:
        root = ET.Element(
            "svg",
            xmlns="http://www.w3.org/2000/svg",
            viewBox="0 0 10 10",
            width="10mm",
            height="10mm",
        )
        atomic_write_via(
            output_path,
            lambda p: ET.ElementTree(root).write(
                str(p), xml_declaration=True, encoding="utf-8"
            ),
        )
        return {"polylines": 0, "width_mm": 0.0, "height_mm": 0.0}

    x0, y0, x1, y1 = _bbox(polys)
    vw = (x1 - x0) + padding * 2
    vh = (y1 - y0) + padding * 2

    # SVG Y-axis points downward; DXF/mm Y axis points upward.
    y_total = y1 + padding

    root = ET.Element(
        "svg",
        xmlns="http://www.w3.org/2000/svg",
        viewBox=f"{x0 - padding:.4f} 0 {vw:.4f} {vh:.4f}",
        width=f"{vw:.4f}mm",
        height=f"{vh:.4f}mm",
    )
    g = ET.SubElement(
        root,
        "g",
        {
            "fill": "none",
            "stroke": stroke,
            "stroke-width": f"{stroke_width:.4f}",
        },
    )
    for pts in polys:
        ET.SubElement(g, "path", d=_poly_to_svg_d(pts, y_total))

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    atomic_write_via(
        output_path,
        lambda p: tree.write(str(p), xml_declaration=True, encoding="utf-8"),
    )
    return {
        "polylines": len(polys),
        "width_mm": round(vw, 4),
        "height_mm": round(vh, 4),
    }


def dxf_to_svg(
    input_path: str | Path,
    output_path: str | Path,
    stroke: str = "#000000",
    stroke_width: float = 0.5,
    padding: float = 2.0,
) -> dict:
    """Convert DXF polylines to a consolidated single-layer SVG."""
    polys, report = load_dxf_polylines_with_report(str(input_path))
    result = write_polylines_svg(
        polys,
        output_path,
        stroke=stroke,
        stroke_width=stroke_width,
        padding=padding,
    )
    if report.has_issues:
        result["ignored_entities"] = report.ignored_entities
        result["ignored_entity_summary"] = summarize_dxf_import_report(report)
    return result
