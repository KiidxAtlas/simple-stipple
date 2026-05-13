"""_GeomOpsMixin — geometric operations on selected polylines for PolylineView."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from shapely.errors import GEOSException
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.ops import split as shapely_split
from shapely.ops import unary_union

from src.backend.geometry.primitives import (
    build_circle_poly,
    build_ellipse_poly,
    build_rect_poly,
)
from src.backend.geometry.spline import build_spline_poly


class _GeomOpsMixin:
    """Mixin providing geometric transform operations for PolylineView."""

    @staticmethod
    def _rotate_point(
        point: tuple[float, float],
        center: tuple[float, float],
        angle_deg: float,
    ) -> tuple[float, float]:
        if abs(angle_deg) < 1e-9:
            return point
        ang = math.radians(angle_deg)
        ca = math.cos(ang)
        sa = math.sin(ang)
        px, py = point
        cx, cy = center
        dx = px - cx
        dy = py - cy
        return (cx + dx * ca - dy * sa, cy + dx * sa + dy * ca)

    @staticmethod
    def _scale_point(
        point: tuple[float, float],
        center: tuple[float, float],
        factor: float,
    ) -> tuple[float, float]:
        if abs(factor - 1.0) < 1e-9:
            return point
        px, py = point
        cx, cy = center
        return (cx + (px - cx) * factor, cy + (py - cy) * factor)

    @staticmethod
    def _mirror_point(
        point: tuple[float, float],
        center: tuple[float, float],
        axis: str,
    ) -> tuple[float, float]:
        px, py = point
        cx, cy = center
        if axis == "horizontal":
            return (2 * cx - px, py)
        if axis == "vertical":
            return (px, 2 * cy - py)
        return point

    def _rebuild_curved_entity_poly(
        self,
        idx: int,
        kind: str,
        meta: dict[str, Any] | None,
    ) -> None:
        if meta is None or not (0 <= idx < len(self._polys)):
            return
        if kind == "circle":
            center = meta.get("center")
            radius = float(meta.get("radius", 0.0))
            if isinstance(center, tuple) and radius > 0:
                self._polys[idx] = build_circle_poly(
                    float(center[0]),
                    float(center[1]),
                    radius,
                    segments=max(24, len(self._polys[idx]) or 64),
                )
        elif kind == "ellipse":
            center = meta.get("center")
            rx = float(meta.get("rx", 0.0))
            ry = float(meta.get("ry", 0.0))
            if isinstance(center, tuple) and rx > 0 and ry > 0:
                poly = build_ellipse_poly(
                    float(center[0]),
                    float(center[1]),
                    rx,
                    ry,
                    segments=max(24, len(self._polys[idx]) or 64),
                )
                rot = float(meta.get("rotation", 0.0))
                if abs(rot) > 1e-9 and poly:
                    cpt = (float(center[0]), float(center[1]))
                    poly = [self._rotate_point(pt, cpt, rot) for pt in poly]
                self._polys[idx] = poly
        elif kind == "arc":
            center = meta.get("center")
            radius = float(meta.get("radius", 0.0))
            start_angle = float(meta.get("start_angle", 0.0))
            end_angle = float(meta.get("end_angle", 0.0))
            if isinstance(center, tuple) and radius > 0:
                seg_count = int(
                    meta.get("segments", max(12, len(self._polys[idx]) - 1))
                )
                seg_count = max(6, min(seg_count, 256))
                start = math.radians(start_angle)
                end = math.radians(end_angle)
                if end < start:
                    end += 2.0 * math.pi
                step = (end - start) / float(seg_count)
                cx = float(center[0])
                cy = float(center[1])
                self._polys[idx] = [
                    (
                        cx + radius * math.cos(start + i * step),
                        cy + radius * math.sin(start + i * step),
                    )
                    for i in range(seg_count + 1)
                ]
        elif kind == "rectangle":
            center = meta.get("center")
            width = float(meta.get("width", 0.0))
            height = float(meta.get("height", 0.0))
            rotation = float(meta.get("rotation", 0.0))
            if isinstance(center, tuple) and width > 0 and height > 0:
                cx = float(center[0])
                cy = float(center[1])
                poly = build_rect_poly(cx, cy, width, height)
                if abs(rotation) > 1e-9:
                    poly = [self._rotate_point(pt, (cx, cy), rotation) for pt in poly]
                self._polys[idx] = poly
        elif kind == "spline":
            cps = meta.get("control_points")
            if isinstance(cps, list) and len(cps) >= 2:
                segments = int(meta.get("segments", 24))
                closed = bool(meta.get("closed", False))
                poly = build_spline_poly(
                    [tuple(pt) for pt in cps],
                    segments=max(4, segments),
                    closed=closed,
                )
                if len(poly) >= 2:
                    self._polys[idx] = poly

    def _transform_entity_meta(
        self,
        idx: int,
        *,
        center: tuple[float, float],
        kind: str,
        meta: dict[str, Any] | None,
        transform: str,
        factor: float | None = None,
        angle_deg: float = 0.0,
        axis: str | None = None,
        dx: float = 0.0,
        dy: float = 0.0,
    ) -> None:
        if not meta or kind == "polyline":
            return
        updated = deepcopy(meta)

        def _translate_point(pt: tuple[float, float]) -> tuple[float, float]:
            return (pt[0] + dx, pt[1] + dy)

        def _xform_point(pt: tuple[float, float]) -> tuple[float, float]:
            if transform == "translate":
                return _translate_point(pt)
            if transform == "rotate":
                return self._rotate_point(pt, center, angle_deg)
            if transform == "scale":
                if factor is None:
                    return pt
                return self._scale_point(pt, center, factor)
            if transform == "mirror" and axis is not None:
                return self._mirror_point(pt, center, axis)
            return pt

        if kind == "line":
            start = updated.get("start")
            end = updated.get("end")
            if isinstance(start, tuple) and isinstance(end, tuple):
                updated["start"] = _xform_point(tuple(start))
                updated["end"] = _xform_point(tuple(end))
        elif kind == "circle":
            ctr = updated.get("center")
            if isinstance(ctr, tuple):
                updated["center"] = _xform_point(tuple(ctr))
            if transform == "scale" and factor is not None:
                updated["radius"] = float(updated.get("radius", 0.0)) * abs(factor)
        elif kind == "ellipse":
            ctr = updated.get("center")
            if isinstance(ctr, tuple):
                updated["center"] = _xform_point(tuple(ctr))
            if transform == "scale" and factor is not None:
                updated["rx"] = float(updated.get("rx", 0.0)) * abs(factor)
                updated["ry"] = float(updated.get("ry", 0.0)) * abs(factor)
            rot = float(updated.get("rotation", 0.0))
            if transform == "rotate":
                updated["rotation"] = (rot + angle_deg) % 360.0
            elif transform == "mirror" and axis is not None:
                if axis == "horizontal":
                    updated["rotation"] = (180.0 - rot) % 360.0
                elif axis == "vertical":
                    updated["rotation"] = (-rot) % 360.0
        elif kind == "arc":
            ctr = updated.get("center")
            radius = float(updated.get("radius", 0.0))
            start_angle = float(updated.get("start_angle", 0.0))
            end_angle = float(updated.get("end_angle", 0.0))
            if isinstance(ctr, tuple) and radius > 0:
                cpt = tuple(ctr)
                start_pt = (
                    cpt[0] + radius * math.cos(math.radians(start_angle)),
                    cpt[1] + radius * math.sin(math.radians(start_angle)),
                )
                end_pt = (
                    cpt[0] + radius * math.cos(math.radians(end_angle)),
                    cpt[1] + radius * math.sin(math.radians(end_angle)),
                )
                cpt = _xform_point(cpt)
                start_pt = _xform_point(start_pt)
                end_pt = _xform_point(end_pt)
                updated["center"] = cpt
                updated["radius"] = math.hypot(
                    start_pt[0] - cpt[0], start_pt[1] - cpt[1]
                )
                updated["start_angle"] = (
                    math.degrees(math.atan2(start_pt[1] - cpt[1], start_pt[0] - cpt[0]))
                    % 360.0
                )
                updated["end_angle"] = (
                    math.degrees(math.atan2(end_pt[1] - cpt[1], end_pt[0] - cpt[0]))
                    % 360.0
                )
        elif kind == "rectangle":
            ctr = updated.get("center")
            if isinstance(ctr, tuple):
                updated["center"] = _xform_point(tuple(ctr))
            if transform == "scale" and factor is not None:
                updated["width"] = float(updated.get("width", 0.0)) * abs(factor)
                updated["height"] = float(updated.get("height", 0.0)) * abs(factor)
            if transform == "rotate":
                updated["rotation"] = (
                    float(updated.get("rotation", 0.0)) + angle_deg
                ) % 360.0
            elif transform == "mirror" and axis is not None:
                rot = float(updated.get("rotation", 0.0))
                if axis == "horizontal":
                    updated["rotation"] = (180.0 - rot) % 360.0
                elif axis == "vertical":
                    updated["rotation"] = (-rot) % 360.0
        elif kind == "spline":
            cps = updated.get("control_points")
            if isinstance(cps, list):
                updated_cps: list[tuple[float, float]] = []
                for pt in cps:
                    if isinstance(pt, tuple):
                        updated_cps.append(_xform_point(tuple(pt)))
                if len(updated_cps) >= 2:
                    updated["control_points"] = updated_cps

        if idx < len(self._entity_meta):
            self._entity_meta[idx] = updated
        if kind in {"circle", "ellipse", "arc", "rectangle", "spline"}:
            self._rebuild_curved_entity_poly(idx, kind, updated)

    @staticmethod
    def _translated_entity_meta(
        kind: str,
        meta: dict[str, Any] | None,
        dx: float,
        dy: float,
    ) -> dict[str, Any] | None:
        if meta is None or kind == "polyline":
            return None
        updated = deepcopy(meta)
        if kind == "line":
            start = updated.get("start")
            end = updated.get("end")
            if isinstance(start, tuple):
                updated["start"] = (start[0] + dx, start[1] + dy)
            if isinstance(end, tuple):
                updated["end"] = (end[0] + dx, end[1] + dy)
        elif kind in {"circle", "ellipse", "arc", "rectangle"}:
            ctr = updated.get("center")
            if isinstance(ctr, tuple):
                updated["center"] = (ctr[0] + dx, ctr[1] + dy)
        elif kind == "spline":
            cps = updated.get("control_points")
            if isinstance(cps, list):
                updated["control_points"] = [
                    (pt[0] + dx, pt[1] + dy) for pt in cps if isinstance(pt, tuple)
                ]
        return updated

    def rotate_selected(self, angle_deg: float) -> bool:
        indices = self._mutable_selected_indices()
        bounds = self._selection_bounds(indices)
        if not indices or bounds is None:
            return False
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        angle = math.radians(angle_deg)
        ca, sa = math.cos(angle), math.sin(angle)
        self._push_undo()
        for idx in indices:
            self._polys[idx] = [
                (
                    cx + (x - cx) * ca - (y - cy) * sa,
                    cy + (x - cx) * sa + (y - cy) * ca,
                )
                for x, y in self._polys[idx]
            ]
            self._transform_entity_meta(
                idx,
                center=(cx, cy),
                kind=self._entity_kinds[idx]
                if idx < len(self._entity_kinds)
                else "polyline",
                meta=self._entity_meta[idx] if idx < len(self._entity_meta) else None,
                transform="rotate",
                angle_deg=angle_deg,
            )
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def scale_selected(self, factor: float) -> bool:
        indices = self._mutable_selected_indices()
        bounds = self._selection_bounds(indices)
        if not indices or bounds is None or factor <= 0:
            return False
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        self._push_undo()
        for idx in indices:
            self._polys[idx] = [
                (cx + (x - cx) * factor, cy + (y - cy) * factor)
                for x, y in self._polys[idx]
            ]
            self._transform_entity_meta(
                idx,
                center=(cx, cy),
                kind=self._entity_kinds[idx]
                if idx < len(self._entity_kinds)
                else "polyline",
                meta=self._entity_meta[idx] if idx < len(self._entity_meta) else None,
                transform="scale",
                factor=factor,
            )
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def mirror_selected(self, axis: str) -> bool:
        indices = self._mutable_selected_indices()
        bounds = self._selection_bounds(indices)
        if not indices or bounds is None:
            return False
        cx = (bounds[0] + bounds[2]) / 2.0
        cy = (bounds[1] + bounds[3]) / 2.0
        self._push_undo()
        for idx in indices:
            if axis == "horizontal":
                self._polys[idx] = [(2 * cx - x, y) for x, y in self._polys[idx]]
            elif axis == "vertical":
                self._polys[idx] = [(x, 2 * cy - y) for x, y in self._polys[idx]]
            else:
                return False
            self._transform_entity_meta(
                idx,
                center=(cx, cy),
                kind=self._entity_kinds[idx]
                if idx < len(self._entity_kinds)
                else "polyline",
                meta=self._entity_meta[idx] if idx < len(self._entity_meta) else None,
                transform="mirror",
                axis=axis,
            )
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def offset_selected(self, distance: float) -> int:
        indices = self._mutable_selected_indices()
        if not indices or abs(distance) <= 1e-9:
            return 0

        created: list[tuple[list[tuple[float, float]], bool]] = []
        for idx in indices:
            poly = self._polys[idx]
            offset_poly = self._offset_polyline(poly, distance)
            if offset_poly is None or len(offset_poly) < 2:
                continue
            created.append((offset_poly, idx in self._construction_polys))
        if not created:
            return 0

        self._push_undo()
        new_sel: set[int] = set()
        for poly, is_construction in created:
            new_idx = len(self._polys)
            self._polys.append(poly)
            if is_construction:
                self._construction_polys.add(new_idx)
            new_sel.add(new_idx)
        self._sel = new_sel
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return len(created)

    def trim_selected_to_intersections(self) -> int:
        indices = self._mutable_selected_indices()
        if not indices:
            return 0

        cutters: list[LineString] = []
        selected_set = set(indices)
        for idx, poly in enumerate(self._polys):
            if idx in selected_set or len(poly) < 2:
                continue
            try:
                geom = LineString(poly)
            except (TypeError, ValueError, GEOSException):
                continue
            if not geom.is_empty:
                cutters.append(geom)
        if not cutters:
            return 0

        cutter_union = unary_union(cutters)
        changed = 0
        self._push_undo()
        for idx in indices:
            poly = self._polys[idx]
            if len(poly) < 2 or self._is_poly_closed(poly):
                continue
            try:
                target = LineString(poly)
            except (TypeError, ValueError, GEOSException):
                continue
            if target.is_empty or not target.intersects(cutter_union):
                continue
            try:
                pieces = shapely_split(target, cutter_union)
            except (TypeError, ValueError, GEOSException):
                continue
            geoms = [
                g for g in getattr(pieces, "geoms", []) if isinstance(g, LineString)
            ]
            if len(geoms) < 2:
                continue
            longest = max(geoms, key=lambda g: g.length)
            coords = [(float(x), float(y)) for x, y in longest.coords]
            if len(coords) >= 2 and coords != poly:
                self._polys[idx] = coords
                changed += 1

        if not changed:
            self._undo_stack.pop()
            return 0
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return changed

    def extend_selected_to_intersections(self) -> int:
        indices = self._mutable_selected_indices()
        if not indices:
            return 0

        cutters: list[LineString] = []
        selected_set = set(indices)
        for idx, poly in enumerate(self._polys):
            if idx in selected_set or len(poly) < 2:
                continue
            try:
                geom = LineString(poly)
            except (TypeError, ValueError, GEOSException):
                continue
            if not geom.is_empty:
                cutters.append(geom)
        if not cutters:
            return 0

        all_pts = [pt for poly in self._polys for pt in poly]
        if all_pts:
            xs, ys = zip(*all_pts)
            ray_length = max(
                math.hypot(max(xs) - min(xs), max(ys) - min(ys)) * 3.0, 100.0
            )
        else:
            ray_length = 100.0

        changed = 0
        self._push_undo()
        for idx in indices:
            poly = self._polys[idx]
            if len(poly) < 2 or self._is_poly_closed(poly):
                continue
            updated = list(poly)
            start_ext = self._nearest_extension_point(
                poly[0], poly[1], cutters, ray_length, reverse=True
            )
            end_ext = self._nearest_extension_point(
                poly[-1], poly[-2], cutters, ray_length, reverse=False
            )
            if start_ext is not None:
                updated[0] = start_ext
            if end_ext is not None:
                updated[-1] = end_ext
            if updated != poly:
                self._polys[idx] = updated
                changed += 1

        if not changed:
            self._undo_stack.pop()
            return 0
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return changed

    def _nearest_extension_point(
        self,
        anchor: tuple[float, float],
        neighbor: tuple[float, float],
        cutters: list[LineString],
        ray_length: float,
        *,
        reverse: bool,
    ) -> tuple[float, float] | None:
        ax, ay = anchor
        nx, ny = neighbor
        dx, dy = ax - nx, ay - ny
        mag = math.hypot(dx, dy)
        if mag < 1e-9:
            return None
        ux, uy = dx / mag, dy / mag
        ray = LineString([anchor, (ax + ux * ray_length, ay + uy * ray_length)])
        best_t: float | None = None
        best_pt: tuple[float, float] | None = None

        for cutter in cutters:
            try:
                inter = ray.intersection(cutter)
            except (TypeError, ValueError, GEOSException):
                continue
            for px, py in self._iter_intersection_points(inter):
                vx, vy = px - ax, py - ay
                t = vx * ux + vy * uy
                if t <= 1e-6:
                    continue
                if best_t is None or t < best_t:
                    best_t = t
                    best_pt = (float(px), float(py))
        return best_pt

    def _iter_intersection_points(self, geom) -> list[tuple[float, float]]:
        if geom is None or getattr(geom, "is_empty", True):
            return []
        if isinstance(geom, Point):
            return [(float(geom.x), float(geom.y))]
        if isinstance(geom, MultiPoint):
            return [(float(g.x), float(g.y)) for g in geom.geoms]
        if isinstance(geom, LineString):
            coords = list(geom.coords)
            if len(coords) >= 2:
                return [
                    (float(coords[0][0]), float(coords[0][1])),
                    (float(coords[-1][0]), float(coords[-1][1])),
                ]
            return []
        if isinstance(geom, MultiLineString):
            pts: list[tuple[float, float]] = []
            for g in geom.geoms:
                pts.extend(self._iter_intersection_points(g))
            return pts
        if isinstance(geom, GeometryCollection):
            pts: list[tuple[float, float]] = []
            for g in geom.geoms:
                pts.extend(self._iter_intersection_points(g))
            return pts
        return []

    def _offset_polyline(
        self,
        poly: list[tuple[float, float]],
        distance: float,
    ) -> list[tuple[float, float]] | None:
        if len(poly) < 2:
            return None
        try:
            if self._is_poly_closed(poly):
                pts = list(poly)
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                geom = Polygon(pts)
                if not geom.is_valid:
                    geom = geom.buffer(0)
                if geom.is_empty:
                    return None
                # Round joins prevent spikes at sharp corners on closed shapes.
                buffered = geom.buffer(distance, join_style="round")
                if buffered.is_empty:
                    return None
                if isinstance(buffered, MultiPolygon):
                    buffered = max(buffered.geoms, key=lambda g: g.area)
                if not isinstance(buffered, Polygon):
                    return None
                coords = list(buffered.exterior.coords)
                return [(float(x), float(y)) for x, y in coords]

            line = LineString(poly)
            if line.is_empty:
                return None
            side = "left" if distance >= 0 else "right"
            offset_geom = line.parallel_offset(
                abs(distance),
                side,
                join_style="mitre",
                mitre_limit=2.0,  # cap spike length at 2× offset distance
            )
            if offset_geom.is_empty:
                return None
            if isinstance(offset_geom, MultiLineString):
                offset_geom = max(offset_geom.geoms, key=lambda g: g.length)
            if not isinstance(offset_geom, LineString):
                return None
            coords = list(offset_geom.coords)
            return [(float(x), float(y)) for x, y in coords]
        except (TypeError, ValueError, GEOSException):
            return None

    def align_selected(self, mode: str) -> bool:
        indices = self._mutable_selected_indices()
        bounds = self._selection_bounds(indices)
        if len(indices) < 2 or bounds is None:
            return False
        bx0, by0, bx1, by1 = bounds
        center_x = (bx0 + bx1) / 2.0
        center_y = (by0 + by1) / 2.0
        self._push_undo()
        for idx in indices:
            px0, py0, px1, py1 = self._poly_bounds(self._polys[idx])
            dx = dy = 0.0
            if mode == "left":
                dx = bx0 - px0
            elif mode == "center-x":
                dx = center_x - (px0 + px1) / 2.0
            elif mode == "right":
                dx = bx1 - px1
            elif mode == "top":
                dy = by1 - py1
            elif mode == "center-y":
                dy = center_y - (py0 + py1) / 2.0
            elif mode == "bottom":
                dy = by0 - py0
            else:
                return False
            self._polys[idx] = [(x + dx, y + dy) for x, y in self._polys[idx]]
            self._transform_entity_meta(
                idx,
                center=(center_x, center_y),
                kind=self._entity_kinds[idx]
                if idx < len(self._entity_kinds)
                else "polyline",
                meta=self._entity_meta[idx] if idx < len(self._entity_meta) else None,
                transform="translate",
                dx=dx,
                dy=dy,
            )
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _set_selected_width(self, width: float) -> bool:
        indices = self._mutable_selected_indices()
        bounds = self._selection_bounds(indices)
        if not indices or bounds is None or width <= 0:
            return False
        cur_w = bounds[2] - bounds[0]
        if cur_w <= 1e-9:
            return False
        fx = width / cur_w
        cx = (bounds[0] + bounds[2]) / 2.0
        self._demote_selected_entities_to_polylines(indices)
        self._push_undo()
        for idx in indices:
            self._polys[idx] = [(cx + (x - cx) * fx, y) for x, y in self._polys[idx]]
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _set_selected_height(self, height: float) -> bool:
        indices = self._mutable_selected_indices()
        bounds = self._selection_bounds(indices)
        if not indices or bounds is None or height <= 0:
            return False
        cur_h = bounds[3] - bounds[1]
        if cur_h <= 1e-9:
            return False
        fy = height / cur_h
        cy = (bounds[1] + bounds[3]) / 2.0
        self._demote_selected_entities_to_polylines(indices)
        self._push_undo()
        for idx in indices:
            self._polys[idx] = [(x, cy + (y - cy) * fy) for x, y in self._polys[idx]]
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _set_selected_line_length(self, length: float) -> bool:
        indices = self._mutable_selected_indices()
        if len(indices) != 1 or length <= 0:
            return False
        poly = self._polys[indices[0]]
        if len(poly) != 2:
            return False
        ax, ay = poly[0]
        bx, by = poly[1]
        dx, dy = bx - ax, by - ay
        cur_len = math.hypot(dx, dy)
        if cur_len <= 1e-9:
            return False
        ux, uy = dx / cur_len, dy / cur_len
        self._push_undo()
        self._polys[indices[0]][1] = (ax + ux * length, ay + uy * length)
        self._transform_entity_meta(
            indices[0],
            center=(ax, ay),
            kind=self._entity_kinds[indices[0]]
            if indices[0] < len(self._entity_kinds)
            else "polyline",
            meta=self._entity_meta[indices[0]]
            if indices[0] < len(self._entity_meta)
            else None,
            transform="translate",
            dx=(ax + ux * length) - bx,
            dy=(ay + uy * length) - by,
        )
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _distribute_selected(self, axis: str, spacing: float) -> bool:
        indices = self._mutable_selected_indices()
        if len(indices) < 2 or spacing < 0:
            return False

        keyed: list[tuple[int, tuple[float, float, float, float]]] = []
        for idx in indices:
            b = self._poly_bounds(self._polys[idx])
            keyed.append((idx, b))

        if axis == "horizontal":
            keyed.sort(key=lambda x: x[1][0])
            cur_edge = keyed[0][1][2]
            self._push_undo()
            for idx, b in keyed[1:]:
                target_min = cur_edge + spacing
                dx = target_min - b[0]
                self._polys[idx] = [(x + dx, y) for x, y in self._polys[idx]]
                self._transform_entity_meta(
                    idx,
                    center=(0.0, 0.0),
                    kind=self._entity_kinds[idx]
                    if idx < len(self._entity_kinds)
                    else "polyline",
                    meta=self._entity_meta[idx]
                    if idx < len(self._entity_meta)
                    else None,
                    transform="translate",
                    dx=dx,
                    dy=0.0,
                )
                nb = self._poly_bounds(self._polys[idx])
                cur_edge = nb[2]
        elif axis == "vertical":
            keyed.sort(key=lambda x: x[1][1])
            cur_edge = keyed[0][1][3]
            self._push_undo()
            for idx, b in keyed[1:]:
                target_min = cur_edge + spacing
                dy = target_min - b[1]
                self._polys[idx] = [(x, y + dy) for x, y in self._polys[idx]]
                self._transform_entity_meta(
                    idx,
                    center=(0.0, 0.0),
                    kind=self._entity_kinds[idx]
                    if idx < len(self._entity_kinds)
                    else "polyline",
                    meta=self._entity_meta[idx]
                    if idx < len(self._entity_meta)
                    else None,
                    transform="translate",
                    dx=0.0,
                    dy=dy,
                )
                nb = self._poly_bounds(self._polys[idx])
                cur_edge = nb[3]
        else:
            return False

        self._redraw()
        self._notify()
        self._fire_poly_change()
        return True

    def _prompt_edit_dimensions(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        indices = self._mutable_selected_indices()
        bounds = self._selection_bounds(indices)
        if not indices or bounds is None:
            self._show_flash("Select shapes/lines first", 1200)
            return

        cur_w = max(bounds[2] - bounds[0], 0.0)
        cur_h = max(bounds[3] - bounds[1], 0.0)

        new_w, ok_w = QInputDialog.getDouble(
            self,
            "Set Width",
            "Width (mm):",
            cur_w,
            0.001,
            1_000_000.0,
            3,
        )
        if not ok_w:
            return

        new_h, ok_h = QInputDialog.getDouble(
            self,
            "Set Height",
            "Height (mm):",
            cur_h,
            0.001,
            1_000_000.0,
            3,
        )
        if not ok_h:
            return

        changed_w = abs(new_w - cur_w) > 1e-9
        changed_h = abs(new_h - cur_h) > 1e-9
        if not changed_w and not changed_h:
            return

        if changed_w:
            self._set_selected_width(new_w)
        if changed_h:
            self._set_selected_height(new_h)
        self._show_flash("Dimensions updated", 900)

    # ── Explode / Merge ───────────────────────────────────────────────────────

    def explode_selected_to_segments(self) -> int:
        """Split each selected multi-vertex polyline into individual 2-pt line segments.

        Returns the number of new segments created.
        """
        indices = self._mutable_selected_indices()
        if not indices:
            return 0
        # Only explode polylines with more than 2 points (1 segment is already atomic).
        to_explode = [i for i in indices if len(self._polys[i]) > 2]
        if not to_explode:
            return 0
        self._push_undo()
        new_polys: list[list[tuple[float, float]]] = []
        new_construction: set[int] = set()
        new_kinds: list[str] = []
        new_meta: list[dict[str, Any] | None] = []
        new_sel: set[int] = set()
        for i, poly in enumerate(self._polys):
            is_construction = i in self._construction_polys
            kind = self._entity_kinds[i] if i < len(self._entity_kinds) else "polyline"
            meta = self._entity_meta[i] if i < len(self._entity_meta) else None
            if i in to_explode:
                pts = list(poly)
                is_closed = False
                # If closed (first == last) drop the duplicate closing point first
                if (
                    len(pts) >= 3
                    and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1])
                    < 1e-6
                ):
                    pts = pts[:-1]
                    is_closed = True
                seg_count = len(pts) if is_closed else max(0, len(pts) - 1)
                for j in range(seg_count):
                    seg = [pts[j], pts[(j + 1) % len(pts)]]
                    si = len(new_polys)
                    new_polys.append(seg)
                    new_kinds.append("line")
                    new_meta.append({"start": seg[0], "end": seg[1]})
                    if is_construction:
                        new_construction.add(si)
                    new_sel.add(si)
            else:
                ni = len(new_polys)
                new_polys.append(poly)
                new_kinds.append(kind)
                new_meta.append(deepcopy(meta) if meta is not None else None)
                if is_construction:
                    new_construction.add(ni)
        self._polys = new_polys
        self._entity_kinds = new_kinds
        self._entity_meta = new_meta
        self._construction_polys = new_construction
        self._sel = new_sel
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return len(new_sel)

    def merge_selected_segments_to_objects(self) -> int:
        """Merge selected geometry by segment connectivity.

        Accepts a mix of atomic 2-point segments and multi-vertex polylines.
        Selected polylines are decomposed into their constituent segments first,
        then rebuilt into one or more connected objects.
        """
        indices = self._mutable_selected_indices()
        if len(indices) < 2:
            return 0

        def _eq(a: tuple[float, float], b: tuple[float, float]) -> bool:
            return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6

        segs: list[tuple[tuple[float, float], tuple[float, float], bool]] = []
        for i in indices:
            poly = self._polys[i]
            if len(poly) < 2:
                continue
            is_construction = i in self._construction_polys
            pts = list(poly)
            is_closed = False
            if (
                len(pts) >= 3
                and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-6
            ):
                pts = pts[:-1]
                is_closed = True
            if len(pts) < 2:
                continue
            seg_count = len(pts) if is_closed else max(0, len(pts) - 1)
            for j in range(seg_count):
                a = pts[j]
                b = pts[(j + 1) % len(pts)]
                if _eq(a, b):
                    continue
                segs.append((a, b, is_construction))

        if len(segs) < 2:
            return 0

        sel_set = set(indices)

        self._push_undo()

        merged_polys: list[tuple[list[tuple[float, float]], bool]] = []
        used = [False] * len(segs)

        for si, seg in enumerate(segs):
            if used[si]:
                continue
            used[si] = True
            chain = [seg[0], seg[1]]
            chain_construction = seg[2]

            changed = True
            while changed:
                changed = False
                for j, s in enumerate(segs):
                    if used[j]:
                        continue
                    a, b = s[0], s[1]
                    head, tail = chain[0], chain[-1]
                    if _eq(tail, a):
                        chain.append(b)
                    elif _eq(tail, b):
                        chain.append(a)
                    elif _eq(head, b):
                        chain.insert(0, a)
                    elif _eq(head, a):
                        chain.insert(0, b)
                    else:
                        continue
                    used[j] = True
                    chain_construction = chain_construction or s[2]
                    changed = True
                    break

            if len(chain) >= 3 and _eq(chain[0], chain[-1]):
                # normalize explicit closure point
                chain[-1] = chain[0]
            merged_polys.append((
                self._normalize_merged_chain(chain),
                chain_construction,
            ))

        kept_polys: list[list[tuple[float, float]]] = []
        kept_construction: set[int] = set()
        for i, poly in enumerate(self._polys):
            if i in sel_set:
                continue
            ni = len(kept_polys)
            kept_polys.append(poly)
            if i in self._construction_polys:
                kept_construction.add(ni)

        new_sel: set[int] = set()
        for poly, is_construction in merged_polys:
            ni = len(kept_polys)
            kept_polys.append(poly)
            new_sel.add(ni)
            if is_construction:
                kept_construction.add(ni)

        self._polys = kept_polys
        self._construction_polys = kept_construction
        self._sel = new_sel
        self._redraw()
        self._notify()
        self._fire_poly_change()
        return len(new_sel)

    @staticmethod
    def _normalize_merged_chain(
        chain: list[tuple[float, float]],
    ) -> list[tuple[float, float]]:
        if not chain:
            return []
        normalized: list[tuple[float, float]] = [chain[0]]
        for pt in chain[1:]:
            if math.hypot(normalized[-1][0] - pt[0], normalized[-1][1] - pt[1]) >= 1e-6:
                normalized.append(pt)
        if (
            len(normalized) >= 3
            and math.hypot(
                normalized[0][0] - normalized[-1][0],
                normalized[0][1] - normalized[-1][1],
            )
            < 1e-6
        ):
            normalized[-1] = normalized[0]
        return normalized
