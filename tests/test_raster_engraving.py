import json

import pytest
from PIL import Image

from src.backend.raster_engraving import (
    RasterEngravingSpec,
    export_raster_job,
    prepare_engraving_image,
)


def test_raster_job_preserves_physical_placement_and_power_metadata(tmp_path):
    source = tmp_path / "photo.png"
    Image.new("L", (20, 10), 128).save(source)
    spec = RasterEngravingSpec(
        x_mm=12.5, y_mm=7.0, width_mm=40, height_mm=20,
        line_interval_mm=0.1, max_power_percent=72, passes=3,
    )
    png, sidecar, positioned = export_raster_job(source, tmp_path / "engraving.png", spec)
    assert png.exists() and sidecar.exists() and positioned.exists()
    payload = json.loads(sidecar.read_text())
    assert payload["placement_mm"] == {"x": 12.5, "y": 7.0, "width": 40, "height": 20}
    assert payload["settings"]["max_power_percent"] == 72
    assert payload["settings"]["passes"] == 3
    with Image.open(png) as result:
        assert result.size == (400, 200)
    svg = positioned.read_text()
    assert 'viewBox="0.0 0.0 52.5 27.0"' in svg
    assert 'x="12.5" y="7.0" width="40" height="20"' in svg


def test_raster_tone_controls_change_power_map():
    image = Image.new("L", (2, 1))
    image.putdata([0, 255])
    result = prepare_engraving_image(
        image, RasterEngravingSpec(width_mm=2, height_mm=1, line_interval_mm=0.5)
    )
    assert min(result.getdata()) < max(result.getdata())


@pytest.mark.parametrize("interval", [0.0, 3.0])
def test_raster_rejects_unsafe_line_interval(interval):
    with pytest.raises(ValueError):
        RasterEngravingSpec(line_interval_mm=interval).validated()
