"""Shape system for the curve redesign.

Provides polymorphic shape objects to replace the old polyline + metadata system.
"""

from src.backend.shapes.factory import ShapeFactory
from src.backend.shapes.shape import (
    ArcShape,
    CircleShape,
    EllipseShape,
    LineShape,
    Point,
    PolylineShape,
    RectangleShape,
    Shape,
    SplineShape,
)

__all__ = [
    "ArcShape",
    "CircleShape",
    "EllipseShape",
    "LineShape",
    "Point",
    "PolylineShape",
    "RectangleShape",
    "Shape",
    "ShapeFactory",
    "SplineShape",
]
