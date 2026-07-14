"""End-to-end preview and generation tests for the Pattern page."""

from src.ui.pages.pattern.services import PatternProcessingService
from src.ui.pages.pattern.workers import run_generate

OUTLINE = [[(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]]


def test_available_patterns_have_exactly_one_parameter_definition():
    from src.infra.constants import PATTERNS
    from src.ui.pages.pattern._spec import PARAM_SPECS

    assert set(PATTERNS) - {"— None —"} == set(PARAM_SPECS)


def test_open_paths_are_reported_as_neutral_linework():
    warning = PatternProcessingService.validate_outline_inputs(
        [OUTLINE[0], [(20.0, 20.0), (30.0, 20.0), (30.0, 30.0)]]
    )
    assert warning == "Using 1 closed outline(s); keeping 1 open path(s) as unfilled linework."


def test_near_open_outline_does_not_pass_preflight_then_disappear():
    import pytest

    near_open = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0), (0.1, 0.0)]
    with pytest.raises(ValueError, match="1 open path"):
        PatternProcessingService.validate_outline_inputs([near_open])


def test_basketweave_dispatch_is_available():
    from shapely.geometry import box

    result = PatternProcessingService()._gen_pattern(
        box(0, 0, 20, 20),
        "Basketweave",
        {"strip_w": 2.0, "strip_l": 8.0, "gap": 0.2},
    )
    assert result


def test_build_preview_polys_honeycomb():
    pps = PatternProcessingService()
    res = pps.build_preview_polys(
        OUTLINE,
        pattern="Honeycomb",
        params={"r": 5, "gap": 1},
        # Scale matching the original dimensions keeps generators in the
        # same coordinate space (the UI normally passes pixel sizes).
        scale=(100.0, 100.0),
        orig_w=100.0,
        orig_h=100.0,
        border_polys=None,
    )
    assert len(res["outline"]) == 1
    assert res["pattern"]
    assert res["fill"] == []
    assert len(res["display"]) == len(res["outline"]) + len(res["pattern"])
    assert res["count"] == len(res["pattern"])


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
    _run_generate_sync([square], out, "Honeycomb", {"r": 2.0, "gap": 0.4}, (20.0, 20.0), 20.0, 20.0)
    doc = ezdxf.readfile(str(out))  # type: ignore[attr-defined]
    layers = {e.dxf.layer for e in doc.modelspace()}
    assert layers == {"pattern", "outline"}


def test_multiple_outlines_still_share_one_pattern_and_one_outline_layer(tmp_path):
    """Every outline is its own entity and every fill cell is its own
    entity, but they all share one 'outline' layer and one 'pattern' layer
    respectively — laser/CAM software (e.g. LightBurn/StarFX) treats a
    layer as one job, so splitting outlines/cells across per-outline
    layers used to make it run each one as its own job."""
    from collections import Counter

    import ezdxf

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
    assert set(counts) == {"pattern", "outline"}
    # Two outline entities on the one shared "outline" layer.
    assert counts["outline"] == 2
    # Many hex-cell entities on the one shared "pattern" layer.
    assert counts["pattern"] > 2
