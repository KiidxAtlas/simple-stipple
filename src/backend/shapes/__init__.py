"""Shape system for the curve redesign."""

from src.backend.shapes.factory import ShapeFactory
from src.backend.shapes.shape import (
    ArcShape,
    CircleShape,
    CircleShape as Circle,
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
    "Circle",
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
