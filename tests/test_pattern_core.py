"""End-to-end preview build for a square outline with a square-grid pattern."""

from src.ui.pages.pattern.services import PatternProcessingService

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

