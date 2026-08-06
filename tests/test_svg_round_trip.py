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

from simple_stipple.engine.formats.svg import (
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
    from simple_stipple.engine.formats.dxf import load_dxf_polylines_with_report
    from simple_stipple.engine.formats.svg import svg_to_dxf

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
