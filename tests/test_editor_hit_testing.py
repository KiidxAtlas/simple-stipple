"""Characterization for the Phase-3 editor hit-testing extraction."""

from __future__ import annotations

from dataclasses import dataclass

from simple_stipple.canvas.hit_testing import HitTestService
from simple_stipple.canvas.objects import (
    CanvasModel,
    CanvasService,
)


@dataclass
class _Entity:
    id: str
    points: list[tuple[float, float]]
    hidden: bool = False


class _Host:
    def __init__(self) -> None:
        entity = _Entity("square", [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)])
        self._entities = [entity]
        self._entities_by_id = {entity.id: entity}
        self._scale = 1.0
        self._guides: list[tuple[str, float]] = []
        self._active_layer = None
        self._ghost_visible = False
        self._ghost_polys: list[list[tuple[float, float]]] = []

    @staticmethod
    def _w2c(x: float, y: float) -> tuple[float, float]:
        return x, y

    @staticmethod
    def _c2w(x: float, y: float) -> tuple[float, float]:
        return x, y

    @staticmethod
    def _entity_selectable(_entity_id: str) -> bool:
        return True

    _entity_selectable_by_id = _entity_selectable

    def _flattened_points_by_id(self, entity_id: str) -> list[tuple[float, float]]:
        return self._entities_by_id[entity_id].points

    @staticmethod
    def _on_active_layer(_entity: _Entity) -> bool:
        return False


def test_editor_document_bridge_paths_share_one_model() -> None:
    model = CanvasModel()
    service = CanvasService(model)
    assert service.documents.document is model.document


def test_hit_testing_preserves_segment_and_entity_queries() -> None:
    service = HitTestService(_Host())
    assert HitTestService.segment_intersection((0.0, 0.0), (2.0, 2.0), (0.0, 2.0), (2.0, 0.0)) == (
        1.0,
        1.0,
    )
    assert HitTestService.segment_count([(0.0, 0.0), (1.0, 0.0)]) == 1
    assert service.nearest_vertex(0.1, 0.1) == ("square", 0)
    assert service.nearest_edge(5.0, 0.2) == ("square", 0, (5.0, 0.0))
    assert service.entity_at(5.0, 0.2) == "square"
