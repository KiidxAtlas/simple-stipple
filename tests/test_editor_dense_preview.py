"""Focused behavior coverage for the dense editor render cache."""

from __future__ import annotations

from types import SimpleNamespace

from simple_stipple.canvas.rendering import DensePreviewRenderer


def test_dense_preview_retains_world_paths_and_clears_every_cache_layer() -> None:
    entity = SimpleNamespace(
        id="line",
        points=[(0.0, 0.0), (4.0, 0.0)],
        hidden=False,
        construction=False,
        locked=False,
        layer="Outline",
    )
    host = SimpleNamespace(
        _entities=[entity],
        _sel=set(),
        _layer_colors={},
        _accent_polys={},
        _scale=1.0,
        _ox=0.0,
        _oy=0.0,
        _layer_service=SimpleNamespace(on_active=lambda _entity: True),
        _flattened_points_by_id=lambda _entity_id: entity.points,
    )
    cache = DensePreviewRenderer(host)

    batches = cache.build_batches()
    assert sum(path.elementCount() for path in batches.values()) == 2
    cache.batches = batches
    cache.raster_origin = (0.0, 0.0)
    cache.raster_size = (100, 100, 1.0)
    cache.raster_scale = 1.0

    cache.invalidate()
    assert cache.batches is None
    assert cache.raster is None
    assert cache.raster_origin is None
    assert cache.raster_size is None
    assert cache.raster_scale is None
