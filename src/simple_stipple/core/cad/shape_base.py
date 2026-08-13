"""Shape abstraction and the point helpers every concrete shape shares."""

from __future__ import annotations
import math
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal


Point = tuple[float, float]


def _finite(*vals: object) -> bool:
    try:
        return all(math.isfinite(float(v)) for v in vals)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _rotate_pt(p: Point, center: Point, angle_deg: float) -> Point:
    if abs(angle_deg) < 1e-9:
        return p
    ang = math.radians(angle_deg)
    ca, sa = math.cos(ang), math.sin(ang)
    dx, dy = p[0] - center[0], p[1] - center[1]
    return (center[0] + dx * ca - dy * sa, center[1] + dx * sa + dy * ca)


def _scale_pt(p: Point, center: Point, factor: float) -> Point:
    if abs(factor - 1.0) < 1e-9:
        return p
    return (
        center[0] + (p[0] - center[0]) * factor,
        center[1] + (p[1] - center[1]) * factor,
    )


def _anisotropic_scale_fn(center: Point, factor_x: float, factor_y: float):
    return lambda p: (
        center[0] + (p[0] - center[0]) * factor_x,
        center[1] + (p[1] - center[1]) * factor_y,
    )


def _mirror_pt(p: Point, center: Point, axis: str) -> Point:
    if axis == "horizontal":
        return (2 * center[0] - p[0], p[1])
    if axis == "vertical":
        return (p[0], 2 * center[1] - p[1])
    return p


@dataclass
class Shape(ABC):
    """Base class for all shape types.

    Shapes are the primary storage unit, replacing the old polyline + metadata system.
    Each shape manages:
    - Its own geometric state (points, parameters, etc.)
    - Metadata (visibility, lock status, layer, etc.)
    - Tessellation cache (lazy-computed for rendering)
    - DXF export capability
    """

    id: int
    shape_type: Literal[
        "polyline",
        "line",
        "arc",
        "elliptical_arc",
        "circle",
        "ellipse",
        "spline",
        "bezier",
        "rectangle",
        "polygon",
        "slot",
        "rounded_rectangle",
        "star",
    ]
    name: str = "Shape"
    visible: bool = True
    locked: bool = False
    layer: str = "default"
    construction: bool = False

    # Tessellation cache
    _tessellation_cache: list[Point] | None = field(default=None, init=False, repr=False)
    _cache_dirty: bool = field(default=True, init=False, repr=False)

    @property
    @abstractmethod
    def control_points(self) -> list[Point]:
        """Return the primary control points for this shape."""
        ...

    @property
    def points(self) -> list[Point]:
        """Get tessellated points for rendering.

        Returns cached tessellation if available, otherwise computes and caches.
        """
        if self._cache_dirty or self._tessellation_cache is None:
            self._tessellation_cache = self._compute_tessellation()
            self._cache_dirty = False
        return self._tessellation_cache

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """Get bounding box as (x0, y0, x1, y1)."""
        if not self.control_points:
            return (0, 0, 0, 0)
        xs = [pt[0] for pt in self.control_points]
        ys = [pt[1] for pt in self.control_points]
        return (min(xs), min(ys), max(xs), max(ys))

    def invalidate_cache(self) -> None:
        """Mark tessellation cache as dirty."""
        self._cache_dirty = True

    @abstractmethod
    def _compute_tessellation(self) -> list[Point]:
        """Compute tessellated point sequence. Implemented by subclasses."""
        ...

    # ── Geometric operations ─────────────────────────────────────────────
    # Each shape transforms its own parametric fields; there is exactly one
    # implementation per shape kind (previously this logic was duplicated as
    # kind-string switches in view.py, dxf/io.py, and factory.py).

    def _map_points(self, fn) -> None:
        """Apply a point mapping to this shape's defining geometry.

        Default covers point-list shapes; parametric subclasses override.
        """
        raise NotImplementedError

    def translate(self, dx: float, dy: float) -> None:
        self._map_points(lambda p: (p[0] + dx, p[1] + dy))

    def rotate(self, center: Point, angle_deg: float) -> None:
        self._map_points(lambda p: _rotate_pt(p, center, angle_deg))

    def scale(self, center: Point, factor: float) -> None:
        self._map_points(lambda p: _scale_pt(p, center, factor))

    def scale_xy(self, center: Point, factor_x: float, factor_y: float) -> Shape | None:
        """Scale independently along each axis. Returns the resulting shape,
        or ``None`` when this shape kind has no way to represent a
        non-uniform scale at all.

        The result may be ``self`` mutated in place (line, polyline,
        spline, bezier — shapes whose geometry is a linear function of
        their control points, so any affine map, uniform or not, keeps
        them the same kind), or a *different* shape (``ArcShape`` returns
        a fresh ``EllipticalArcShape`` — a circular arc scaled unevenly
        traces an ellipse, a family its own center/radius/angles schema
        can't represent). Everything else keeps this default — return
        None so the caller falls back to flattening the geometry instead
        of trusting cached parameters (radius, width/height, ...) a
        non-uniform scale would have made incorrect.
        """
        return None

    def mirror(self, center: Point, axis: str) -> None:
        self._map_points(lambda p: _mirror_pt(p, center, axis))

    def set_parameter(self, key: str, value: float) -> bool:
        """Update a user-editable defining parameter when supported."""
        return False

    def move_control_point(self, index: int, point: Point) -> bool:
        """Move one editable control point when this shape exposes it."""
        return False

    # ── Interop ──────────────────────────────────────────────────────────

    def to_meta_dict(self) -> tuple[str, dict | None]:
        """Return ``(kind, meta)`` in the polyline+metadata dict encoding used
        by ``EntityRecord`` (the canvas's live entity representation)."""
        return (self.shape_type, None)

    def to_dxf(self, msp, dxfattribs: dict | None = None) -> bool:
        """Emit this shape as a native DXF entity onto ``msp``.

        Returns ``False`` when the shape has no native DXF form or its
        parameters are degenerate (non-finite, zero radius, …); callers then
        fall back to writing the tessellated polyline, so no shape is ever
        dropped or written corrupt.
        """
        return False

    def copy(self, new_id: int) -> Shape:
        """Create a deep copy with a new ID."""
        copy = deepcopy(self)
        copy.id = new_id
        return copy


class _CenterBasedShapeMixin:
    """Mixin for shapes defined by a single center point + parameters.

    Provides shared ``_map_points`` for center-based shapes (Circle, Polygon,
    Ellipse, Star, Rectangle, Slot) so the geometric mapping logic lives in
    one place.
    """

    def _map_points(self, fn) -> None:
        self.center = fn(self.center)  # type: ignore[attr-defined]
        self.invalidate_cache()  # type: ignore[attr-defined]


class _ParametricRotateMixin:
    """Mixin for shapes that track a ``rotation`` property on rotate.

    Parametric center-based shapes (Polygon, Ellipse, Star, Rectangle, Slot)
    rotate their center point and accumulate the rotation angle — a pattern
    shared across these classes.
    """

    def rotate(self, center: Point, angle_deg: float) -> None:
        self.center = _rotate_pt(self.center, center, angle_deg)  # type: ignore[attr-defined]
        self.rotation = (self.rotation + angle_deg) % 360.0  # type: ignore[attr-defined]
        self.invalidate_cache()  # type: ignore[attr-defined]
