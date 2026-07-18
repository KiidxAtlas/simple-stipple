"""FiberStar/StarFX FVI vector-program import and export.

FVI coordinates are relative and use 0.01 inch units (0.254 mm).  This
module intentionally supports only the geometry commands documented by the
supplied FiberStar command reference: ``MOVEDIST``, ``DRAWLINE`` and
``DRAWARC``.  Other commands are reported instead of being executed.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import ezdxf  # type: ignore[attr-defined]
from ezdxf.math import ConstructionArc  # type: ignore[attr-defined]

from ..persistence import atomic_write_via

FVI_UNIT_MM = 0.254
_ARC_SAGITTA_FVI = 0.01 / FVI_UNIT_MM
_DXF_CLOSE_TOL_MM = 0.01
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_COMMAND = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\s*(.*)$")

Point = tuple[float, float]
OriginMode = Literal["preserve", "lower_left", "center"]


class FviNoGeometryError(ValueError):
    """The input is readable but contains no supported drawable commands."""


@dataclass(frozen=True)
class FviImportReport:
    line_count: int
    path_count: int
    draw_line_count: int
    draw_arc_count: int
    ignored_commands: tuple[str, ...] = ()
    malformed_lines: tuple[int, ...] = ()

    @property
    def has_issues(self) -> bool:
        return bool(self.ignored_commands or self.malformed_lines)


@dataclass(frozen=True)
class FviDocument:
    paths: tuple[tuple[Point, ...], ...]
    report: FviImportReport
    segments: tuple[FviSegment, ...] = ()

    @property
    def bounds(self) -> tuple[float, float, float, float] | None:
        points = [point for path in self.paths for point in path]
        if not points:
            return None
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return min(xs), min(ys), max(xs), max(ys)


@dataclass(frozen=True)
class FviSegment:
    kind: Literal["line", "arc"]
    start: Point
    end: Point
    center: Point | None = None
    clockwise: bool = False


@dataclass(frozen=True)
class FviExportOptions:
    origin: OriginMode = "lower_left"
    margin_mm: float = 0.0
    precision: int = 6
    optimize_travel: bool = True
    reverse_open_paths: bool = True
    preserve_arcs: bool = True
    include_comments: bool = True
    flip_y: bool = False

    def validated(self) -> FviExportOptions:
        if self.origin not in {"preserve", "lower_left", "center"}:
            raise ValueError(f"Unsupported FVI origin mode: {self.origin}")
        if not math.isfinite(self.margin_mm) or self.margin_mm < 0:
            raise ValueError("FVI margin must be a finite, non-negative number.")
        if not 0 <= self.precision <= 9:
            raise ValueError("FVI precision must be between 0 and 9 decimal places.")
        return self


@dataclass(frozen=True)
class FviExportReport:
    path_count: int
    draw_line_count: int
    draw_arc_count: int
    travel_mm: float
    bounds_mm: tuple[float, float, float, float] | None
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _numbers(payload: str, expected: int) -> tuple[float, ...] | None:
    parts = [part.strip() for part in payload.split(",")]
    if len(parts) != expected or any(not re.fullmatch(_NUMBER, part) for part in parts):
        return None
    values = tuple(float(part) for part in parts)
    return values if all(math.isfinite(value) for value in values) else None


def _arc_points(x: float, y: float, ex: float, ey: float, cx: float, cy: float) -> list[Point]:
    """Flatten one relative FVI arc and return start-exclusive points in mm."""
    center_x, center_y = x + cx, y + cy
    end_x, end_y = x + ex, y + ey
    radius = math.hypot(cx, cy)
    if radius < 1e-9:
        return [(end_x * FVI_UNIT_MM, end_y * FVI_UNIT_MM)]

    start_angle = math.degrees(math.atan2(-cy, -cx))
    end_angle = math.degrees(math.atan2(end_y - center_y, end_x - center_x))
    cross = ex * cy - ey * cx
    if cross >= 0:
        arc = ConstructionArc(
            center=(center_x, center_y),
            radius=radius,
            start_angle=start_angle,
            end_angle=end_angle,
        )
    else:
        arc = ConstructionArc(
            center=(center_x, center_y),
            radius=radius,
            start_angle=end_angle,
            end_angle=start_angle,
        )
    points = list(arc.flattening(_ARC_SAGITTA_FVI))
    if cross < 0:
        points.reverse()
    if len(points) <= 1:
        return [(end_x * FVI_UNIT_MM, end_y * FVI_UNIT_MM)]
    result = [(float(point.x) * FVI_UNIT_MM, float(point.y) * FVI_UNIT_MM) for point in points[1:]]
    result[-1] = (end_x * FVI_UNIT_MM, end_y * FVI_UNIT_MM)
    return result


def parse_fvi(text: str) -> FviDocument:
    """Parse FVI geometry without executing loops, file calls, or hardware I/O."""
    x = y = 0.0
    current: list[Point] = []
    paths: list[tuple[Point, ...]] = []
    ignored: set[str] = set()
    malformed: list[int] = []
    segments: list[FviSegment] = []
    line_count = arc_count = 0

    def flush() -> None:
        nonlocal current
        if len(current) >= 2:
            paths.append(tuple(current))
        current = []

    raw_lines = text.splitlines()
    for line_number, raw in enumerate(raw_lines, 1):
        content = raw.split(";", 1)[0].strip()
        if not content:
            continue
        match = _COMMAND.match(content)
        if not match:
            malformed.append(line_number)
            continue
        command, payload = match.group(1).upper(), match.group(2).strip()
        expected = 4 if command == "DRAWARC" else 2
        if command not in {"MOVEDIST", "DRAWLINE", "DRAWARC"}:
            ignored.add(command)
            continue
        values = _numbers(payload, expected)
        if values is None:
            malformed.append(line_number)
            continue
        if command == "MOVEDIST":
            flush()
            x += values[0]
            y += values[1]
            current = [(x * FVI_UNIT_MM, y * FVI_UNIT_MM)]
        elif command == "DRAWLINE":
            if not current:
                current = [(x * FVI_UNIT_MM, y * FVI_UNIT_MM)]
            start = (x * FVI_UNIT_MM, y * FVI_UNIT_MM)
            x += values[0]
            y += values[1]
            segments.append(FviSegment("line", start, (x * FVI_UNIT_MM, y * FVI_UNIT_MM)))
            current.append((x * FVI_UNIT_MM, y * FVI_UNIT_MM))
            line_count += 1
        else:
            if not current:
                current = [(x * FVI_UNIT_MM, y * FVI_UNIT_MM)]
            ex, ey, cx, cy = values
            radius = math.hypot(cx, cy)
            end_radius = math.hypot(ex - cx, ey - cy)
            if radius < 1e-9 or abs(radius - end_radius) > max(1e-6, radius * 1e-6):
                malformed.append(line_number)
                flush()
                x += ex
                y += ey
                continue
            start = (x * FVI_UNIT_MM, y * FVI_UNIT_MM)
            center = ((x + cx) * FVI_UNIT_MM, (y + cy) * FVI_UNIT_MM)
            current.extend(_arc_points(x, y, ex, ey, cx, cy))
            x += ex
            y += ey
            segments.append(
                FviSegment(
                    "arc",
                    start,
                    (x * FVI_UNIT_MM, y * FVI_UNIT_MM),
                    center,
                    clockwise=(ex * cy - ey * cx) < 0,
                )
            )
            arc_count += 1
    flush()
    return FviDocument(
        paths=tuple(paths),
        report=FviImportReport(
            line_count=len(raw_lines),
            path_count=len(paths),
            draw_line_count=line_count,
            draw_arc_count=arc_count,
            ignored_commands=tuple(sorted(ignored)),
            malformed_lines=tuple(malformed),
        ),
        segments=tuple(segments),
    )


def read_fvi(path: str | Path) -> FviDocument:
    source = Path(path)
    max_bytes = 32 * 1024 * 1024
    size = source.stat().st_size
    if size > max_bytes:
        raise ValueError(
            f"{source.name} is too large to import safely "
            f"({size / (1024 * 1024):.1f} MB; limit 32 MB)."
        )
    try:
        text = source.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = source.read_text(encoding="latin-1")
    return parse_fvi(text)


def summarize_fvi_import(report: FviImportReport) -> str | None:
    parts: list[str] = []
    if report.ignored_commands:
        parts.append("ignored non-geometry commands: " + ", ".join(report.ignored_commands))
    if report.malformed_lines:
        shown = ", ".join(str(n) for n in report.malformed_lines[:8])
        suffix = "…" if len(report.malformed_lines) > 8 else ""
        parts.append(f"malformed command lines: {shown}{suffix}")
    return "; ".join(parts) or None


def _polyline_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for record in records:
        points: list[Point] = []
        for raw in record.get("polyline", record.get("points", [])):
            if len(raw) < 2:
                continue
            point = (float(raw[0]), float(raw[1]))
            if all(math.isfinite(value) for value in point):
                if not points or math.dist(points[-1], point) > 1e-9:
                    points.append(point)
        if len(points) >= 2:
            result.append({**record, "polyline": points})
    return result


def _transform_records(
    records: list[dict[str, Any]], options: FviExportOptions
) -> tuple[list[dict[str, Any]], tuple[float, float, float, float] | None]:
    all_points = [point for record in records for point in record["polyline"]]
    if not all_points:
        return records, None
    min_x = min(point[0] for point in all_points)
    max_x = max(point[0] for point in all_points)
    min_y = min(point[1] for point in all_points)
    max_y = max(point[1] for point in all_points)
    if options.origin == "lower_left":
        offset_x, offset_y = options.margin_mm - min_x, options.margin_mm - min_y
    elif options.origin == "center":
        offset_x, offset_y = -(min_x + max_x) / 2.0, -(min_y + max_y) / 2.0
    else:
        offset_x = offset_y = 0.0

    def transform(point: Point) -> Point:
        x, y = point[0] + offset_x, point[1] + offset_y
        return x, -y if options.flip_y else y

    transformed: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)
        item["polyline"] = [transform(point) for point in record["polyline"]]
        meta = dict(record.get("meta") or {})
        for key in ("center", "start", "end"):
            raw = meta.get(key)
            if isinstance(raw, (list, tuple)) and len(raw) >= 2:
                meta[key] = transform((float(raw[0]), float(raw[1])))
        if options.flip_y and record.get("kind") == "arc":
            meta["start_angle"], meta["end_angle"] = (
                -float(meta.get("end_angle", 0.0)),
                -float(meta.get("start_angle", 0.0)),
            )
        item["meta"] = meta
        transformed.append(item)
    points = [point for record in transformed for point in record["polyline"]]
    bounds = (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )
    return transformed, bounds


def _order_records(records: list[dict[str, Any]], *, allow_reverse: bool) -> list[dict[str, Any]]:
    remaining = [dict(record) for record in records]
    ordered: list[dict[str, Any]] = []
    cursor: Point = (0.0, 0.0)
    while remaining:
        best_index = 0
        best_reverse = False
        best_distance = math.inf
        for index, record in enumerate(remaining):
            points = record["polyline"]
            candidates = [(math.dist(cursor, points[0]), False)]
            closed = math.dist(points[0], points[-1]) <= 1e-6
            if allow_reverse and not closed:
                candidates.append((math.dist(cursor, points[-1]), True))
            distance, reverse = min(candidates)
            if distance < best_distance:
                best_index, best_reverse, best_distance = index, reverse, distance
        item = remaining.pop(best_index)
        if best_reverse:
            original_kind = str(item.get("kind", "polyline"))
            item["polyline"] = list(reversed(item["polyline"]))
            # Reversing native curves safely requires rewriting their sweep.
            item["kind"] = "polyline"
            item["meta"] = {}
            if original_kind in {"arc", "circle", "ellipse", "bezier", "spline"}:
                item["_fvi_reversed_curve"] = original_kind
        ordered.append(item)
        cursor = item["polyline"][-1]
    return ordered


def _format_delta(value_mm: float, precision: int) -> str:
    value = value_mm / FVI_UNIT_MM
    if abs(value) < 0.5 * 10 ** (-precision):
        value = 0.0
    return f"{value:.{precision}f}"


def _quantized_delta(value_mm: float, precision: int) -> tuple[str, float]:
    text = _format_delta(value_mm, precision)
    return text, float(text) * FVI_UNIT_MM


def render_fvi(
    records: Sequence[dict[str, Any]], options: FviExportOptions | None = None
) -> tuple[str, FviExportReport]:
    options = (options or FviExportOptions()).validated()
    clean = _polyline_records(records)
    transformed, bounds = _transform_records(clean, options)
    if options.optimize_travel:
        transformed = _order_records(transformed, allow_reverse=options.reverse_open_paths)

    lines: list[str] = []
    if options.include_comments:
        lines.extend(
            [
                "; Generated by Simple Stipple",
                f"; Units: 1 FVI unit = {FVI_UNIT_MM:g} mm",
                f"; Paths: {len(transformed)}",
            ]
        )
    cursor: Point = (0.0, 0.0)
    draw_lines = draw_arcs = 0
    travel = 0.0
    warnings: list[str] = []

    for index, record in enumerate(transformed, 1):
        points: list[Point] = record["polyline"]
        start, end = points[0], points[-1]
        move = (start[0] - cursor[0], start[1] - cursor[1])
        move_x, actual_move_x = _quantized_delta(move[0], options.precision)
        move_y, actual_move_y = _quantized_delta(move[1], options.precision)
        travel += math.hypot(actual_move_x, actual_move_y)
        if options.include_comments:
            label = str((record.get("meta") or {}).get("name") or record.get("layer") or index)
            lines.append(f"; Path {index}: {label}")
        lines.append(f"MOVEDIST {move_x},{move_y}")
        cursor = (cursor[0] + actual_move_x, cursor[1] + actual_move_y)

        kind = str(record.get("kind", "polyline"))
        meta = record.get("meta") or {}
        center_raw = meta.get("center")
        can_arc = (
            options.preserve_arcs
            and kind == "arc"
            and isinstance(center_raw, (list, tuple))
            and len(center_raw) >= 2
        )
        if can_arc and center_raw is not None:
            center_values = cast(Sequence[Any], center_raw)
            center = (float(center_values[0]), float(center_values[1]))
            end_delta = (end[0] - cursor[0], end[1] - cursor[1])
            center_delta = (center[0] - cursor[0], center[1] - cursor[1])
            ex_text, ex_actual = _quantized_delta(end_delta[0], options.precision)
            ey_text, ey_actual = _quantized_delta(end_delta[1], options.precision)
            cx_text, cx_actual = _quantized_delta(center_delta[0], options.precision)
            cy_text, cy_actual = _quantized_delta(center_delta[1], options.precision)
            start_radius = math.hypot(cx_actual, cy_actual)
            end_radius = math.hypot(ex_actual - cx_actual, ey_actual - cy_actual)
            if start_radius <= 1e-12 or abs(start_radius - end_radius) > max(
                1e-9, start_radius * 1e-6
            ):
                raise ValueError("FVI precision is too low to preserve a native arc.")
            lines.append(f"DRAWARC {ex_text},{ey_text},{cx_text},{cy_text}")
            cursor = (cursor[0] + ex_actual, cursor[1] + ey_actual)
            draw_arcs += 1
        elif (
            options.preserve_arcs
            and kind == "circle"
            and isinstance(center_raw, (list, tuple))
            and len(center_raw) >= 2
        ):
            center = (float(center_raw[0]), float(center_raw[1]))
            opposite = (2.0 * center[0] - start[0], 2.0 * center[1] - start[1])
            for target in (opposite, start):
                center_delta = (center[0] - cursor[0], center[1] - cursor[1])
                delta = (target[0] - cursor[0], target[1] - cursor[1])
                dx_text, dx_actual = _quantized_delta(delta[0], options.precision)
                dy_text, dy_actual = _quantized_delta(delta[1], options.precision)
                lines.append(
                    "DRAWARC "
                    f"{dx_text},{dy_text},"
                    f"{_format_delta(center_delta[0], options.precision)},"
                    f"{_format_delta(center_delta[1], options.precision)}"
                )
                cursor = (cursor[0] + dx_actual, cursor[1] + dy_actual)
                draw_arcs += 1
        else:
            for target in points[1:]:
                delta = (target[0] - cursor[0], target[1] - cursor[1])
                if math.hypot(*delta) <= 1e-9:
                    continue
                dx_text, dx_actual = _quantized_delta(delta[0], options.precision)
                dy_text, dy_actual = _quantized_delta(delta[1], options.precision)
                if dx_actual == 0.0 and dy_actual == 0.0:
                    raise ValueError(
                        "FVI precision is too low to represent one or more drawing segments."
                    )
                lines.append(f"DRAWLINE {dx_text},{dy_text}")
                cursor = (cursor[0] + dx_actual, cursor[1] + dy_actual)
                draw_lines += 1
            if kind in {"bezier", "spline", "ellipse"}:
                warnings.append(f"{kind.title()} geometry was exported as line segments.")
            if record.get("_fvi_reversed_curve"):
                warnings.append("A native curve was reversed and exported as line segments.")

    return "\n".join(lines) + "\n", FviExportReport(
        path_count=len(transformed),
        draw_line_count=draw_lines,
        draw_arc_count=draw_arcs,
        travel_mm=travel,
        bounds_mm=bounds,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def write_fvi(
    records: Sequence[dict[str, Any]],
    path: str | Path,
    options: FviExportOptions | None = None,
) -> FviExportReport:
    text, report = render_fvi(records, options)

    def write(temporary: Path) -> None:
        temporary.write_text(text, encoding="utf-8")

    atomic_write_via(path, write)
    return report


def convert_fvi_to_dxf(src: Path, dst: Path) -> FviImportReport:
    """Convert each continuous FVI draw path into one joined DXF polyline.

    FVI coordinates are commonly rounded to three decimals, so a nominally
    closed path can accumulate a sub-micron endpoint residual.  Endpoints
    within ``_DXF_CLOSE_TOL_MM`` are represented with the DXF closed flag.
    Arcs are tessellated using the parser's fixed sagitta tolerance so path
    continuity is retained instead of exporting disconnected LINE/ARC pieces.
    """
    parsed = read_fvi(src)
    if not parsed.paths:
        if src.stat().st_size == 0:
            raise FviNoGeometryError("The FVI file is empty.")
        details = summarize_fvi_import(parsed.report)
        raise FviNoGeometryError(
            "The FVI file contains no supported drawable geometry."
            + (f" {details}" if details else "")
        )
    doc = cast(Any, ezdxf).new("R2010")
    doc.header["$INSUNITS"] = 4
    modelspace = doc.modelspace()
    for path in parsed.paths:
        points = list(path)
        closed = len(points) >= 3 and math.dist(points[0], points[-1]) <= _DXF_CLOSE_TOL_MM
        if closed:
            points = points[:-1]
        if len(points) >= 2:
            modelspace.add_lwpolyline(points, close=closed)
    atomic_write_via(dst, lambda temporary: doc.saveas(str(temporary)))
    return parsed.report


__all__ = [
    "FVI_UNIT_MM",
    "FviDocument",
    "FviExportOptions",
    "FviExportReport",
    "FviImportReport",
    "FviNoGeometryError",
    "FviSegment",
    "convert_fvi_to_dxf",
    "parse_fvi",
    "read_fvi",
    "render_fvi",
    "summarize_fvi_import",
    "write_fvi",
]
