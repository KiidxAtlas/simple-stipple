"""Opening what we write — including the image inside an SVG.

The canvas already imported DXF, FVI and SVG linework. Adding an embedded
raster to the SVG export created a one-way door: reopening our own file
recovered the outlines and dropped the artwork without a word.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from simple_stipple.core.formats.svg import (
    SvgImagePlacement,
    read_svg_images,
    write_document_svg,
)
from simple_stipple.features.pattern.page import PatternPage

SQUARE = [(0.0, 0.0), (45.0, 0.0), (45.0, 30.0), (0.0, 30.0), (0.0, 0.0)]


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _png(size: tuple[int, int] = (64, 32), shade: int = 90) -> bytes:
    buffer = io.BytesIO()
    Image.new("L", size, shade).save(buffer, format="PNG")
    return buffer.getvalue()


def test_an_embedded_image_survives_the_write_read_cycle(tmp_path) -> None:
    original = SvgImagePlacement(_png(), x_mm=12.5, y_mm=7.25, width_mm=40.0, height_mm=20.0)
    target = tmp_path / "doc.svg"
    write_document_svg([SQUARE], target, images=[original])

    restored = read_svg_images(target)
    assert len(restored) == 1
    assert restored[0].png_bytes == original.png_bytes
    assert restored[0].width_mm == pytest.approx(original.width_mm)
    assert restored[0].height_mm == pytest.approx(original.height_mm)


def test_the_image_lands_where_the_outlines_land(tmp_path) -> None:
    """The writer's padding shifts the whole document; both must shift alike.

    An image that drifted relative to its outlines would be worse than no
    import at all — it would look right and cut wrong.
    """
    from simple_stipple.core.formats.dxf import load_dxf_polylines_with_report
    from simple_stipple.core.formats.svg import svg_to_dxf

    placement = SvgImagePlacement(_png(), x_mm=12.5, y_mm=7.25, width_mm=40.0, height_mm=20.0)
    svg = tmp_path / "doc.svg"
    write_document_svg([SQUARE], svg, images=[placement])

    dxf = tmp_path / "doc.dxf"
    svg_to_dxf(svg, dxf)
    polys, _report = load_dxf_polylines_with_report(str(dxf))
    restored = read_svg_images(svg)[0]

    outline_shift = (polys[0][0][0] - SQUARE[0][0], polys[0][0][1] - SQUARE[0][1])
    image_shift = (restored.x_mm - placement.x_mm, restored.y_mm - placement.y_mm)
    assert outline_shift == pytest.approx(image_shift)


def test_rotation_survives_the_flip_in_both_directions(tmp_path) -> None:
    placement = SvgImagePlacement(
        _png(), x_mm=5.0, y_mm=5.0, width_mm=20.0, height_mm=10.0, rotation_deg=30.0
    )
    target = tmp_path / "rotated.svg"
    write_document_svg([SQUARE], target, images=[placement])
    assert read_svg_images(target)[0].rotation_deg == pytest.approx(30.0)


def test_an_svg_without_images_reads_as_no_images(tmp_path) -> None:
    target = tmp_path / "plain.svg"
    write_document_svg([SQUARE], target)
    assert read_svg_images(target) == []


def test_an_unreadable_image_reference_is_skipped_not_fatal(tmp_path) -> None:
    target = tmp_path / "broken.svg"
    target.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" '
        'width="10mm" height="10mm">'
        '<image x="0" y="0" width="5" height="5" href="does-not-exist.png"/>'
        '<image x="0" y="0" width="5" height="5" href="data:image/png;base64,!!!"/>'
        "</svg>"
    )
    assert read_svg_images(target) == []


def test_reopening_our_own_export_brings_the_image_back(
    app: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The whole point: export an outline with an image, open it, see both."""
    from PySide6.QtTest import QTest

    from simple_stipple.features.pattern import page as page_module

    art = tmp_path / "mountains.png"
    art.write_bytes(_png())

    page = PatternPage(settings={})
    page.load_outline_polys([SQUARE])
    page._zone_list.setCurrentRow(0)
    page._engraving_image_path = str(art)
    page._engrave_x.setValue(5.0)
    page._engrave_y.setValue(4.0)
    page._engrave_w.setValue(30.0)
    page._engrave_h.setValue(15.0)
    page._attach_image_to_selected_region(str(art))
    page._select_export_format("svg")

    target = tmp_path / "roundtrip.svg"
    monkeypatch.setattr(page_module, "pick_save_file", lambda *a, **k: str(target))
    page._export_document_job()
    deadline = 8000
    while not target.exists() and deadline > 0:
        QTest.qWait(50)
        deadline -= 50
    assert target.exists()
    page.shutdown()
    page.close()

    reopened = PatternPage(settings={})
    reopened._load_outline_file(str(target))

    assert reopened._edit_polys, "the outline did not come back"
    assert reopened._engraving_image_path, "the image did not come back"
    assert reopened._engrave_w.value() == pytest.approx(30.0)
    assert reopened._engrave_h.value() == pytest.approx(15.0)
    # It is a real operation again, so the next export writes it too.
    assert any(op.kind == "engrave" for op in reopened._enabled_operations())
    reopened.shutdown()
    reopened.close()


# ── Origin fidelity ───────────────────────────────────────────────────────
#
# Reopening an export used to translate the part onto its own bounding box.
# For a drawing at negative coordinates that is not a rounding nuisance: a
# part drawn at x = -33.6 came back at x = +2, a 35.6 mm move.

NEGATIVE = [(-33.6, -10.0), (66.4, -10.0), (66.4, 51.5), (-33.6, 51.5), (-33.6, -10.0)]


def _reimport_outline(svg_path, tmp_path):
    from simple_stipple.core.formats.dxf import load_dxf_polylines_with_report
    from simple_stipple.core.formats.svg import svg_to_dxf

    dxf = tmp_path / "reimport.dxf"
    svg_to_dxf(svg_path, dxf)
    polys, _report = load_dxf_polylines_with_report(str(dxf))
    return polys


@pytest.mark.parametrize("geometry", [SQUARE, NEGATIVE], ids=["at-origin", "negative"])
def test_a_document_reopens_exactly_where_it_was_drawn(tmp_path, geometry) -> None:
    placement = SvgImagePlacement(
        _png(), x_mm=geometry[0][0], y_mm=geometry[0][1], width_mm=20.0, height_mm=10.0
    )
    target = tmp_path / "doc.svg"
    write_document_svg([geometry], target, images=[placement])

    outline = _reimport_outline(target, tmp_path)[0]
    assert outline[0][0] == pytest.approx(geometry[0][0])
    assert outline[0][1] == pytest.approx(geometry[0][1])

    restored = read_svg_images(target)[0]
    assert restored.x_mm == pytest.approx(placement.x_mm)
    assert restored.y_mm == pytest.approx(placement.y_mm)


def test_the_drawing_does_not_drift_across_repeated_round_trips(tmp_path) -> None:
    """The old normalisation compounded: every save/open moved the part again."""
    geometry = [list(point) for point in NEGATIVE]
    for index in range(4):
        target = tmp_path / f"pass{index}.svg"
        write_document_svg([[tuple(p) for p in geometry]], target)
        geometry = [list(point) for point in _reimport_outline(target, tmp_path)[0]]
    assert geometry[0][0] == pytest.approx(NEGATIVE[0][0])
    assert geometry[0][1] == pytest.approx(NEGATIVE[0][1])


def test_draft_exports_are_origin_stable_too(tmp_path) -> None:
    from simple_stipple.core.formats.svg import write_polylines_svg

    target = tmp_path / "draft.svg"
    write_polylines_svg([NEGATIVE], target)
    outline = _reimport_outline(target, tmp_path)[0]
    assert outline[0][0] == pytest.approx(NEGATIVE[0][0])
    assert outline[0][1] == pytest.approx(NEGATIVE[0][1])


def test_a_foreign_svg_still_normalises_onto_its_viewbox(tmp_path) -> None:
    """Arbitrary artwork has a page, not a machine bed. It must not change."""
    target = tmp_path / "foreign.svg"
    target.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="-35.6 0 104 65.54" '
        'width="104mm" height="65.54mm">'
        '<path d="M-33.6,2 L66.4,2"/>'
        "</svg>"
    )
    outline = _reimport_outline(target, tmp_path)[0]
    # Unchanged behaviour: translated into the viewBox's positive space.
    assert outline[0][0] == pytest.approx(2.0)


def test_a_curved_path_imports_as_a_flattened_polyline_not_dropped(tmp_path) -> None:
    """Bezier paths used to be rejected outright as an unsupported command."""
    from simple_stipple.core.formats.svg import svg_to_dxf

    target = tmp_path / "curve.svg"
    target.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" '
        'width="20mm" height="20mm">'
        '<path d="M0,0 C0,10 10,10 10,0 S20,-10 20,0"/>'
        "</svg>"
    )
    dxf = tmp_path / "curve.dxf"
    result = svg_to_dxf(target, dxf)
    assert result["unsupported_paths"] == 0

    polys = _reimport_outline(target, tmp_path)
    assert len(polys) == 1
    # A flattened curve has many more than the 4 anchor points of the d string.
    assert len(polys[0]) > 10


def test_draft_shows_an_imported_svg_image_instead_of_an_empty_outline(
    app: QApplication, tmp_path
) -> None:
    """Draft edits linework, but it must still *show* the artwork.

    The geometry was drawn over this image. Importing the outline and hiding
    the picture left the user looking at an empty square with no way to tell
    the image had come across at all.
    """
    from simple_stipple.features.draft.page import DraftPage

    target = tmp_path / "with-image.svg"
    write_document_svg(
        [NEGATIVE],
        target,
        images=[SvgImagePlacement(_png(), x_mm=-30.0, y_mm=-5.0, width_mm=90.0, height_mm=50.0)],
    )

    page = DraftPage(settings={})
    page._load_vector(str(target))

    assert page._canvas.get_polylines_state(), "the outline did not import"
    assert page._canvas._bg_pil is not None, "the image is not on the canvas"
    assert page._canvas._bg_x_mm == pytest.approx(-30.0)
    assert page._canvas._bg_y_mm == pytest.approx(-5.0)
    assert page._canvas._bg_w_mm == pytest.approx(90.0)
    # It is a real object, not a stuck decal: pick it up, move it, delete it.
    assert page._canvas._bg_editable is True
    assert "engraving image" in (page._import_note or "")
    page.close()


def test_a_backdrop_can_be_moved_and_deleted(app: QApplication, tmp_path) -> None:
    """A reference image you cannot remove is worse than one you opted into.

    Draft mounted the imported image non-editable and offered no way to clear
    it, so it was stuck on the canvas for the rest of the session.
    """
    from simple_stipple.features.draft.page import DraftPage

    target = tmp_path / "with-image.svg"
    write_document_svg(
        [SQUARE],
        target,
        images=[SvgImagePlacement(_png(), x_mm=1.0, y_mm=1.0, width_mm=20.0, height_mm=10.0)],
    )
    page = DraftPage(settings={})
    page._load_vector(str(target))

    assert page._canvas._bg_editable, "the backdrop cannot be picked up"
    page._canvas.select_background_image(True)
    assert page._canvas._bg_selected, "the backdrop cannot be selected"

    page._on_backdrop_key("remove")
    assert page._canvas._bg_pil is None, "the backdrop cannot be deleted"
    assert not (page._import_note or ""), "the note outlived the image it described"
    page.close()


def test_image_controls_survive_turning_advanced_mode_off(app: QApplication, tmp_path) -> None:
    """Advanced hid the section that owns Remove, stranding the image.

    Since the inspector rework the engraving section is the only place an
    image can be removed or placed, so it follows the selection. Only the
    power/speed/passes detail is advanced.
    """
    art = tmp_path / "art.png"
    art.write_bytes(_png())

    page = PatternPage(settings={})
    page.load_outline_polys([SQUARE])
    page._zone_list.setCurrentRow(0)
    page._engraving_image_path = str(art)
    page._attach_image_to_selected_region(str(art))

    page._set_advanced_mode(False)
    assert page._engraving_section.isVisibleTo(page), "the image controls vanished"
    assert page._engrave_remove_btn.isVisibleTo(page), "Remove is unreachable"
    assert page._engrave_x.isVisibleTo(page), "placement is unreachable"
    assert not page._engraving_process_section.isVisibleTo(page), "calibration is not basic"

    page._remove_engraving_image()
    assert page._engraving_image_path == ""
    page.shutdown()
    page.close()
