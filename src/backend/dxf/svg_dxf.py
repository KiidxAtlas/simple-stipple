"""DXF <-> SVG converters (both directions).

Two previously-separate modules merged here since they're an inverse-
direction pair: ``dxf_to_svg`` / ``write_polylines_svg`` (was ``dxf/svg.py``)
converts DXF polylines to a single-layer SVG, and ``svg_to_dxf`` (this
module's original content) parses common SVG primitives back to DXF
LWPOLYLINE entities. Supported SVG elements for the svg->dxf direction:
polyline, polygon, line, rect, circle, ellipse, path (M/L/H/V/Z commands,
absolute + relative).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from src.backend.dxf.io import (
    load_dxf_polylines_with_report,
    summarize_dxf_import_report,
    write_polylines_dxf,
)

# ══════════════════════════════════════════════════════════════════════════
# DXF -> SVG
# ══════════════════════════════════════════════════════════════════════════


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
    from ..persistence import atomic_write_via

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
            lambda p: ET.ElementTree(root).write(str(p), xml_declaration=True, encoding="utf-8"),
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


# ══════════════════════════════════════════════════════════════════════════
# SVG -> DXF
# ══════════════════════════════════════════════════════════════════════════

_NUM_RE = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"
_CMD_RE = re.compile(rf"[MmLlHhVvZz]|{_NUM_RE}")
_PATH_COMMAND_RE = re.compile(r"[A-Za-z]")
_SUPPORTED_PATH_COMMANDS = frozenset("MmLlHhVvZz")


def _parse_float(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _parse_points_attr(points: str) -> list[tuple[float, float]]:
    nums = re.findall(_NUM_RE, points)
    values = [float(n) for n in nums]
    pts: list[tuple[float, float]] = []
    for i in range(0, len(values) - 1, 2):
        pts.append((values[i], values[i + 1]))
    return pts


def _parse_path_d(d: str) -> list[list[tuple[float, float]]]:
    tokens = _CMD_RE.findall(d)
    i = 0
    cmd = ""
    cx = cy = 0.0
    sx = sy = 0.0
    current: list[tuple[float, float]] = []
    out: list[list[tuple[float, float]]] = []

    def push_current() -> None:
        nonlocal current
        if len(current) >= 2:
            out.append(current)
        current = []

    while i < len(tokens):
        tok = tokens[i]
        if re.fullmatch(r"[MmLlHhVvZz]", tok):
            cmd = tok
            i += 1
        if not cmd:
            i += 1
            continue

        if cmd in ("M", "m"):
            if i + 1 >= len(tokens):
                break
            x = float(tokens[i])
            y = float(tokens[i + 1])
            i += 2
            if cmd == "m":
                x += cx
                y += cy
            push_current()
            cx, cy = x, y
            sx, sy = x, y
            current = [(x, y)]
            cmd = "L" if cmd == "M" else "l"
            continue

        if cmd in ("L", "l"):
            if i + 1 >= len(tokens):
                break
            x = float(tokens[i])
            y = float(tokens[i + 1])
            i += 2
            if cmd == "l":
                x += cx
                y += cy
            cx, cy = x, y
            current.append((x, y))
            continue

        if cmd in ("H", "h"):
            x = float(tokens[i])
            i += 1
            if cmd == "h":
                x += cx
            cx = x
            current.append((cx, cy))
            continue

        if cmd in ("V", "v"):
            y = float(tokens[i])
            i += 1
            if cmd == "v":
                y += cy
            cy = y
            current.append((cx, cy))
            continue

        if cmd in ("Z", "z"):
            if current and current[0] != current[-1]:
                current.append(current[0])
            push_current()
            cx, cy = sx, sy
            i += 1
            continue

        i += 1

    if current:
        push_current()
    return out


def _svg_height(root: ET.Element) -> float:
    vb = root.attrib.get("viewBox", "").strip()
    if vb:
        nums = re.findall(_NUM_RE, vb)
        if len(nums) == 4:
            y0 = float(nums[1])
            h = float(nums[3])
            return y0 + h
    h_attr = root.attrib.get("height", "")
    nums = re.findall(_NUM_RE, h_attr)
    if nums:
        return float(nums[0])
    return 0.0


def svg_to_dxf(
    input_path: str | Path,
    output_path: str | Path,
    *,
    flip_y: bool = True,
) -> dict:
    """Convert supported SVG elements to DXF.

    Parsing produces (polyline, kind, meta) records which are written by
    :func:`src.backend.dxf.io.write_polylines_dxf` — the single DXF writer,
    so SVG conversions get the same validation guarantees as every other
    export (audit gate, finite checks, ellipse axis handling). Previously
    this function assembled its own ezdxf document and wrote it even when
    the audit reported errors.
    """
    from src.backend.shapes import shape_from_meta

    tree = ET.parse(str(input_path))
    root = tree.getroot()
    y_flip = _svg_height(root)

    def yf(y: float) -> float:
        if not flip_y:
            return y
        return y_flip - y if y_flip else -y

    records: list[tuple[list[tuple[float, float]], str, dict | None]] = []
    native_entities = {"LINE": 0, "CIRCLE": 0, "ELLIPSE": 0}
    unsupported_paths = 0

    def _add_meta_entity(kind: str, meta: dict, counter: str) -> None:
        shape = shape_from_meta(kind, meta)
        if shape is None:
            return
        pts = [(x, y) for x, y in shape.points]
        if len(pts) >= 2:
            records.append((pts, kind, meta))
            native_entities[counter] += 1

    for elem in root.iter():
        tag = elem.tag.split("}")[-1].lower()

        if tag == "polyline":
            pts = _parse_points_attr(elem.attrib.get("points", ""))
            if len(pts) >= 2:
                records.append(([(x, yf(y)) for x, y in pts], "polyline", None))

        elif tag == "polygon":
            pts = _parse_points_attr(elem.attrib.get("points", ""))
            if len(pts) >= 3:
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                records.append(([(x, yf(y)) for x, y in pts], "polyline", None))

        elif tag == "line":
            x1 = _parse_float(elem.attrib.get("x1"))
            y1 = _parse_float(elem.attrib.get("y1"))
            x2 = _parse_float(elem.attrib.get("x2"))
            y2 = _parse_float(elem.attrib.get("y2"))
            _add_meta_entity(
                "line",
                {"start": (x1, yf(y1)), "end": (x2, yf(y2))},
                "LINE",
            )

        elif tag == "rect":
            x = _parse_float(elem.attrib.get("x"))
            y = _parse_float(elem.attrib.get("y"))
            w = _parse_float(elem.attrib.get("width"))
            h = _parse_float(elem.attrib.get("height"))
            if w > 0 and h > 0:
                pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
                records.append(([(px, yf(py)) for px, py in pts], "polyline", None))

        elif tag == "circle":
            cx = _parse_float(elem.attrib.get("cx"))
            cy = _parse_float(elem.attrib.get("cy"))
            r = _parse_float(elem.attrib.get("r"))
            if r > 0:
                _add_meta_entity("circle", {"center": (cx, yf(cy)), "radius": r}, "CIRCLE")

        elif tag == "ellipse":
            cx = _parse_float(elem.attrib.get("cx"))
            cy = _parse_float(elem.attrib.get("cy"))
            rx = _parse_float(elem.attrib.get("rx"))
            ry = _parse_float(elem.attrib.get("ry"))
            if rx > 0 and ry > 0:
                _add_meta_entity(
                    "ellipse",
                    {"center": (cx, yf(cy)), "rx": rx, "ry": ry, "rotation": 0.0},
                    "ELLIPSE",
                )

        elif tag == "path":
            d = elem.attrib.get("d", "")
            if not d.strip():
                continue
            # Never reinterpret Bézier/arc/control-point coordinates as line
            # vertices. That produced valid-looking but geometrically wrong
            # DXFs. Unsupported paths are skipped and reported explicitly.
            commands = set(_PATH_COMMAND_RE.findall(d))
            if commands - _SUPPORTED_PATH_COMMANDS:
                unsupported_paths += 1
                continue
            for p in _parse_path_d(d):
                if len(p) >= 2:
                    records.append(([(x, yf(y)) for x, y in p], "polyline", None))

    write_polylines_dxf(
        [poly for poly, _k, _m in records],
        str(output_path),
        entity_kinds=[k for _p, k, _m in records],
        entity_meta=[m for _p, _k, m in records],
    )

    plain = [poly for poly, kind, _m in records if kind == "polyline"]
    all_pts = [pt for poly in plain for pt in poly]
    if all_pts:
        xs, ys = zip(*all_pts)
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
    else:
        width = height = 0.0

    return {
        "polylines": len(plain),
        "width_mm": round(width, 4),
        "height_mm": round(height, 4),
        "native_entities": native_entities,
        "unsupported_paths": unsupported_paths,
    }
