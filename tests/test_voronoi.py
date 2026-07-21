from __future__ import annotations

import pytest

from src.backend.voronoi import delaunay_triangulation, voronoi_diagram

POINTS = [(0.0, 0.0), (2.0, 0.0), (0.0, 2.0), (2.0, 2.0), (1.0, 0.8)]


def test_voronoi_returns_finite_cell_for_every_site():
    cells = voronoi_diagram(POINTS)
    assert len(cells) == len(POINTS)
    assert all(len(cell) >= 3 for cell in cells)
    assert all(abs(value) < 100 for cell in cells for point in cell for value in point)


def test_delaunay_returns_valid_site_indices():
    triangles = delaunay_triangulation(POINTS)
    assert triangles
    assert all(len(set(triangle)) == 3 for triangle in triangles)
    assert all(0 <= index < len(POINTS) for triangle in triangles for index in triangle)


def test_spatial_algorithms_reject_collinear_sites():
    with pytest.raises(ValueError):
        voronoi_diagram([(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)])
