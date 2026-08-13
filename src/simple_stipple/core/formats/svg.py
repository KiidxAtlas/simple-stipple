"""DXF <-> SVG converters (both directions).

Two previously-separate modules merged here since they're an inverse-
direction pair: ``dxf_to_svg`` / ``write_polylines_svg`` (was ``dxf/svg.py``)
converts DXF polylines to a single-layer SVG, and ``svg_to_dxf`` (this
module's original content) parses common SVG primitives back to DXF
LWPOLYLINE entities. Supported SVG elements for the svg->dxf direction:
polyline, polygon, line, rect, circle, ellipse, path (full path grammar,
including curves, via svgelements).
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import ezdxf  # type: ignore[attr-defined]
import svgelements as se
from ezdxf import units  # type: ignore[attr-defined]

from simple_stipple.core.cad.shape_factory import shape_from_meta
from simple_stipple.core.formats.dxf import (
    load_dxf_polylines_with_report,
    summarize_dxf_import_report,
)
from simple_stipple.core.formats.dxf_write import write_polylines_dxf

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
    from simple_stipple.platform.storage import atomic_write_via

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
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "xmlns:stipple": STIPPLE_NS,
            "viewBox": f"{x0 - padding:.4f} 0 {vw:.4f} {vh:.4f}",
            "width": f"{vw:.4f}mm",
            "height": f"{vh:.4f}mm",
            # Where this drawing sits, so reopening it does not translate the
            # part onto its own bounding box.
            "stipple:world": f"0 {y_total:.6f}",
        },
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


# ── Origin fidelity ───────────────────────────────────────────────────────
#
# The generic reader normalises any SVG into its own viewBox: a drawing comes
# back translated to its bounding box plus the writer's padding. That is the
# right behaviour for arbitrary artwork, whose viewBox origin is a page, not a
# machine bed — but it means reopening our *own* export moved the part, and
# the drift compounded on every round trip.
#
# Rather than change how every SVG is read, our files say where they are.
# When this marker is present the reader uses it verbatim and skips the
# viewBox normalisation entirely; without it nothing changes.
STIPPLE_NS = "https://simple-stipple.app/svg"
_WORLD_ATTR = f"{{{STIPPLE_NS}}}world"


def _stipple_world(root: ET.Element) -> tuple[float, float] | None:
    """``(x_offset_mm, y_flip_mm)`` when this SVG declares its world origin.

    ``x_world = x_svg + x_offset`` and ``y_world = y_flip - y_svg`` — the exact
    inverse of what :func:`write_document_svg` emits.
    """
    raw = root.attrib.get(_WORLD_ATTR) or root.attrib.get("data-stipple-world")
    if not raw:
        return None
    values = [float(value) for value in re.findall(_NUM_RE, raw)]
    return (values[0], values[1]) if len(values) == 2 else None


def _world_transform(root: ET.Element) -> tuple[Matrix, float]:
    """The matrix and y-flip that take this SVG's units to millimetres.

    A file that declares its own world origin is taken at its word, so a part
    drawn at negative coordinates reopens where it was drawn. Everything else
    falls back to the viewBox normalisation, which is what arbitrary artwork
    needs — its viewBox is a page, not a machine bed.
    """
    declared = _stipple_world(root)
    if declared is None:
        return _root_svg_matrix(root)
    x_offset, y_flip = declared
    return se.Matrix(1.0, 0.0, 0.0, 1.0, x_offset, 0.0), y_flip


@dataclass(frozen=True)
class SvgImagePlacement:
    """A raster to embed at real-world millimetre coordinates."""

    png_bytes: bytes
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    rotation_deg: float = 0.0


def write_document_svg(
    polys: list[list[tuple[float, float]]],
    output_path: str | Path,
    *,
    images: list[SvgImagePlacement] | None = None,
    stroke: str = "#000000",
    stroke_width: float = 0.5,
    padding: float = 2.0,
) -> dict:
    """Write outlines *and* placed images into one SVG, in one mm space.

    This is the single-file answer to "export the outline with the image on
    it": every other format needs the raster as a sidecar, because DXF and FVI
    have nowhere to put one. Images are embedded as base64 PNG, so the file
    stands alone.

    Coordinates are millimetres, y-up in the document. SVG's y axis points
    down, so both paths and images are emitted through the same
    ``y_svg = y_total - y_world`` flip rather than a group transform, which
    would mirror the rasters.
    """
    import base64

    from simple_stipple.platform.storage import atomic_write_via

    placements = list(images or [])
    boxes = [
        [
            (image.x_mm, image.y_mm),
            (image.x_mm + image.width_mm, image.y_mm + image.height_mm),
        ]
        for image in placements
    ]
    extent = [poly for poly in polys if poly] + boxes
    if not extent:
        raise ValueError("Nothing to export — the document has no geometry or images.")

    x0, y0, x1, y1 = _bbox(extent)
    vw = (x1 - x0) + padding * 2
    vh = (y1 - y0) + padding * 2
    y_total = y1 + padding

    root = ET.Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "xmlns:xlink": "http://www.w3.org/1999/xlink",
            "xmlns:stipple": STIPPLE_NS,
            "viewBox": f"{x0 - padding:.4f} 0 {vw:.4f} {vh:.4f}",
            "width": f"{vw:.4f}mm",
            "height": f"{vh:.4f}mm",
            # Where this drawing actually sits, so reopening it does not
            # translate the part onto its own bounding box.
            "stipple:world": f"0 {y_total:.6f}",
        },
    )
    # Images first so vector linework reads on top of the artwork.
    for image in placements:
        encoded = base64.b64encode(image.png_bytes).decode("ascii")
        top = y_total - (image.y_mm + image.height_mm)
        attrs = {
            "x": f"{image.x_mm:.4f}",
            "y": f"{top:.4f}",
            "width": f"{image.width_mm:.4f}",
            "height": f"{image.height_mm:.4f}",
            "preserveAspectRatio": "none",
            "href": f"data:image/png;base64,{encoded}",
        }
        if image.rotation_deg:
            centre_x = image.x_mm + image.width_mm / 2.0
            centre_y = top + image.height_mm / 2.0
            # Negated: the y flip reverses the sense of a rotation.
            attrs["transform"] = f"rotate({-image.rotation_deg:.4f} {centre_x:.4f} {centre_y:.4f})"
        ET.SubElement(root, "image", attrs)

    group = ET.SubElement(
        root,
        "g",
        {"fill": "none", "stroke": stroke, "stroke-width": f"{stroke_width:.4f}"},
    )
    for points in polys:
        if points:
            ET.SubElement(group, "path", d=_poly_to_svg_d(points, y_total))

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    atomic_write_via(
        output_path,
        lambda path: tree.write(str(path), xml_declaration=True, encoding="utf-8"),
    )
    return {
        "polylines": len(polys),
        "images": len(placements),
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
        mm_factor = (
            float(
                units.conversion_factor(
                    cast(units.InsertUnits, unit_code), cast(units.InsertUnits, 4)
                )
            )
            if unit_code
            else 1.0
        )
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
# Curve segments (path C/S/Q/T/A) are flattened at this resolution — matches
# build_bezier_poly's default elsewhere in the codebase.
_CURVE_SEGMENTS = 32
Matrix = se.Matrix


def _matrix_multiply(left: Matrix, right: Matrix) -> Matrix:
    """Combine two transforms so ``right`` applies first, then ``left``."""
    return right * left


def _apply_matrix(matrix: Matrix, point: tuple[float, float]) -> tuple[float, float]:
    result = matrix.point_in_matrix_space(point)
    return result.x, result.y


def _parse_transform(value: str) -> Matrix:
    """Parse an SVG ``transform`` attribute value into a matrix.

    svgelements already resolves the full transform grammar (matrix,
    translate, scale, rotate — including the 3-arg center form — skewX,
    skewY, chained in document order), so this is a direct pass-through.
    """
    return se.Matrix(value)


def _parse_points_attr(points: str) -> list[tuple[float, float]]:
    nums = re.findall(_NUM_RE, points)
    values = [float(n) for n in nums]
    pts: list[tuple[float, float]] = []
    for i in range(0, len(values) - 1, 2):
        pts.append((values[i], values[i + 1]))
    return pts


def _parse_path_d(d: str) -> list[list[tuple[float, float]]]:
    """Flatten an SVG path ``d`` string to polylines, in the path's own units.

    svgelements parses the full path grammar (including curves and arcs);
    this only has to walk its segments and sample the non-linear ones.
    """
    out: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []

    def push_current() -> None:
        nonlocal current
        if len(current) >= 2:
            out.append(current)
        current = []

    for seg in se.Path(d):
        if isinstance(seg, se.Move):
            push_current()
            current = [(seg.end.x, seg.end.y)] if seg.end is not None else []
        elif isinstance(seg, se.Close):
            if seg.end is not None:
                point = (seg.end.x, seg.end.y)
                if not current or current[-1] != point:
                    current.append(point)
            push_current()
        elif isinstance(seg, se.Line):
            if seg.end is not None:
                current.append((seg.end.x, seg.end.y))
        elif isinstance(seg, se.Curve):
            for step in range(1, _CURVE_SEGMENTS + 1):
                point = seg.point(step / _CURVE_SEGMENTS)
                current.append((point.x, point.y))

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
    factor = {
        None: 25.4 / 96.0,
        "px": 25.4 / 96.0,
        "mm": 1.0,
        "cm": 10.0,
        "in": 25.4,
        "pt": 25.4 / 72.0,
        "pc": 25.4 / 6.0,
    }[match.group(2)]
    return number * factor


def _root_svg_matrix(root: ET.Element) -> tuple[Matrix, float]:
    values = [float(value) for value in re.findall(_NUM_RE, root.attrib.get("viewBox", ""))]
    if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
        scale = 25.4 / 96.0
        height = _length_mm(root.attrib.get("height", ""), default_px=_svg_height(root))
        return se.Matrix(scale, 0, 0, scale, 0, 0), height
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
        return se.Matrix(scale, 0, 0, scale, ax - x0 * scale, ay - y0 * scale), height_mm
    return se.Matrix(sx, 0, 0, sy, -x0 * sx, -y0 * sy), height_mm


def _viewport_user_units(
    root: ET.Element,
    viewbox: list[float],
) -> tuple[float, float]:
    if len(viewbox) != 4:
        return 1.0, 1.0
    viewport_width_px = _length_mm(root.attrib.get("width", ""), default_px=viewbox[2]) * 96 / 25.4
    viewport_height_px = (
        _length_mm(root.attrib.get("height", ""), default_px=viewbox[3]) * 96 / 25.4
    )
    return viewbox[2] / viewport_width_px, viewbox[3] / viewport_height_px


def _svg_coordinate(
    value: str | None,
    axis: str,
    user_per_px_x: float,
    user_per_px_y: float,
) -> float:
    if value is None or not value.strip():
        return 0.0
    match = re.fullmatch(rf"\s*({_NUM_RE})\s*(px|mm|cm|in|pt|pc)?\s*", value)
    if not match:
        raise ValueError(f"Unsupported SVG coordinate: {value}")
    number = float(match.group(1))
    unit = match.group(2)
    if unit is None:
        return number
    millimetres = _length_mm(f"{number}{unit}", default_px=number)
    user_per_px = user_per_px_y if axis == "y" else user_per_px_x
    return millimetres * 96 / 25.4 * user_per_px


def _element_transform(element: ET.Element) -> Matrix:
    transform_value = element.attrib.get("transform", "")
    if transform_value:
        return _parse_transform(transform_value)
    style_match = re.search(
        r"(?:^|;)\s*transform\s*:\s*([^;]+)",
        element.attrib.get("style", ""),
    )
    return _parse_transform(style_match.group(1).strip() if style_match else "")


def _collect_transforms(root: ET.Element, root_matrix: Matrix) -> dict[int, Matrix]:
    transforms: dict[int, Matrix] = {}

    def collect(element: ET.Element, parent: Matrix) -> None:
        combined = _matrix_multiply(parent, _element_transform(element))
        transforms[id(element)] = combined
        for child in element:
            collect(child, combined)

    collect(root, root_matrix)
    return transforms


_NON_RENDERED_TAGS = frozenset({"defs", "clippath", "mask", "symbol", "marker"})


def _non_rendered_elements(root: ET.Element) -> set[int]:
    non_rendered: set[int] = set()

    def collect(element: ET.Element, ancestor_hidden: bool) -> None:
        tag = element.tag.split("}")[-1].lower()
        style = element.attrib.get("style", "").replace(" ", "").lower()
        hidden = (
            ancestor_hidden
            or tag in _NON_RENDERED_TAGS
            or element.attrib.get("display", "").lower() == "none"
            or "display:none" in style
            or element.attrib.get("visibility", "").lower() == "hidden"
        )
        if hidden:
            non_rendered.add(id(element))
        for child in element:
            collect(child, hidden)

    collect(root, False)
    return non_rendered


def _svg_output_size(
    records: list[tuple[list[tuple[float, float]], str, dict | None]],
) -> tuple[float, float]:
    points = [point for polyline, _kind, _meta in records for point in polyline]
    if not points:
        return 0.0, 0.0
    xs, ys = zip(*points)
    return max(xs) - min(xs), max(ys) - min(ys)


def read_svg_images(input_path: str | Path) -> list[SvgImagePlacement]:
    """Read ``<image>`` placements back out of an SVG, in world millimetres.

    ``svg_to_dxf`` recovers the linework and has nowhere to put a raster, so an
    SVG written by this app could not be reopened without losing its artwork.
    Placements come back through the same root matrix and y-flip the vector
    path uses, so an image and the outlines around it land in one space.

    Images referenced by an external path are resolved relative to the SVG;
    ones that cannot be read are skipped rather than failing the whole import.
    """
    import base64

    source = Path(input_path)
    root = ET.parse(str(source)).getroot()
    root_matrix, y_flip = _world_transform(root)
    viewbox = [float(value) for value in re.findall(_NUM_RE, root.attrib.get("viewBox", ""))]
    user_per_px_x, user_per_px_y = _viewport_user_units(root, viewbox)

    placements: list[SvgImagePlacement] = []
    for element in root.iter():
        if not element.tag.endswith("image"):
            continue
        href = element.attrib.get("href") or element.attrib.get(
            "{http://www.w3.org/1999/xlink}href", ""
        )
        if not href:
            continue
        if href.startswith("data:"):
            _header, _, payload = href.partition(",")
            try:
                # validate=True so junk raises rather than decoding to b"" —
                # an empty payload would otherwise become a zero-byte image
                # placed on the part.
                png_bytes = base64.b64decode(payload, validate=True)
            except (ValueError, TypeError):
                continue
        else:
            candidate = (source.parent / href).resolve()
            try:
                png_bytes = candidate.read_bytes()
            except OSError:
                continue
        if not png_bytes:
            continue

        x = _svg_coordinate(element.attrib.get("x"), "x", user_per_px_x, user_per_px_y)
        y = _svg_coordinate(element.attrib.get("y"), "y", user_per_px_x, user_per_px_y)
        width = _svg_coordinate(element.attrib.get("width"), "x", user_per_px_x, user_per_px_y)
        height = _svg_coordinate(element.attrib.get("height"), "y", user_per_px_x, user_per_px_y)
        if width <= 0 or height <= 0:
            continue
        matrix = _matrix_multiply(
            root_matrix, _parse_transform(element.attrib.get("transform") or "")
        )
        left, top = _apply_matrix(matrix, (x, y))
        right, bottom = _apply_matrix(matrix, (x + width, y + height))
        # SVG y grows downward, the document's grows up: the box's SVG bottom
        # edge is its world *top*, so the origin is the flipped far corner.
        world_x = min(left, right)
        world_y = (y_flip - max(top, bottom)) if y_flip else -max(top, bottom)
        rotation = _transform_rotation_deg(element.attrib.get("transform"))
        placements.append(
            SvgImagePlacement(
                png_bytes=png_bytes,
                x_mm=world_x,
                y_mm=world_y,
                width_mm=abs(right - left),
                height_mm=abs(bottom - top),
                # The writer negates rotation to cross the flip; undo that.
                rotation_deg=-rotation,
            )
        )
    return placements


def _transform_rotation_deg(transform: str | None) -> float:
    """The rotate() angle in a transform attribute, if it carries one."""
    if not transform:
        return 0.0
    match = re.search(rf"rotate\s*\(\s*({_NUM_RE})", transform)
    return float(match.group(1)) if match else 0.0


def _handle_svg_element(
    elem: ET.Element,
    coord: Callable[[str | None, str], float],
    tp: Callable[[tuple[float, float]], tuple[float, float]],
    records: list[tuple[list[tuple[float, float]], str, dict | None]],
    native_entities: dict[str, int],
    unsupported_paths: list[int],
) -> None:
    """Dispatch a single SVG element to the appropriate handler."""
    tag = elem.tag.split("}")[-1].lower()
    match tag:
        case "polyline":
            _handle_svg_polyline(elem, tp, records)
        case "polygon":
            _handle_svg_polygon(elem, tp, records)
        case "line":
            _handle_svg_line(elem, coord, tp, records, native_entities)
        case "rect":
            _handle_svg_rect(elem, coord, tp, records)
        case "circle":
            _handle_svg_circle(elem, coord, tp, records, native_entities)
        case "ellipse":
            _handle_svg_ellipse(elem, coord, tp, records)
        case "path":
            _handle_svg_path(elem, tp, records, unsupported_paths)


def _handle_svg_polyline(
    elem: ET.Element,
    tp: Callable[[tuple[float, float]], tuple[float, float]],
    records: list[tuple[list[tuple[float, float]], str, dict | None]],
) -> None:
    pts = _parse_points_attr(elem.attrib.get("points", ""))
    if len(pts) >= 2:
        records.append(([tp(point) for point in pts], "polyline", None))


def _handle_svg_polygon(
    elem: ET.Element,
    tp: Callable[[tuple[float, float]], tuple[float, float]],
    records: list[tuple[list[tuple[float, float]], str, dict | None]],
) -> None:
    pts = _parse_points_attr(elem.attrib.get("points", ""))
    if len(pts) >= 3:
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        records.append(([tp(point) for point in pts], "polyline", None))


def _handle_svg_line(
    elem: ET.Element,
    coord: Callable[[str | None, str], float],
    tp: Callable[[tuple[float, float]], tuple[float, float]],
    records: list[tuple[list[tuple[float, float]], str, dict | None]],
    native_entities: dict[str, int],
) -> None:
    x1 = coord(elem.attrib.get("x1"), "x")
    y1 = coord(elem.attrib.get("y1"), "y")
    x2 = coord(elem.attrib.get("x2"), "x")
    y2 = coord(elem.attrib.get("y2"), "y")
    start, end = tp((x1, y1)), tp((x2, y2))
    _add_meta_entity("line", {"start": start, "end": end}, "LINE", records, native_entities)


def _handle_svg_rect(
    elem: ET.Element,
    coord: Callable[[str | None, str], float],
    tp: Callable[[tuple[float, float]], tuple[float, float]],
    records: list[tuple[list[tuple[float, float]], str, dict | None]],
) -> None:
    x = coord(elem.attrib.get("x"), "x")
    y = coord(elem.attrib.get("y"), "y")
    w = coord(elem.attrib.get("width"), "x")
    h = coord(elem.attrib.get("height"), "y")
    if w <= 0 or h <= 0:
        return
    pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
    rx = max(0.0, min(coord(elem.attrib.get("rx"), "x"), w / 2))
    ry_raw = coord(elem.attrib.get("ry"), "y")
    ry = max(0.0, min(ry_raw if ry_raw else rx, h / 2))
    if rx and ry:
        pts = []
        for arc_cx, arc_cy, start_angle in (
            (x + w - rx, y + ry, -90),
            (x + w - rx, y + h - ry, 0),
            (x + rx, y + h - ry, 90),
            (x + rx, y + ry, 180),
        ):
            for step in range(5):
                angle = math.radians(start_angle + step * 22.5)
                pts.append((arc_cx + rx * math.cos(angle), arc_cy + ry * math.sin(angle)))
        pts.append(pts[0])
    records.append(([tp(point) for point in pts], "polyline", None))


def _handle_svg_circle(
    elem: ET.Element,
    coord: Callable[[str | None, str], float],
    tp: Callable[[tuple[float, float]], tuple[float, float]],
    records: list[tuple[list[tuple[float, float]], str, dict | None]],
    native_entities: dict[str, int],
) -> None:
    cx = coord(elem.attrib.get("cx"), "x")
    cy = coord(elem.attrib.get("cy"), "y")
    r = coord(elem.attrib.get("r"), "x")
    if r <= 0:
        return
    center = tp((cx, cy))
    edge_x = tp((cx + r, cy))
    edge_y = tp((cx, cy + r))
    rx2, ry2 = math.dist(center, edge_x), math.dist(center, edge_y)
    if abs(rx2 - ry2) <= 1e-9 * max(rx2, ry2, 1.0):
        _add_meta_entity(
            "circle", {"center": center, "radius": rx2}, "CIRCLE", records, native_entities
        )
    else:
        pts = [
            tp((cx + r * math.cos(a), cy + r * math.sin(a)))
            for a in [i * math.tau / 64 for i in range(65)]
        ]
        records.append((pts, "polyline", None))


def _handle_svg_ellipse(
    elem: ET.Element,
    coord: Callable[[str | None, str], float],
    tp: Callable[[tuple[float, float]], tuple[float, float]],
    records: list[tuple[list[tuple[float, float]], str, dict | None]],
) -> None:
    cx = coord(elem.attrib.get("cx"), "x")
    cy = coord(elem.attrib.get("cy"), "y")
    rx = coord(elem.attrib.get("rx"), "x")
    ry = coord(elem.attrib.get("ry"), "y")
    if rx <= 0 or ry <= 0:
        return
    pts = [
        tp((cx + rx * math.cos(a), cy + ry * math.sin(a)))
        for a in [i * math.tau / 64 for i in range(65)]
    ]
    records.append((pts, "polyline", None))


def _handle_svg_path(
    elem: ET.Element,
    tp: Callable[[tuple[float, float]], tuple[float, float]],
    records: list[tuple[list[tuple[float, float]], str, dict | None]],
    unsupported_paths: list[int],
) -> None:
    d = elem.attrib.get("d", "")
    if not d.strip():
        return
    polylines = _parse_path_d(d)
    if not polylines:
        unsupported_paths[0] += 1
        return
    for p in polylines:
        records.append(([tp(point) for point in p], "polyline", None))


def _add_meta_entity(
    kind: str,
    meta: dict,
    counter: str,
    records: list[tuple[list[tuple[float, float]], str, dict | None]],
    native_entities: dict[str, int],
) -> None:
    shape = shape_from_meta(kind, meta)
    if shape is None:
        return
    pts = [(x, y) for x, y in shape.points]
    if len(pts) >= 2:
        records.append((pts, kind, meta))
        native_entities[counter] += 1


def svg_to_dxf(
    input_path: str | Path,
    output_path: str | Path,
    *,
    flip_y: bool = True,
) -> dict:
    """Convert supported SVG elements to DXF.

    Parsing produces (polyline, kind, meta) records which are written by
    :func:`simple_stipple.core.formats.dxf.write_polylines_dxf` — the single DXF writer,
    so SVG conversions get the same validation guarantees as every other
    export (audit gate, finite checks, ellipse axis handling). Previously
    this function assembled its own ezdxf document and wrote it even when
    the audit reported errors.
    """
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
    root_matrix, y_flip = _world_transform(root)
    viewbox = [float(value) for value in re.findall(_NUM_RE, root.attrib.get("viewBox", ""))]
    user_per_px_x, user_per_px_y = _viewport_user_units(root, viewbox)

    def coord(value: str | None, axis: str = "x") -> float:
        return _svg_coordinate(value, axis, user_per_px_x, user_per_px_y)

    def yf(y: float) -> float:
        if not flip_y:
            return y
        return y_flip - y if y_flip else -y

    records: list[tuple[list[tuple[float, float]], str, dict | None]] = []
    native_entities = {"LINE": 0, "CIRCLE": 0, "ELLIPSE": 0}
    unsupported_paths: list[int] = [0]
    unsupported_features: set[str] = set()

    transforms = _collect_transforms(root, root_matrix)
    non_rendered = _non_rendered_elements(root)

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

        _handle_svg_element(elem, coord, tp, records, native_entities, unsupported_paths)

    write_polylines_dxf(
        [poly for poly, _k, _m in records],
        str(output_path),
        entity_kinds=[k for _p, k, _m in records],
        entity_meta=[m for _p, _k, m in records],
    )

    plain = [poly for poly, kind, _m in records if kind == "polyline"]
    width, height = _svg_output_size(records)

    return {
        "polylines": len(plain),
        "width_mm": round(width, 4),
        "height_mm": round(height, 4),
        "native_entities": native_entities,
        "unsupported_paths": unsupported_paths[0],
        "unsupported_features": tuple(sorted(unsupported_features)),
    }
