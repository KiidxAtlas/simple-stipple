"""The closed set of entity kinds a document can hold.

``EntityRecord.kind`` used to be a free string compared against literal sets
scattered through the canvas. :class:`EntityKind` is the one list: it is a
``str`` subclass, so existing comparisons, dict keys and JSON output keep
the same plain-string values (the workspace format is unchanged), while a
record can no longer hold a kind nothing else knows how to draw.
"""

from __future__ import annotations

from enum import Enum


class EntityKind(str, Enum):
    POLYLINE = "polyline"
    LINE = "line"
    ARC = "arc"
    ELLIPTICAL_ARC = "elliptical_arc"
    CIRCLE = "circle"
    ELLIPSE = "ellipse"
    RECTANGLE = "rectangle"
    ROUNDED_RECTANGLE = "rounded_rectangle"
    POLYGON = "polygon"
    STAR = "star"
    SPLINE = "spline"
    BEZIER = "bezier"
    SLOT = "slot"
    # Procedural quick shapes: stored as their generated outline (drawn as a
    # path) with ``meta["generator"]`` naming the recipe.
    RING = "ring"
    GEAR = "gear"
    SPIRAL = "spiral"
    TEARDROP = "teardrop"
    KEYHOLE = "keyhole"
    SUPERELLIPSE = "superellipse"
    ROUNDED_STAR = "rounded_star"
    CHAMFERED_STAR = "chamfered_star"
    FINGER_JOINT_BOX = "finger_joint_box"
    DOVETAIL_BOX = "dovetail_box"
    TABBED_PANEL = "tabbed_panel"

    # Behave exactly like the plain string everywhere (Python 3.10 has no
    # StrEnum, and Enum's own hash/str would break ``kind in {"circle"}``).
    __hash__ = str.__hash__

    def __str__(self) -> str:
        return str.__str__(self)

    def __format__(self, format_spec: str) -> str:
        return str.__format__(self, format_spec)

    @classmethod
    def coerce(cls, value: object) -> EntityKind:
        """Map a stored kind to its member; anything unknown is a plain path.

        Unknown kinds were already drawn as polylines (see
        ``geometry_for_entity``); naming that here keeps the record honest.
        """
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value))
        except ValueError:
            return cls.POLYLINE


#: Procedural quick-shape recipes; their records are drawn as plain paths.
PROCEDURAL_KINDS: frozenset[EntityKind] = frozenset(
    {
        EntityKind.RING,
        EntityKind.GEAR,
        EntityKind.SPIRAL,
        EntityKind.TEARDROP,
        EntityKind.KEYHOLE,
        EntityKind.SUPERELLIPSE,
        EntityKind.ROUNDED_STAR,
        EntityKind.CHAMFERED_STAR,
        EntityKind.FINGER_JOINT_BOX,
        EntityKind.DOVETAIL_BOX,
        EntityKind.TABBED_PANEL,
    }
)

#: Kinds whose ``meta`` describes the shape and is rebuilt into a Shape.
PARAMETRIC_KINDS: frozenset[EntityKind] = (
    frozenset(EntityKind) - PROCEDURAL_KINDS - {EntityKind.POLYLINE}
)

#: Kinds whose stored points are a tessellation, not editable handles.
HANDLELESS_KINDS: frozenset[EntityKind] = frozenset(
    {
        EntityKind.ARC,
        EntityKind.ELLIPTICAL_ARC,
        EntityKind.CIRCLE,
        EntityKind.ELLIPSE,
        EntityKind.SLOT,
        EntityKind.ROUNDED_RECTANGLE,
        EntityKind.STAR,
    }
)

#: Kinds whose stored points are the control points themselves.
CONTROL_POINT_KINDS: frozenset[EntityKind] = frozenset(
    {EntityKind.POLYLINE, EntityKind.LINE, EntityKind.SPLINE, EntityKind.BEZIER}
)

__all__ = [
    "CONTROL_POINT_KINDS",
    "HANDLELESS_KINDS",
    "PARAMETRIC_KINDS",
    "PROCEDURAL_KINDS",
    "EntityKind",
]
