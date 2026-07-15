"""Shared geometry readiness analysis used before fabrication/export."""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import LineString, Polygon  # type: ignore[import-untyped]


@dataclass(frozen=True)
class GeometryIssue:
    """A locatable geometry-health finding suitable for canvas overlays."""

    kind: str
    path_index: int
    point: tuple[float, float]
    message: str
    severity: str = "warning"


@dataclass(frozen=True)
class GeometryPreflight:
    paths: int
    closed: int
    open: int
    invalid: int
    duplicates: int
    zero_segments: int
    tiny_paths: int
    minimum_segment: float | None
    tolerance: float
    near_closed: int = 0
    issues: tuple[GeometryIssue, ...] = ()

    @property
    def ready(self) -> bool:
        return (
            self.paths > 0
            and self.invalid == 0
            and self.zero_segments == 0
            and self.duplicates == 0
            and self.tiny_paths == 0
            and self.near_closed == 0
        )

    def summary(self) -> str:
        state = "Geometry valid" if self.ready else "Needs attention"
        return (
            f"{state} · {self.paths} paths · {self.closed} closed · {self.open} open · "
            f"{self.invalid} invalid · {self.duplicates} duplicate · "
            f"{self.zero_segments} zero-length segments · {self.tiny_paths} tiny · "
            f"{self.near_closed} nearly closed"
        )


def scale_tolerance(polys: list[list[tuple[float, float]]]) -> float:
    points = [point for poly in polys for point in poly]
    if not points:
        return 0.01
    xs, ys = zip(*points)
    diagonal = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    return min(0.05, max(0.001, diagonal * 1e-5))


def analyze_geometry(polys: list[list[tuple[float, float]]]) -> GeometryPreflight:
    tolerance = scale_tolerance(polys)
    closed = invalid = duplicates = zero = tiny = near_closed = 0
    minimum: float | None = None
    signatures: set[tuple[tuple[float, float], ...]] = set()
    issues: list[GeometryIssue] = []
    for path_index, poly in enumerate(polys):
        if not poly:
            invalid += 1
            issues.append(GeometryIssue("empty", path_index, (0.0, 0.0), "Empty path", "error"))
            continue
        signature = tuple((round(x, 6), round(y, 6)) for x, y in poly)
        signature_points = signature[:-1] if len(signature) > 2 and signature[0] == signature[-1] else signature
        if len(signature_points) >= 3 and math.dist(poly[0], poly[-1]) <= tolerance:
            rotations = [
                signature_points[index:] + signature_points[:index]
                for index in range(len(signature_points))
            ]
            reversed_points = tuple(reversed(signature_points))
            rotations.extend(
                reversed_points[index:] + reversed_points[:index]
                for index in range(len(reversed_points))
            )
            canonical = min(rotations)
        else:
            canonical = min(signature, tuple(reversed(signature)))
        if canonical in signatures:
            duplicates += 1
            issues.append(GeometryIssue("duplicate", path_index, poly[0], "Duplicate path"))
        signatures.add(canonical)
        lengths = [math.dist(a, b) for a, b in zip(poly, poly[1:])]
        for segment_index, length in enumerate(lengths):
            if length <= 1e-12:
                zero += 1
                issues.append(
                    GeometryIssue(
                        "zero_segment",
                        path_index,
                        poly[segment_index],
                        "Zero-length segment",
                        "error",
                    )
                )
        for length in lengths:
            if length > 0:
                minimum = length if minimum is None else min(minimum, length)
        endpoint_gap = math.dist(poly[0], poly[-1]) if len(poly) >= 2 else math.inf
        exactly_closed = endpoint_gap <= 1e-9
        is_closed = len(poly) >= 3 and endpoint_gap <= tolerance
        if is_closed:
            closed += 1
            if not exactly_closed:
                near_closed += 1
                issues.append(
                    GeometryIssue(
                        "near_closed",
                        path_index,
                        poly[-1],
                        f"Endpoints are {endpoint_gap:.6g} mm apart and only tolerance-closed",
                    )
                )
            try:
                polygon_points = list(poly)
                polygon_points[-1] = polygon_points[0]
                shape = Polygon(polygon_points)
                if not shape.is_valid or shape.is_empty:
                    invalid += 1
                    issues.append(
                        GeometryIssue(
                            "invalid", path_index, poly[0], "Invalid closed path", "error"
                        )
                    )
                if abs(float(shape.area)) < tolerance * tolerance * 10:
                    tiny += 1
                    issues.append(GeometryIssue("tiny", path_index, poly[0], "Tiny closed path"))
            except (TypeError, ValueError):
                invalid += 1
                issues.append(
                    GeometryIssue("invalid", path_index, poly[0], "Unreadable closed path", "error")
                )
        else:
            issues.append(GeometryIssue("open_start", path_index, poly[0], "Open endpoint", "info"))
            if len(poly) > 1:
                issues.append(
                    GeometryIssue("open_end", path_index, poly[-1], "Open endpoint", "info")
                )
            try:
                line = LineString(poly)
                if not line.is_valid or line.is_empty:
                    invalid += 1
                    issues.append(
                        GeometryIssue("invalid", path_index, poly[0], "Invalid open path", "error")
                    )
                if float(line.length) < tolerance:
                    tiny += 1
                    issues.append(GeometryIssue("tiny", path_index, poly[0], "Tiny open path"))
            except (TypeError, ValueError):
                invalid += 1
                issues.append(
                    GeometryIssue("invalid", path_index, poly[0], "Unreadable open path", "error")
                )
    return GeometryPreflight(
        paths=len(polys),
        closed=closed,
        open=len(polys) - closed,
        invalid=invalid,
        duplicates=duplicates,
        zero_segments=zero,
        tiny_paths=tiny,
        minimum_segment=minimum,
        tolerance=tolerance,
        near_closed=near_closed,
        issues=tuple(issues),
    )
