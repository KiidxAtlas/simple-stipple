"""Machine-independent production planning for vector jobs.

The editor owns geometry and format writers own their file syntax.  This
module sits between them: it reports what a machine operator needs to know and
provides explicit, opt-in transformations for common fabrication concerns.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from shapely.geometry import Polygon  # type: ignore[import-untyped]

from simple_stipple.core.editing.boolean import offset_polyline

Point = tuple[float, float]
Polyline = list[Point]
Bounds = tuple[float, float, float, float]
KerfMode = Literal["inside", "outside", "none"]


@dataclass(frozen=True)
class MachineProfile:
    """Portable machine assumptions used for review, never machine control."""

    name: str = "No machine selected"
    bed_width_mm: float | None = None
    bed_height_mm: float | None = None
    feed_rate_mm_s: float | None = None
    kerf_mm: float = 0.0

    def has_bed(self) -> bool:
        return bool(
            self.bed_width_mm is not None
            and self.bed_height_mm is not None
            and self.bed_width_mm > 0
            and self.bed_height_mm > 0
        )

    def validated(self) -> MachineProfile:
        for label, value in (
            ("Bed width", self.bed_width_mm),
            ("Bed height", self.bed_height_mm),
            ("Feed rate", self.feed_rate_mm_s),
        ):
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"{label} must be a positive number when configured.")
        if not math.isfinite(self.kerf_mm) or self.kerf_mm < 0:
            raise ValueError("Kerf must be zero or a positive number.")
        return self


def machine_profile_from_settings(settings: dict) -> MachineProfile:
    """Read a tolerant profile snapshot from persisted application settings."""

    def positive(key: str) -> float | None:
        try:
            value = float(settings.get(key, 0.0) or 0.0)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) and value > 0 else None

    try:
        kerf = float(settings.get("machine_kerf_mm", 0.0) or 0.0)
    except (TypeError, ValueError):
        kerf = 0.0
    return MachineProfile(
        name=str(
            settings.get("machine_profile_name", "No machine selected") or "No machine selected"
        ),
        bed_width_mm=positive("machine_bed_width_mm"),
        bed_height_mm=positive("machine_bed_height_mm"),
        feed_rate_mm_s=positive("machine_feed_rate_mm_s"),
        kerf_mm=kerf if math.isfinite(kerf) and kerf >= 0 else 0.0,
    )


@dataclass(frozen=True)
class JobEstimate:
    """Measured geometry quantities suitable for a pre-production review."""

    paths: int
    segments: int
    cut_length_mm: float
    travel_length_mm: float
    bounds_mm: Bounds | None
    estimated_cut_seconds: float | None

    @property
    def width_mm(self) -> float | None:
        return None if self.bounds_mm is None else self.bounds_mm[2] - self.bounds_mm[0]

    @property
    def height_mm(self) -> float | None:
        return None if self.bounds_mm is None else self.bounds_mm[3] - self.bounds_mm[1]


def _clean_path(poly: list[tuple[float, float]]) -> Polyline:
    result: Polyline = []
    for raw in poly:
        if len(raw) < 2:
            continue
        point = (float(raw[0]), float(raw[1]))
        if not all(math.isfinite(value) for value in point):
            continue
        result.append(point)
    return result


def estimate_job(
    polylines: list[list[tuple[float, float]]], *, feed_rate_mm_s: float | None = None
) -> JobEstimate:
    """Measure path/travel length and bounds without changing the job.

    ``travel_length_mm`` uses the current path order.  It is intentionally not
    presented as a machine-time promise; acceleration, power changes, and
    controller settings are machine-specific.
    """
    clean = [path for poly in polylines if len(path := _clean_path(poly)) >= 2]
    points = [point for path in clean for point in path]
    bounds = (
        (
            min(x for x, _y in points),
            min(y for _x, y in points),
            max(x for x, _y in points),
            max(y for _x, y in points),
        )
        if points
        else None
    )
    cut_length = sum(math.dist(start, end) for path in clean for start, end in zip(path, path[1:]))
    travel_length = sum(
        math.dist(previous[-1], current[0]) for previous, current in zip(clean, clean[1:])
    )
    valid_feed = (
        float(feed_rate_mm_s)
        if feed_rate_mm_s is not None
        and math.isfinite(float(feed_rate_mm_s))
        and feed_rate_mm_s > 0
        else None
    )
    return JobEstimate(
        paths=len(clean),
        segments=sum(len(path) - 1 for path in clean),
        cut_length_mm=cut_length,
        travel_length_mm=travel_length,
        bounds_mm=bounds,
        estimated_cut_seconds=cut_length / valid_feed if valid_feed else None,
    )


def fits_machine_bed(bounds: Bounds | None, profile: MachineProfile) -> bool | None:
    """Return whether lower-left-origin geometry fits a configured rectangular bed.

    ``None`` means no bed is configured, rather than incorrectly calling an
    unconstrained job safe.
    """
    profile.validated()
    if bounds is None:
        return True
    if not profile.has_bed():
        return None
    bed_width = profile.bed_width_mm
    bed_height = profile.bed_height_mm
    assert bed_width is not None and bed_height is not None
    min_x, min_y, max_x, max_y = bounds
    return bool(min_x >= 0 and min_y >= 0 and max_x <= bed_width and max_y <= bed_height)


def apply_kerf_compensation(
    polylines: list[list[tuple[float, float]]], *, kerf_mm: float, mode: KerfMode
) -> list[Polyline]:
    """Return compensated closed contours; open paths are preserved unchanged.

    The compensation is explicit and opt-in.  A positive kerf expands outside
    cuts and contracts inside cuts by half the measured kerf.
    """
    if mode not in {"inside", "outside", "none"}:
        raise ValueError(f"Unsupported kerf mode: {mode!r}")
    if not math.isfinite(kerf_mm) or kerf_mm < 0:
        raise ValueError("Kerf must be zero or a positive number.")
    if mode == "none" or kerf_mm == 0:
        return [_clean_path(poly) for poly in polylines]

    distance = kerf_mm / 2 if mode == "outside" else -kerf_mm / 2
    compensated: list[Polyline] = []
    for index, raw in enumerate(polylines, 1):
        poly = _clean_path(raw)
        closed = len(poly) >= 4 and math.dist(poly[0], poly[-1]) <= 1e-6
        if not closed:
            compensated.append(poly)
            continue
        result = offset_polyline(poly, distance)
        if result is None or len(result) < 4:
            raise ValueError(f"Kerf compensation collapsed closed contour {index}.")
        compensated.append(result)
    return compensated


def order_for_cut(
    polylines: list[list[tuple[float, float]]], *, allow_reverse_open_paths: bool = True
) -> list[Polyline]:
    """Order paths inner-before-outer, then by nearest available start point.

    This is a conservative ordering helper for vector writers.  Closed rings
    are never reversed, preserving their winding; only open paths may reverse
    to reduce travel.
    """
    remaining = [_clean_path(poly) for poly in polylines]
    remaining = [poly for poly in remaining if len(poly) >= 2]

    def containment_depth(poly: Polyline) -> int:
        if len(poly) < 4 or math.dist(poly[0], poly[-1]) > 1e-6:
            return 0
        try:
            candidate = Polygon(poly)
            if not candidate.is_valid or candidate.is_empty:
                return 0
            point = candidate.representative_point()
            return sum(
                1
                for other in remaining
                if other is not poly
                and len(other) >= 4
                and math.dist(other[0], other[-1]) <= 1e-6
                and Polygon(other).is_valid
                and Polygon(other).contains(point)
            )
        except (TypeError, ValueError):
            return 0

    cursor: Point = (0.0, 0.0)
    result: list[Polyline] = []
    while remaining:
        depths = [containment_depth(poly) for poly in remaining]
        deepest = max(depths)
        candidates = [index for index, depth in enumerate(depths) if depth == deepest]
        best_index = candidates[0]
        best_reverse = False
        best_distance = math.inf
        for index in candidates:
            poly = remaining[index]
            closed = len(poly) >= 4 and math.dist(poly[0], poly[-1]) <= 1e-6
            options = [(math.dist(cursor, poly[0]), False)]
            if allow_reverse_open_paths and not closed:
                options.append((math.dist(cursor, poly[-1]), True))
            distance, reverse = min(options)
            if distance < best_distance:
                best_index, best_reverse, best_distance = index, reverse, distance
        chosen = remaining.pop(best_index)
        if best_reverse:
            chosen = list(reversed(chosen))
        result.append(chosen)
        cursor = chosen[-1]
    return result


__all__ = [
    "Bounds",
    "JobEstimate",
    "KerfMode",
    "MachineProfile",
    "Polyline",
    "apply_kerf_compensation",
    "estimate_job",
    "fits_machine_bed",
    "machine_profile_from_settings",
    "order_for_cut",
]
