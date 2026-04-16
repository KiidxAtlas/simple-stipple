"""SVG → DXF converter.

Parses common SVG primitives and writes them as DXF LWPOLYLINE entities.
Supported SVG elements:
- polyline
- polygon
- line
- rect
- circle
- ellipse
- path (M/L/H/V/Z commands, absolute + relative)
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from src.core.dxf.io import write_polylines_dxf

_NUM_RE = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"
_CMD_RE = re.compile(rf"[MmLlHhVvZz]|{_NUM_RE}")


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


def _circle_poly(
    cx: float, cy: float, rx: float, ry: float, segments: int = 64
) -> list[tuple[float, float]]:
    pts = []
    for i in range(segments):
        a = (2.0 * math.pi * i) / segments
        pts.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
    pts.append(pts[0])
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
    tree = ET.parse(str(input_path))
    root = tree.getroot()
    y_flip = _svg_height(root)

    def yf(y: float) -> float:
        if not flip_y:
            return y
        return y_flip - y if y_flip else -y

    polylines: list[list[tuple[float, float]]] = []

    for elem in root.iter():
        tag = elem.tag.split("}")[-1].lower()

        if tag == "polyline":
            pts = _parse_points_attr(elem.attrib.get("points", ""))
            if len(pts) >= 2:
                polylines.append([(x, yf(y)) for x, y in pts])

        elif tag == "polygon":
            pts = _parse_points_attr(elem.attrib.get("points", ""))
            if len(pts) >= 3:
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                polylines.append([(x, yf(y)) for x, y in pts])

        elif tag == "line":
            x1 = _parse_float(elem.attrib.get("x1"))
            y1 = _parse_float(elem.attrib.get("y1"))
            x2 = _parse_float(elem.attrib.get("x2"))
            y2 = _parse_float(elem.attrib.get("y2"))
            polylines.append([(x1, yf(y1)), (x2, yf(y2))])

        elif tag == "rect":
            x = _parse_float(elem.attrib.get("x"))
            y = _parse_float(elem.attrib.get("y"))
            w = _parse_float(elem.attrib.get("width"))
            h = _parse_float(elem.attrib.get("height"))
            if w > 0 and h > 0:
                pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
                polylines.append([(px, yf(py)) for px, py in pts])

        elif tag == "circle":
            cx = _parse_float(elem.attrib.get("cx"))
            cy = _parse_float(elem.attrib.get("cy"))
            r = _parse_float(elem.attrib.get("r"))
            if r > 0:
                pts = _circle_poly(cx, cy, r, r)
                polylines.append([(x, yf(y)) for x, y in pts])

        elif tag == "ellipse":
            cx = _parse_float(elem.attrib.get("cx"))
            cy = _parse_float(elem.attrib.get("cy"))
            rx = _parse_float(elem.attrib.get("rx"))
            ry = _parse_float(elem.attrib.get("ry"))
            if rx > 0 and ry > 0:
                pts = _circle_poly(cx, cy, rx, ry)
                polylines.append([(x, yf(y)) for x, y in pts])

        elif tag == "path":
            d = elem.attrib.get("d", "")
            if not d.strip():
                continue
            for p in _parse_path_d(d):
                if len(p) >= 2:
                    polylines.append([(x, yf(y)) for x, y in p])

    write_polylines_dxf(polylines, str(output_path), close=False)

    all_pts = [pt for poly in polylines for pt in poly]
    if all_pts:
        xs, ys = zip(*all_pts)
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
    else:
        width = height = 0.0

    return {
        "polylines": len(polylines),
        "width_mm": round(width, 4),
        "height_mm": round(height, 4),
    }
