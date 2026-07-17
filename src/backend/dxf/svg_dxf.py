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

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import ezdxf  # type: ignore[attr-defined]
from ezdxf import units  # type: ignore[attr-defined]

from src.backend.dxf.io import (
    load_dxf_polylines_with_report,
    summarize_dxf_import_report,
    write_polylines_dxf,
)

MAX_SVG_FILE_BYTES = 32 * 1024 * 1024
MAX_SVG_ELEMENTS = 250_000

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
    document = ezdxf.readfile(str(input_path))
    unit_code = int(document.header.get("$INSUNITS", 0) or 0)
    try:
        mm_factor = float(units.conversion_factor(unit_code, 4)) if unit_code else 1.0
    except (ValueError, TypeError):
        mm_factor = 1.0
    if mm_factor != 1.0:
        polys = [[(x * mm_factor, y * mm_factor) for x, y in poly] for poly in polys]
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
_PATH_COMMAND_RE = re.compile(r"[A-DF-Za-df-z]")  # E/e may be a numeric exponent
_SUPPORTED_PATH_COMMANDS = frozenset("MmLlHhVvZz")
Matrix = tuple[float, float, float, float, float, float]
_IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _matrix_multiply(left: Matrix, right: Matrix) -> Matrix:
    a, b, c, d, e, f = left
    g, h, i, j, k, offset_y = right
    return (
        a * g + c * h, b * g + d * h,
        a * i + c * j, b * i + d * j,
        a * k + c * offset_y + e, b * k + d * offset_y + f,
    )


def _apply_matrix(matrix: Matrix, point: tuple[float, float]) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    x, y = point
    return a * x + c * y + e, b * x + d * y + f


def _parse_transform(value: str) -> Matrix:
    result = _IDENTITY
    operations = re.findall(r"([A-Za-z]+)\s*\(([^)]*)\)", value)
    if value.strip() and not operations:
        raise ValueError(f"Malformed SVG transform: {value}")
    for name, payload in operations:
        args = [float(number) for number in re.findall(_NUM_RE, payload)]
        name = name.lower()
        if name == "matrix" and len(args) == 6:
            current = tuple(args)  # type: ignore[assignment]
        elif name == "translate" and args:
            current = (1, 0, 0, 1, args[0], args[1] if len(args) > 1 else 0)
        elif name == "scale" and args:
            current = (args[0], 0, 0, args[1] if len(args) > 1 else args[0], 0, 0)
        elif name == "rotate" and args:
            angle = math.radians(args[0])
            rotation: Matrix = (math.cos(angle), math.sin(angle), -math.sin(angle), math.cos(angle), 0, 0)
            if len(args) >= 3:
                cx, cy = args[1], args[2]
                current = _matrix_multiply(
                    _matrix_multiply((1, 0, 0, 1, cx, cy), rotation),
                    (1, 0, 0, 1, -cx, -cy),
                )
            else:
                current = rotation
        elif name == "skewx" and args:
            current = (1, 0, math.tan(math.radians(args[0])), 1, 0, 0)
        elif name == "skewy" and args:
            current = (1, math.tan(math.radians(args[0])), 0, 1, 0, 0)
        else:
            raise ValueError(f"Unsupported or malformed SVG transform: {name}({payload})")
        result = _matrix_multiply(result, current)
    return result


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
            if i >= len(tokens):
                raise ValueError("Malformed SVG path: H command requires a coordinate")
            x = float(tokens[i])
            i += 1
            if cmd == "h":
                x += cx
            cx = x
            current.append((cx, cy))
            continue

        if cmd in ("V", "v"):
            if i >= len(tokens):
                raise ValueError("Malformed SVG path: V command requires a coordinate")
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


def _length_mm(value: str, *, default_px: float) -> float:
    match = re.fullmatch(rf"\s*({_NUM_RE})\s*(px|mm|cm|in|pt|pc)?\s*", value)
    if not match:
        return default_px * 25.4 / 96.0
    number = float(match.group(1))
    factor = {None: 25.4 / 96.0, "px": 25.4 / 96.0, "mm": 1.0, "cm": 10.0,
              "in": 25.4, "pt": 25.4 / 72.0, "pc": 25.4 / 6.0}[match.group(2)]
    return number * factor


def _root_svg_matrix(root: ET.Element) -> tuple[Matrix, float]:
    values = [float(value) for value in re.findall(_NUM_RE, root.attrib.get("viewBox", ""))]
    if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
        scale = 25.4 / 96.0
        height = _length_mm(root.attrib.get("height", ""), default_px=_svg_height(root))
        return (scale, 0, 0, scale, 0, 0), height
    x0, y0, vb_width, vb_height = values
    width_mm = _length_mm(root.attrib.get("width", ""), default_px=vb_width)
    height_mm = _length_mm(root.attrib.get("height", ""), default_px=vb_height)
    sx, sy = width_mm / vb_width, height_mm / vb_height
    aspect = root.attrib.get("preserveAspectRatio", "xMidYMid meet").strip()
    if aspect != "none":
        scale = max(sx, sy) if "slice" in aspect else min(sx, sy)
        extra_x, extra_y = width_mm - vb_width * scale, height_mm - vb_height * scale
        align = aspect.split()[0]
        ax = 0.0 if "xMin" in align else extra_x if "xMax" in align else extra_x / 2.0
        ay = 0.0 if "YMin" in align else extra_y if "YMax" in align else extra_y / 2.0
        return (scale, 0, 0, scale, ax - x0 * scale, ay - y0 * scale), height_mm
    return (sx, 0, 0, sy, -x0 * sx, -y0 * sy), height_mm


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

    source = Path(input_path)
    size = source.stat().st_size
    if size > MAX_SVG_FILE_BYTES:
        raise ValueError(
            f"{source.name} is too large to import safely "
            f"({size / (1024 * 1024):.1f} MB; limit 32 MB)."
        )
    tree = ET.parse(str(source))
    root = tree.getroot()
    element_count = sum(1 for _ in root.iter())
    if element_count > MAX_SVG_ELEMENTS:
        raise ValueError(
            f"{source.name} contains {element_count:,} SVG elements; "
            "the safe import limit is 250,000."
        )
    root_matrix, y_flip = _root_svg_matrix(root)
    viewbox = [float(value) for value in re.findall(_NUM_RE, root.attrib.get("viewBox", ""))]
    if len(viewbox) == 4:
        viewport_width_px = _length_mm(root.attrib.get("width", ""), default_px=viewbox[2]) * 96 / 25.4
        viewport_height_px = _length_mm(root.attrib.get("height", ""), default_px=viewbox[3]) * 96 / 25.4
        user_per_px_x = viewbox[2] / viewport_width_px
        user_per_px_y = viewbox[3] / viewport_height_px
    else:
        user_per_px_x = user_per_px_y = 1.0

    def coord(value: str | None, axis: str = "x") -> float:
        if value is None or not value.strip():
            return 0.0
        match = re.fullmatch(rf"\s*({_NUM_RE})\s*(px|mm|cm|in|pt|pc)?\s*", value)
        if not match:
            raise ValueError(f"Unsupported SVG coordinate: {value}")
        number = float(match.group(1))
        unit = match.group(2)
        if unit is None:
            return number
        mm = _length_mm(f"{number}{unit}", default_px=number)
        return mm * 96 / 25.4 * (user_per_px_y if axis == "y" else user_per_px_x)

    def yf(y: float) -> float:
        if not flip_y:
            return y
        return y_flip - y if y_flip else -y

    records: list[tuple[list[tuple[float, float]], str, dict | None]] = []
    native_entities = {"LINE": 0, "CIRCLE": 0, "ELLIPSE": 0}
    unsupported_paths = 0
    unsupported_features: set[str] = set()

    def _add_meta_entity(kind: str, meta: dict, counter: str) -> None:
        shape = shape_from_meta(kind, meta)
        if shape is None:
            return
        pts = [(x, y) for x, y in shape.points]
        if len(pts) >= 2:
            records.append((pts, kind, meta))
            native_entities[counter] += 1

    transforms: dict[int, Matrix] = {}

    def collect_transforms(elem: ET.Element, parent: Matrix = root_matrix) -> None:
        transform_value = elem.attrib.get("transform", "")
        if not transform_value:
            style_match = re.search(r"(?:^|;)\s*transform\s*:\s*([^;]+)", elem.attrib.get("style", ""))
            transform_value = style_match.group(1).strip() if style_match else ""
        local = _parse_transform(transform_value)
        combined = _matrix_multiply(parent, local)
        transforms[id(elem)] = combined
        for child in elem:
            collect_transforms(child, combined)

    collect_transforms(root)

    non_rendered: set[int] = set()

    def mark_non_rendered(elem: ET.Element, hidden: bool = False) -> None:
        tag = elem.tag.split("}")[-1].lower()
        style = elem.attrib.get("style", "").replace(" ", "").lower()
        hidden = hidden or tag in {"defs", "clippath", "mask", "symbol", "marker"}
        hidden = hidden or elem.attrib.get("display", "").lower() == "none"
        hidden = hidden or "display:none" in style or elem.attrib.get("visibility", "").lower() == "hidden"
        if hidden:
            non_rendered.add(id(elem))
        for child in elem:
            mark_non_rendered(child, hidden)

    mark_non_rendered(root)

    for elem in root.iter():
        tag = elem.tag.split("}")[-1].lower()
        if id(elem) in non_rendered:
            continue
        for feature in ("clip-path", "mask", "filter"):
            if feature in elem.attrib:
                unsupported_features.add(feature)
        if tag in {"text", "use", "image", "foreignobject"}:
            unsupported_features.add(tag)
            continue
        matrix = transforms[id(elem)]

        def tp(point: tuple[float, float], transform: Matrix = matrix) -> tuple[float, float]:
            x, y = _apply_matrix(transform, point)
            return x, yf(y)

        if tag == "polyline":
            pts = _parse_points_attr(elem.attrib.get("points", ""))
            if len(pts) >= 2:
                records.append(([tp(point) for point in pts], "polyline", None))

        elif tag == "polygon":
            pts = _parse_points_attr(elem.attrib.get("points", ""))
            if len(pts) >= 3:
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                records.append(([tp(point) for point in pts], "polyline", None))

        elif tag == "line":
            x1 = coord(elem.attrib.get("x1"), "x")
            y1 = coord(elem.attrib.get("y1"), "y")
            x2 = coord(elem.attrib.get("x2"), "x")
            y2 = coord(elem.attrib.get("y2"), "y")
            start, end = tp((x1, y1)), tp((x2, y2))
            _add_meta_entity("line", {"start": start, "end": end}, "LINE")

        elif tag == "rect":
            x = coord(elem.attrib.get("x"), "x")
            y = coord(elem.attrib.get("y"), "y")
            w = coord(elem.attrib.get("width"), "x")
            h = coord(elem.attrib.get("height"), "y")
            if w > 0 and h > 0:
                pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
                rx = max(0.0, min(coord(elem.attrib.get("rx"), "x"), w / 2))
                ry_raw = coord(elem.attrib.get("ry"), "y")
                ry = max(0.0, min(ry_raw if ry_raw else rx, h / 2))
                if rx and ry:
                    pts = []
                    for cx0, cy0, start in (
                        (x + w - rx, y + ry, -90), (x + w - rx, y + h - ry, 0),
                        (x + rx, y + h - ry, 90), (x + rx, y + ry, 180),
                    ):
                        for step in range(5):
                            angle = math.radians(start + step * 22.5)
                            pts.append((cx0 + rx * math.cos(angle), cy0 + ry * math.sin(angle)))
                    pts.append(pts[0])
                records.append(([tp(point) for point in pts], "polyline", None))

        elif tag == "circle":
            cx = coord(elem.attrib.get("cx"), "x")
            cy = coord(elem.attrib.get("cy"), "y")
            r = coord(elem.attrib.get("r"), "x")
            if r > 0:
                center = tp((cx, cy))
                edge_x = tp((cx + r, cy))
                edge_y = tp((cx, cy + r))
                rx2, ry2 = math.dist(center, edge_x), math.dist(center, edge_y)
                if abs(rx2 - ry2) <= 1e-9 * max(rx2, ry2, 1.0):
                    _add_meta_entity("circle", {"center": center, "radius": rx2}, "CIRCLE")
                else:
                    pts = [tp((cx + r * math.cos(a), cy + r * math.sin(a))) for a in [i * math.tau / 64 for i in range(65)]]
                    records.append((pts, "polyline", None))

        elif tag == "ellipse":
            cx = coord(elem.attrib.get("cx"), "x")
            cy = coord(elem.attrib.get("cy"), "y")
            rx = coord(elem.attrib.get("rx"), "x")
            ry = coord(elem.attrib.get("ry"), "y")
            if rx > 0 and ry > 0:
                pts = [tp((cx + rx * math.cos(a), cy + ry * math.sin(a))) for a in [i * math.tau / 64 for i in range(65)]]
                records.append((pts, "polyline", None))

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
                    records.append(([tp(point) for point in p], "polyline", None))

    write_polylines_dxf(
        [poly for poly, _k, _m in records],
        str(output_path),
        entity_kinds=[k for _p, k, _m in records],
        entity_meta=[m for _p, _k, m in records],
    )

    plain = [poly for poly, kind, _m in records if kind == "polyline"]
    all_pts = [pt for poly, _kind, _meta in records for pt in poly]
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
        "unsupported_features": tuple(sorted(unsupported_features)),
    }
