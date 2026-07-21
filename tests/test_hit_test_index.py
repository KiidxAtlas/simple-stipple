"""The spatial vertex index must be an exact optimization of the linear scan.

``nearest_vertex`` was changed from an O(vertices) scan on every mouse move to a
KD-tree query. These tests pin that the fast path returns *identical* results to
the linear reference across zoom levels, selection filtering, and edits (cache
invalidation), so the optimization can never silently change hit behavior.
"""

import math
import random

from src.backend.model.document import EntityRecord
from src.ui.canvas.services.hit_test import HitTestService


class FakeHost:
    """Minimal stand-in exposing the surface HitTestService reads."""

    def __init__(self, entities, scale=1.0, ox=0.0, oy=0.0):
        self._entities = entities
        self._scale = scale
        self._ox = ox
        self._oy = oy

    def _w2c(self, x, y):
        return (x * self._scale + self._ox, -y * self._scale + self._oy)

    def _c2w(self, cx, cy):
        return ((cx - self._ox) / self._scale, -(cy - self._oy) / self._scale)

    def _entity_selectable(self, index):
        return not self._entities[index].hidden


def _scene(n, verts, seed):
    rng = random.Random(seed)
    entities = []
    for _ in range(n):
        ox, oy = rng.uniform(0, 500), rng.uniform(0, 500)
        radius = rng.uniform(2, 20)
        points = [
            (
                ox + math.cos(t / verts * 2 * math.pi) * radius,
                oy + math.sin(t / verts * 2 * math.pi) * radius,
            )
            for t in range(verts)
        ]
        entity = EntityRecord(points=points)
        entity.hidden = rng.random() < 0.2
        entities.append(entity)
    return entities


def test_index_matches_linear_across_zoom_and_selection():
    rng = random.Random(42)
    for scale in (0.25, 1.0, 3.7):
        host = FakeHost(_scene(60, 12, seed=7), scale=scale, ox=13.0, oy=200.0)
        service = HitTestService(host)
        for _ in range(400):
            cx, cy = rng.uniform(-50, 600), rng.uniform(-50, 600)
            assert service.nearest_vertex(cx, cy) == service._nearest_vertex_linear(cx, cy)


def test_index_rebuilds_when_entities_are_replaced():
    host = FakeHost(_scene(20, 8, seed=1), scale=1.0)
    service = HitTestService(host)
    # Prime the cache.
    service.nearest_vertex(250.0, 250.0)
    # Replace the entity list wholesale, as a committed edit does.
    host._entities = [EntityRecord(points=[(100.0, -100.0)])]
    # w2c((100,-100)) == (100, 100); a query right on it must find (0, 0).
    assert service.nearest_vertex(100.0, 100.0) == (0, 0)
    assert service.nearest_vertex(100.0, 100.0) == service._nearest_vertex_linear(100.0, 100.0)


def test_index_and_linear_agree_on_empty_scene():
    host = FakeHost([], scale=1.0)
    service = HitTestService(host)
    assert service.nearest_vertex(0.0, 0.0) is None
