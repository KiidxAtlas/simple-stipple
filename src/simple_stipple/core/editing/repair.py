"""Conservative, data-only repairs for findings from geometry preflight."""

from __future__ import annotations

import math

from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge, unary_union

from simple_stipple.core.cad.preflight import analyze_geometry, scale_tolerance

Point = tuple[float, float]
Path = list[Point]


def _repair_path(path: Path, tolerance: float) -> list[Path]:
    # Non-finite coordinates require manual review: never guess replacement values.
    if any(not math.isfinite(value) for point in path for value in point):
        return [list(path)]
    points: Path = []
    for point in path:
        if not points or math.dist(points[-1], point) > 1e-12:
            points.append(point)
    if len(points) < 2:
        return []
    # Match preflight's three-point closure rule, including degenerate rings.
    if len(points) >= 3 and math.dist(points[0], points[-1]) <= tolerance:
        points[-1] = points[0]
        if math.dist(points[-2], points[-1]) <= 1e-12:
            points.pop()
    line = LineString(points)
    if not line.is_simple:
        # Noding preserves the drawn linework, unlike polygon.buffer(0), which
        # can discard lobes of a self-crossing outline or fill a glyph's hole.
        noded = unary_union(line)
        if isinstance(noded, MultiLineString):
            # Rejoin degree-two endpoints so a closed lobe stays closed,
            # while genuine branching junctions remain separate paths.
            noded = linemerge(noded)
        branches = getattr(noded, "geoms", (noded,))
        return [[(float(x), float(y)) for x, y in branch.coords] for branch in branches]
    return [points]


def repair_geometry(polys: list[Path], *, remove_tiny: bool = False) -> list[list[Path]]:
    """Return replacement paths per input, without modifying the input.

    Empty/zero-extent paths and exact duplicate linework are removed. Nearly
    closed rings are snapped using preflight's scale-aware tolerance, repeated
    adjacent vertices are removed, and self-crossings are split into branches.
    Deliberate open paths are kept. With ``remove_tiny``, discard fragments
    flagged as tiny by preflight after splitting, including microscopic lobes.
    Otherwise valid tiny features are retained. Call separately for different
    layers/construction roles to retain their meaning.
    """
    finite = [
        path for path in polys if all(math.isfinite(value) for point in path for value in point)
    ]
    tolerance = scale_tolerance(finite)
    seen: set[tuple] = set()
    result: list[list[Path]] = []
    for path in polys:
        replacements = []
        for repaired in _repair_path(path, tolerance):
            if not all(math.isfinite(value) for point in repaired for value in point):
                replacements.append(repaired)
                continue
            # Exact segment signatures ignore traversal direction and a ring's
            # start vertex, but do not delete merely nearby parallel linework.
            signature = tuple(sorted(tuple(sorted((a, b))) for a, b in zip(repaired, repaired[1:])))
            if signature not in seen:
                seen.add(signature)
                replacements.append(repaired)
        result.append(replacements)
    if remove_tiny:
        finite = [
            path
            for paths in result
            for path in paths
            if all(math.isfinite(value) for point in path for value in point)
        ]
        tiny = {
            tuple(finite[issue.path_index])
            for issue in analyze_geometry(finite).issues
            if issue.kind == "tiny"
        }
        result = [[path for path in paths if tuple(path) not in tiny] for paths in result]
    return result
