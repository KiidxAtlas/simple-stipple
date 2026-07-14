"""Multi-line text layout and attaching a text object to a path."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QFontDatabase  # noqa: E402

from tests.test_canvas_behavior import make_view  # noqa: E402


def _bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _system_family() -> str:
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()


def test_multiline_text_stacks_vertically_not_side_by_side(qapp):
    from src.ui.canvas.mixins.hud_text import text_to_polylines

    family = _system_family()
    one_line = text_to_polylines("Hi", family=family, height_mm=10.0)
    two_line = text_to_polylines("Hi\nBye", family=family, height_mm=10.0)
    if not one_line or not two_line:
        pytest.skip("no usable font on offscreen platform")

    _, _, x1_one, _ = _bbox([p for poly in one_line for p in poly])
    _, _, x1_two, _ = _bbox([p for poly in two_line for p in poly])
    # Stacked lines share the block's total width envelope (bounded by the
    # wider of the two lines) rather than the lines running side by side
    # (which would roughly sum the two single-line widths).
    assert x1_two < x1_one * 1.5


def test_multiline_text_contour_count_matches_glyphs(qapp):
    from src.ui.canvas.mixins.hud_text import text_to_polylines

    family = _system_family()
    hi = text_to_polylines("Hi", family=family, height_mm=10.0)
    hi_bye = text_to_polylines("Hi\nBye", family=family, height_mm=10.0)
    bye = text_to_polylines("Bye", family=family, height_mm=10.0)
    if not hi or not hi_bye or not bye:
        pytest.skip("no usable font on offscreen platform")
    assert len(hi_bye) == len(hi) + len(bye)


def _place_text(v, wx, wy, text="Hi"):
    family = _system_family()
    n = v.add_text_at(wx, wy, text=text, family=family, height_mm=8.0)
    if n == 0:
        pytest.skip("no usable font on offscreen platform")
    return n


def test_attach_text_to_path_moves_contours_onto_the_path(qapp):
    v = make_view(qapp, [])
    n = _place_text(v, 0.0, 0.0)
    text_idx = 0
    path_idx = v._append_entity([(0.0, 50.0), (100.0, 50.0)])
    assert v.attach_text_to_path(text_idx, path_idx)

    members = v._text_member_indices(text_idx)
    assert len(members) == n
    # Baseline (the lowest point across every glyph contour) sits exactly on
    # the (horizontal, unrotated) path; ascenders/cap-height sit above it.
    ys = [y for i in members for _x, y in v._entities[i].points]
    assert min(ys) == pytest.approx(50.0, abs=0.1)
    assert max(ys) > 50.0

    meta = v._entities[text_idx].meta
    assert meta is not None
    assert meta["text_params"]["attached_path_idx"] == path_idx


def test_rebuild_text_reflows_onto_the_attached_path(qapp):
    v = make_view(qapp, [])
    _place_text(v, 0.0, 0.0)
    text_idx = 0
    path_idx = v._append_entity([(0.0, 40.0), (100.0, 40.0)])
    assert v.attach_text_to_path(text_idx, path_idx)

    # After the path was created (idx 1), text sits at indices [0, ...]; the
    # path is now the last entity. Rebuild the text with new content and
    # confirm it re-attaches to the (now index-shifted) path automatically.
    text_idx = v._text_member_indices(0)[0]
    values = v.text_params_at(text_idx)
    assert values is not None
    values["text"] = "Bye"
    assert v.rebuild_text(text_idx, values)

    assert v._sel
    new_members = v._text_member_indices(next(iter(v._sel)))
    assert new_members
    ys = [y for i in new_members for _x, y in v._entities[i].points]
    assert min(ys) == pytest.approx(40.0, abs=0.1)


def test_attach_text_to_path_rejects_missing_path(qapp):
    v = make_view(qapp, [])
    _place_text(v, 0.0, 0.0)
    assert v.attach_text_to_path(0, 99) is False
