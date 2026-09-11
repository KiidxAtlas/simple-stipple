from __future__ import annotations

from PIL import Image, ImageDraw

from simple_stipple.app.launcher import main
from simple_stipple.core.formats.dxf import load_dxf_polylines, write_polylines_dxf


def test_headless_pattern_command_writes_a_dxf_without_qt(tmp_path) -> None:
    source = tmp_path / "outline.dxf"
    output = tmp_path / "pattern.dxf"
    write_polylines_dxf(
        [[(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0), (0.0, 0.0)]], str(source)
    )

    assert main(["pattern", str(source), str(output), "--preset", "honeycomb"]) == 0
    assert load_dxf_polylines(str(output))


def test_headless_pattern_and_trace_accept_input_folders(tmp_path) -> None:
    outlines = tmp_path / "outlines"
    patterns = tmp_path / "patterns"
    images = tmp_path / "images"
    traces = tmp_path / "traces"
    outlines.mkdir()
    images.mkdir()
    square = [[(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0), (0.0, 0.0)]]
    write_polylines_dxf(square, str(outlines / "one.dxf"))
    write_polylines_dxf(square, str(outlines / "two.dxf"))

    assert main(["pattern", str(outlines), str(patterns)]) == 0
    assert load_dxf_polylines(str(patterns / "one_pattern.dxf"))
    assert load_dxf_polylines(str(patterns / "two_pattern.dxf"))

    image = Image.new("RGB", (40, 40), "white")
    ImageDraw.Draw(image).rectangle((8, 8, 31, 31), fill="black")
    image.save(images / "part.png")
    assert main(["trace", str(images), str(traces), "--width-mm", "20"]) == 0
    assert load_dxf_polylines(str(traces / "part_trace.dxf"))
