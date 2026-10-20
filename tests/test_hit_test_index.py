"""Spatially indexed hit-testing answers exactly like the linear scan."""

from __future__ import annotations

import random
from copy import deepcopy
from typing import cast

import pytest
from PySide6.QtWidgets import QApplication

from simple_stipple.canvas.hit_testing import HitTestHost, HitTestService
from simple_stipple.canvas.widget import DxfCanvas
from simple_stipple.core.document.model import EntityRecord


class _LinearHitTest(HitTestService):
    """Reference implementation: every query scans every entity."""

    def _entities_near(self, wx: float, wy: float, radius_px: float) -> list[EntityRecord]:
        return list(self._host._entities)


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


def _random_paths(rng: random.Random, count: int) -> list[list[tuple[float, float]]]:
    paths: list[list[tuple[float, float]]] = []
    for index in range(count):
        x, y = rng.uniform(0, 200), rng.uniform(0, 120)
        if index % 7 == 0:  # axis-aligned lines have zero-area bounds
            paths.append([(x, y), (x + rng.uniform(1, 30), y)])
        elif index % 7 == 1:
            paths.append([(x, y), (x, y + rng.uniform(1, 30))])
        else:
            paths.append(
                [
                    (x + rng.uniform(-15, 15), y + rng.uniform(-15, 15))
                    for _ in range(rng.randint(2, 6))
                ]
            )
    return paths


def test_indexed_queries_match_linear_scan(app: QApplication) -> None:
    rng = random.Random(1234)
    canvas = DxfCanvas()
    canvas.resize(800, 600)
    canvas.add_polylines_state(_random_paths(rng, 300))
    canvas.fit()
    linear = _LinearHitTest(cast(HitTestHost, canvas))
    indexed = canvas._hit_test
    pruned = 0
    hits = 0
    for _ in range(400):
        cx, cy = rng.uniform(0, 800), rng.uniform(0, 600)
        wx, wy = canvas._c2w(cx, cy)
        if len(indexed._entities_near(wx, wy, 8.0)) < len(canvas._entities):
            pruned += 1
        expected = linear.entities_at(cx, cy)
        hits += bool(expected)
        assert indexed.entity_at(cx, cy) == linear.entity_at(cx, cy)
        assert indexed.entities_at(cx, cy) == expected
        assert indexed.nearest_edge(cx, cy) == linear.nearest_edge(cx, cy)
    # The comparison is only meaningful if the index both prunes and hits.
    assert pruned == 400
    assert hits > 20
    canvas.close()


def test_index_follows_committed_edits_and_in_place_previews(app: QApplication) -> None:
    canvas = DxfCanvas()
    canvas.resize(600, 400)
    canvas.add_polylines_state([[(0.0, 0.0), (10.0, 0.0)], [(50.0, 50.0), (60.0, 50.0)]])
    canvas.fit()
    eid = canvas._entities[0].id
    probe = canvas._w2c(5.0, 30.0)
    assert canvas._hit_test.entity_at(*probe) is None  # builds the index

    moved = deepcopy(canvas._entities[0])
    moved.points = [(0.0, 30.0), (10.0, 30.0)]
    assert canvas._canvas_service.update_entities([moved]).changed
    assert canvas._hit_test.entity_at(*probe) == eid

    canvas._entities[0].points = [(0.0, 0.0), (10.0, 0.0)]
    canvas._invalidate_snap_shapes()
    assert canvas._hit_test.entity_at(*probe) is None
    assert canvas._hit_test.entity_at(*canvas._w2c(5.0, 0.0)) == eid
    canvas.close()
