"""End-to-end preview build for a square outline with a square-grid pattern."""

from src.ui.pages.pattern.services import PatternProcessingService
from src.ui.pages.pattern.workers import run_generate

OUTLINE = [[(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]]


def test_build_preview_polys_square_grid():
    pps = PatternProcessingService()
    res = pps.build_preview_polys(
        OUTLINE,
        pattern="Square Grid",
        params={"spacing": 10},
        # Scale matching the original dimensions keeps generators in the
        # same coordinate space (the UI normally passes pixel sizes).
        scale=(100.0, 100.0),
        orig_w=100.0,
        orig_h=100.0,
        border_polys=None,
    )
    assert len(res["outline"]) == 1
    assert len(res["pattern"]) == 21
    assert res["fill"] == []
    assert len(res["display"]) == len(res["outline"]) + len(res["pattern"])
    assert res["count"] == 21


def _run_generate_sync(active, out_path, pattern, params, scale, orig_w, orig_h):
    svc = PatternProcessingService()
    results: dict = {}
    run_generate(
        active,
        str(out_path),
        pattern,
        params,
        scale,
        None,
        pattern_service=svc,
        orig_w=orig_w,
        orig_h=orig_h,
        on_done=lambda args: results.update(done=args),
        on_error=lambda args: results.update(error=args),
    )
    assert "error" not in results, results.get("error")
    return results["done"]


def test_single_outline_keeps_shared_pattern_layer(tmp_path):
    import ezdxf

    square = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0), (0.0, 0.0)]
    out = tmp_path / "single.dxf"
    _run_generate_sync(
        [square], out, "Honeycomb", {"r": 2.0, "gap": 0.4}, (20.0, 20.0), 20.0, 20.0
    )
    doc = ezdxf.readfile(str(out))  # type: ignore[attr-defined]
    layers = {e.dxf.layer for e in doc.modelspace()}
    assert layers == {"pattern", "outline"}


def test_multiple_outlines_split_pattern_layer_per_outline(tmp_path):
    """Each outline gets its own pattern_N/outline_N layer pair, but every
    shape belonging to one outline still shares a single pattern_N layer —
    laser/CAM software (e.g. LightBurn/StarFX) treats a layer as one job,
    so splitting per-shape instead of per-outline used to make it run each
    pattern cell as its own job rather than one job per outline."""
    import ezdxf
    from collections import Counter

    sq1 = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0), (0.0, 0.0)]
    sq2 = [(40.0, 0.0), (60.0, 0.0), (60.0, 20.0), (40.0, 20.0), (40.0, 0.0)]
    out = tmp_path / "multi.dxf"
    _run_generate_sync(
        [sq1, sq2],
        out,
        "Honeycomb",
        {"r": 2.0, "gap": 0.4},
        (60.0, 20.0),
        60.0,
        20.0,
    )
    doc = ezdxf.readfile(str(out))  # type: ignore[attr-defined]
    counts = Counter(e.dxf.layer for e in doc.modelspace())
    assert set(counts) == {"pattern_1", "pattern_2", "outline_1", "outline_2"}
    assert counts["outline_1"] == 1
    assert counts["outline_2"] == 1
    # Each outline's fill is many hex cells, all on that one shared layer.
    assert counts["pattern_1"] > 1
    assert counts["pattern_2"] > 1

