"""Batch behavior for Convert > Fix DXF."""

from __future__ import annotations

import threading

import ezdxf

from src.backend.dxf.svg_dxf import svg_to_dxf
from src.ui.pages.convert import FixerSubTab


def _write_dxf(path, points) -> None:
    doc = ezdxf.new("R2010")
    doc.modelspace().add_lwpolyline(points)
    doc.saveas(path)


def test_folder_fix_discovers_dxf_case_insensitively_and_repairs_each(qapp, tmp_path):
    source = tmp_path / "source"
    output = tmp_path / "fixed"
    source.mkdir()
    output.mkdir()
    _write_dxf(source / "a.dxf", [(0, 0), (10, 0), (10, 10)])
    _write_dxf(source / "B.DXF", [(0, 0), (5, 0), (5, 5)])
    (source / "notes.txt").write_text("not a drawing")

    tab = FixerSubTab(settings={})
    try:
        discovered = tab._folder_dxf_files(str(source))
        assert [path.name for path in discovered] == ["a.dxf", "B.DXF"]

        tab._running = True
        tab._fix_batch(str(source), str(output), threading.Event())

        assert (output / "a.dxf").is_file()
        assert (output / "B.DXF").is_file()
        assert not (output / "notes.txt").exists()
        assert not tab._running
    finally:
        tab.deleteLater()
        qapp.processEvents()


def test_svg_curves_are_reported_instead_of_silently_misread_as_lines(tmp_path):
    source = tmp_path / "curves.svg"
    output = tmp_path / "curves.dxf"
    source.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">'
        '<path d="M 0 0 C 5 10 15 10 20 0"/>'
        '<path d="M 0 5 L 20 5"/>'
        "</svg>"
    )

    stats = svg_to_dxf(source, output)

    assert stats["unsupported_paths"] == 1
    assert stats["polylines"] == 1
