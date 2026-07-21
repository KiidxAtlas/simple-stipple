from __future__ import annotations

import ezdxf
import pytest

from src.backend.dxf import io


def _write_document(tmp_path, document, name: str = "drawing.dxf"):
    path = tmp_path / name
    document.saveas(path)
    return path


def test_import_rejects_non_finite_unit_conversion(tmp_path, monkeypatch):
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 1
    document.modelspace().add_line((0, 0), (1, 0))
    path = _write_document(tmp_path, document)
    monkeypatch.setattr(io.units, "conversion_factor", lambda *_args: float("inf"))

    with pytest.raises(ValueError, match="invalid millimeter scale"):
        io.load_dxf_polylines(str(path))


def test_import_rejects_malformed_dxf_instead_of_returning_clean_empty_report(tmp_path):
    path = tmp_path / "broken.dxf"
    path.write_text("not a dxf", encoding="utf-8")

    with pytest.raises(ValueError, match=r"Could not open broken\.dxf as a DXF"):
        io.load_dxf_polylines_with_report(str(path))


def test_draft_surfaces_malformed_dxf_as_an_error(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from src.ui.pages.draft import DraftPage

    path = tmp_path / "broken.dxf"
    path.write_text("not a dxf", encoding="utf-8")
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda _parent, title, message: errors.append((str(title), str(message))),
    )
    page = DraftPage(settings={})
    try:
        page._load_dxf(str(path))
    finally:
        page.close()

    assert len(errors) == 1
    assert errors[0][0] == "Open DXF Failed"
    assert "Could not open broken.dxf as a DXF" in errors[0][1]


def test_import_rejects_coordinate_overflow_after_unit_conversion(tmp_path):
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 1
    document.modelspace().add_line((1e308, 0), (1e308, 1))
    path = _write_document(tmp_path, document)

    with pytest.raises(ValueError, match="finite numerical range"):
        io.load_dxf_polylines(str(path))


def test_import_reports_non_planar_supported_entity_instead_of_projecting(tmp_path):
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 4
    document.modelspace().add_line((0, 0, 2), (10, 0, 2))
    path = _write_document(tmp_path, document)

    polylines, report = io.load_dxf_polylines_with_report(str(path))

    assert polylines == []
    assert report.unsupported_entities == {"LINE (non-planar)": 1}
    assert report.has_issues
