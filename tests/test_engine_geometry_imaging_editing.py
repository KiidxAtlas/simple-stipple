"""Behavior characterization for canonical engine geometry, imaging, and editing."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from simple_stipple.core.editing.boolean import boolean_polylines, offset_polyline
from simple_stipple.core.editing.boolean import clipper_difference, clipper_union
from simple_stipple.core.geometry import tessellate_arc
from simple_stipple.core.geometry import build_snap_tree, find_nearest
from simple_stipple.core.geometry import delaunay_triangulation, voronoi_diagram
from simple_stipple.core.imaging import RasterEngravingSpec, prepare_engraving_image
from simple_stipple.core.imaging import (
    TraceCancelled,
    image_to_outlines,
    scale_to_mm,
    simplify_contours,
)


def test_geometry_preserves_spatial_and_tessellation_contracts() -> None:
    tree = build_snap_tree([(0.0, 0.0), (4.0, 0.0)])
    assert find_nearest(tree, (3.5, 0.0), 1.0) == (4.0, 0.0)
    assert find_nearest(tree, (3.5, 0.0), 0.1) is None

    arc = tessellate_arc(0.0, 0.0, 1.0, 0.0, 3.141592653589793, 4)
    assert arc.shape == (5, 2)
    assert tuple(arc[0]) == (1.0, 0.0)

    sites = [(0.0, 0.0), (2.0, 0.0), (0.0, 2.0)]
    assert len(voronoi_diagram(sites)) == len(sites)
    assert len(delaunay_triangulation(sites)) == 1


def test_raster_power_map_contract() -> None:
    prepared = prepare_engraving_image(
        Image.new("RGB", (4, 2), "black"),
        RasterEngravingSpec(width_mm=2.0, height_mm=1.0, line_interval_mm=0.5),
    )
    assert prepared.mode == "L"
    assert prepared.size == (4, 2)


def test_trace_pipeline_contract(tmp_path: Path) -> None:
    image_path = tmp_path / "trace-source.png"
    image = Image.new("RGB", (40, 40), "white")
    for x in range(8, 32):
        for y in range(8, 32):
            image.putpixel((x, y), (0, 0, 0))
    image.save(image_path)

    progress: list[tuple[int, str]] = []
    display, outlines, width, height = image_to_outlines(
        str(image_path),
        threshold=127,
        blur_radius=0,
        close_radius=0,
        simplify_tol=0.5,
        min_area_px=10,
        width_mm=20,
        on_progress=lambda pct, label: progress.append((pct, label)),
    )
    assert display.size == (40, 40)
    assert outlines and (width, height) == (40, 40)
    assert progress[0] == (5, "Loading image…")
    assert progress[-1] == (100, "Done.")

    with pytest.raises(TraceCancelled):
        image_to_outlines(str(image_path), cancel_check=lambda: True)


def test_polygon_editing_contracts() -> None:
    left = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (0.0, 0.0)]
    right = [(1.0, 0.0), (3.0, 0.0), (3.0, 2.0), (1.0, 2.0), (1.0, 0.0)]
    union = clipper_union([left, right])
    difference = clipper_difference([left], [right])
    assert union and difference
    assert offset_polyline(left, 0.25)
    assert boolean_polylines([left, right], "intersect")

    scaled = scale_to_mm([left], 1.0, 2)
    assert scaled[0][-1][1] == 2.0
    assert simplify_contours([left], 0.1)
