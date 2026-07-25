from __future__ import annotations

import pytest

from src.core.storage import read_json_file


def test_json_reader_rejects_file_over_limit(tmp_path):
    path = tmp_path / "large.json"
    path.write_text('{"value": 123}', encoding="utf-8")

    with pytest.raises(ValueError, match="too large to open safely"):
        read_json_file(path, max_bytes=4)


def test_dxf_reader_rejects_file_over_limit(tmp_path, monkeypatch):
    from src.backend.dxf import io

    path = tmp_path / "large.dxf"
    path.write_text("larger than test limit", encoding="utf-8")
    monkeypatch.setattr(io, "MAX_DXF_FILE_BYTES", 4)

    with pytest.raises(ValueError, match="too large to import safely"):
        io.load_dxf_polylines(str(path))


def test_svg_reader_rejects_file_over_limit(tmp_path, monkeypatch):
    from src.backend.dxf import svg_dxf

    path = tmp_path / "large.svg"
    path.write_text("<svg/>", encoding="utf-8")
    monkeypatch.setattr(svg_dxf, "MAX_SVG_FILE_BYTES", 4)

    with pytest.raises(ValueError, match="too large to import safely"):
        svg_dxf.svg_to_dxf(path, tmp_path / "out.dxf")
