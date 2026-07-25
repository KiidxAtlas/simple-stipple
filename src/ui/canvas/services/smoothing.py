"""Canvas state adapter for backend smoothing, simplification, and curve fitting."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol

from src.backend.cad.geometry import fit_polyline_to_bezier
from src.backend.editing.smoothing import simplify, smooth


class SignalEmitter(Protocol):
    def emit(self, *args: object) -> None: ...


class SmoothingHost(Protocol):
    _canvas_service: Any
    _entities: list
    _entities_by_id: dict[str, Any]
    _smoothing_method: str
    _smooth_iterations: int
    _simplify_tolerance: float
    _sel: set[str]
    selectionChanged: SignalEmitter
    smoothingMethodChanged: SignalEmitter
    smoothIterationsChanged: SignalEmitter
    simplifyToleranceChanged: SignalEmitter

    def _mutable_selected_ids(self) -> list[str]: ...
    def _is_poly_closed(self, points: list[tuple[float, float]]) -> bool: ...
    def _redraw(self) -> None: ...
    def _notify(self) -> None: ...
    def _fire_poly_change(self) -> None: ...
    def _refresh_draw_sidebar_state(self) -> None: ...


class SmoothingService:
    """Mutates canvas state only around pure backend path operations."""

    def __init__(self, host: SmoothingHost) -> None:
        self._host = host

    def set_method(self, method: str) -> None:
        if method not in ("chaikin", "gaussian", "catmull_rom"):
            return
        host = self._host
        host._smoothing_method = method
        host.selectionChanged.emit(len(host._sel))
        host._refresh_draw_sidebar_state()

    def method_changed(self, method: str) -> None:
        self.set_method(method)
        self._host.smoothingMethodChanged.emit(method)

    def set_iterations(self, iterations: int) -> None:
        self._host._smooth_iterations = int(iterations)

    def iterations_changed(self, iterations: int) -> None:
        self.set_iterations(iterations)
        self._host.smoothIterationsChanged.emit(self._host._smooth_iterations)

    def set_tolerance(self, tolerance: float) -> None:
        self._host._simplify_tolerance = float(tolerance)

    def tolerance_changed(self, tolerance: float) -> None:
        self.set_tolerance(tolerance)
        self._host.simplifyToleranceChanged.emit(self._host._simplify_tolerance)

    def smooth_selected(self, iterations: int = 2) -> int:
        host = self._host
        indices = [
            index
            for index in host._mutable_selected_ids()
            if len(host._entities_by_id[index].points) >= 3
        ]
        if not indices:
            return 0
        candidates = []
        for index in indices:
            entity = deepcopy(host._entities_by_id[index])
            entity.points = smooth(
                entity.points,
                method=host._smoothing_method,
                iterations=iterations,
                closed=host._is_poly_closed(entity.points),
            )
            entity.kind, entity.meta = "polyline", None
            candidates.append(entity)
        host._canvas_service.update_entities(candidates)
        self._changed()
        return len(indices)

    def simplify_selected(self, tolerance: float = 0.2) -> int:
        host = self._host
        indices = [
            index
            for index in host._mutable_selected_ids()
            if len(host._entities_by_id[index].points) >= 3
        ]
        candidates = []
        for index in indices:
            entity = host._entities_by_id[index]
            points = simplify(entity.points, tolerance, closed=host._is_poly_closed(entity.points))
            if 2 <= len(points) < len(entity.points):
                candidate = deepcopy(entity)
                candidate.points, candidate.kind, candidate.meta = points, "polyline", None
                candidates.append(candidate)
        if not candidates:
            return 0
        host._canvas_service.update_entities(candidates)
        self._changed()
        return len(candidates)

    def fit_selected_to_curve(self, tolerance: float = 0.3, corner_angle_deg: float = 55.0) -> int:
        host = self._host
        fitted = []
        for index in host._mutable_selected_ids():
            entity = host._entities_by_id[index]
            if len(entity.points) < 3:
                continue
            result = fit_polyline_to_bezier(
                entity.points,
                tolerance=tolerance,
                corner_angle_deg=corner_angle_deg,
                closed=host._is_poly_closed(entity.points),
            )
            if result is not None:
                fitted.append((index, result))
        if not fitted:
            return 0
        candidates = []
        for index, (anchors, tangents) in fitted:
            entity = deepcopy(host._entities_by_id[index])
            entity.points = anchors
            entity.kind = "bezier"
            entity.meta = {"control_points": tangents}
            candidates.append(entity)
        host._canvas_service.update_entities(candidates)
        self._changed()
        return len(fitted)

    def _changed(self) -> None:
        self._host._redraw()
        self._host._notify()
        self._host._fire_poly_change()
