"""Geometry and drag-state helpers for canvas quick shapes."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt

from simple_stipple.engine.cad.geometry import shape_circle, shape_polygon, shape_rect, shape_slot


def mode_from_modifiers(host: Any, modifiers) -> str:
    if modifiers & Qt.KeyboardModifier.AltModifier:
        return "circle"
    if modifiers & Qt.KeyboardModifier.ControlModifier:
        return "slot"
    return host._quick_shape_mode


def start_drag(host: Any, mode: str, position) -> None:
    pos = position.toPoint()
    wx, wy = host._c2w(position.x(), position.y())
    host._shape_drag_active = True
    host._shape_drag_mode = mode
    host._shape_start_w = (wx, wy)
    host._shape_start_c = pos
    host._shape_end_c = pos


def translate(
    coords: list[tuple[float, float]], cx: float, cy: float
) -> list[tuple[float, float]]:
    return [(x + cx, y + cy) for x, y in coords]


def build_shapes(
    host: Any, mode: str, sx: float, sy: float, ex: float, ey: float
) -> list[list[tuple[float, float]]]:
    width = abs(ex - sx)
    height = abs(ey - sy)
    if width < 1e-6 or height < 1e-6:
        return []
    cx = (sx + ex) / 2.0
    cy = (sy + ey) / 2.0
    if mode == "rectangle":
        return [translate(shape_rect(width, height), cx, cy)]
    if mode == "circle":
        return [translate(shape_circle(min(width, height) / 2.0, 64), cx, cy)]
    if mode == "slot":
        return [translate(shape_slot(max(width, height), min(width, height)), cx, cy)]
    if mode == "hexagon":
        return [translate(shape_polygon(6, min(width, height) / 2.0), cx, cy)]
    if mode in host._PROCEDURAL_QUICK_SHAPES:
        return build_procedural_shapes(mode, width, height, cx, cy)
    return []


def build_first_shape(
    host: Any, mode: str, sx: float, sy: float, ex: float, ey: float
) -> list[tuple[float, float]]:
    shapes = build_shapes(host, mode, sx, sy, ex, ey)
    return shapes[0] if shapes else []


def build_procedural_shapes(
    mode: str, width: float, height: float, cx: float, cy: float
) -> list[list[tuple[float, float]]]:
    """Scale a procedural primitive into the drag bounds, preserving holes."""
    from simple_stipple.engine.cad.primitives import (
        chamfered_star,
        dovetail_box,
        finger_joint_box,
        gear,
        keyhole,
        ring,
        rounded_star,
        spiral,
        superellipse,
        tabbed_panel,
        teardrop,
    )

    generators = {
        "gear": lambda: [gear()],
        "spiral": lambda: [spiral()],
        "superellipse": lambda: [superellipse()],
        "teardrop": lambda: [teardrop()],
        "keyhole": lambda: [keyhole()],
        "ring": lambda: list(ring()),
        "rounded_star": lambda: [rounded_star()],
        "chamfered_star": lambda: [chamfered_star()],
        "finger_joint_box": lambda: [finger_joint_box()],
        "dovetail_box": lambda: [dovetail_box()],
        "tabbed_panel": lambda: [tabbed_panel()],
    }
    paths = generators[mode]()
    points = [point for path in paths for point in path]
    min_x, max_x = min(point[0] for point in points), max(point[0] for point in points)
    min_y, max_y = min(point[1] for point in points), max(point[1] for point in points)
    scale_x = width / max(max_x - min_x, 1e-9)
    scale_y = height / max(max_y - min_y, 1e-9)
    source_cx = (min_x + max_x) / 2.0
    source_cy = (min_y + max_y) / 2.0
    return [
        [
            ((point[0] - source_cx) * scale_x + cx, (point[1] - source_cy) * scale_y + cy)
            for point in path
        ]
        for path in paths
    ]
