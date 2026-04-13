"""DXF → SVG converter.

Converts all LWPOLYLINE entities in a DXF file to a single SVG file.
Coordinates are preserved in mm; the SVG viewBox matches the bounding box
of the polylines.

Usage::

    from src.core.dxf_svg import dxf_to_svg
    dxf_to_svg("input.dxf", "output.svg")
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import ezdxf  # type: ignore[attr-defined]


def _load_polylines(
    path: str | Path,
) -> list[list[tuple[float, float]]]:
    """Read LWPOLYLINE entities from a DXF file."""
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    result: list[list[tuple[float, float]]] = []
    for ent in msp:
        if ent.dxftype() == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in ent.get_points()]
            closed = bool(ent.closed)
            if closed and len(pts) >= 2 and pts[0] != pts[-1]:
                pts = pts + [pts[0]]
            if len(pts) >= 2:
                result.append(pts)
    return result


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
    if len(pts) >= 3 and pts[0] == pts[-1]:
        parts.append("Z")
    return " ".join(parts)


def dxf_to_svg(
    input_path: str | Path,
    output_path: str | Path,
    stroke: str = "#000000",
    stroke_width: float = 0.5,
    padding: float = 2.0,
) -> dict:
    """
    Convert DXF polylines to SVG.

    Parameters
    ----------
    input_path:   Path to source .dxf file.
    output_path:  Path to write .svg file.
    stroke:       CSS colour for all paths (default black).
    stroke_width: Path stroke width in mm (default 0.5 mm).
    padding:      Extra whitespace around the drawing in mm (default 2 mm).

    Returns
    -------
    dict with keys ``polylines``, ``width_mm``, ``height_mm``.
    """
    polys = _load_polylines(input_path)
    if not polys:
        # Write empty SVG
        root = ET.Element(
            "svg",
            xmlns="http://www.w3.org/2000/svg",
            viewBox="0 0 10 10",
            width="10mm",
            height="10mm",
        )
        ET.ElementTree(root).write(
            str(output_path), xml_declaration=True, encoding="utf-8"
        )
        return {"polylines": 0, "width_mm": 0.0, "height_mm": 0.0}

    x0, y0, x1, y1 = _bbox(polys)
    vw = (x1 - x0) + padding * 2
    vh = (y1 - y0) + padding * 2

    # SVG Y-axis points downward; DXF/mm Y axis points upward.
    # We flip by writing  svg_y = (y1 + padding) - dxf_y  which equals (vbox_h - (dxf_y - y0 + padding)).
    y_total = y1 + padding  # constant for y-flip formula

    root = ET.Element(
        "svg",
        xmlns="http://www.w3.org/2000/svg",
        viewBox=f"{x0 - padding:.4f} 0 {vw:.4f} {vh:.4f}",
        width=f"{vw:.4f}mm",
        height=f"{vh:.4f}mm",
    )
    g = ET.SubElement(
        root, "g", fill="none", stroke=stroke, **{"stroke-width": f"{stroke_width:.4f}"}
    )

    for pts in polys:
        d = _poly_to_svg_d(pts, y_total)
        ET.SubElement(g, "path", d=d)

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(str(output_path), xml_declaration=True, encoding="utf-8")

    return {
        "polylines": len(polys),
        "width_mm": round(vw, 4),
        "height_mm": round(vh, 4),
    }
