from PIL import Image

from src.backend.laserstar_package import export_laserstar_package
from src.backend.raster_engraving import RasterEngravingSpec


def test_laserstar_package_contains_operator_handoff_files(tmp_path):
    source = tmp_path / "photo.png"
    Image.new("L", (20, 10), 128).save(source)
    square = [(10.0, 20.0), (30.0, 20.0), (30.0, 40.0), (10.0, 40.0), (10.0, 20.0)]
    spec = RasterEngravingSpec(
        x_mm=12, y_mm=22, width_mm=10, height_mm=5,
        line_interval_mm=0.1, max_power_percent=50, speed_mm_s=1500, passes=1,
    )

    folder = export_laserstar_package(
        tmp_path, "Operator Test", [square],
        raster_source=source, raster_spec=spec, raster_mask=[square],
    )

    expected = {
        "01_pattern-and-outline.fvi",
        "02_grayscale-engraving.png",
        "02_grayscale-engraving.engrave.json",
        "03_placement-reference.fvi",
        "LaserStar-Setup.txt",
        "job-manifest.json",
        "job-preview.png",
    }
    assert expected <= {path.name for path in folder.iterdir()}
    fvi = (folder / "01_pattern-and-outline.fvi").read_text()
    # StarFX FVI uses 0.254 mm units; preserve-origin export converts without
    # rebasing, so 10/20 mm become 39.370079/78.740157 FVI units.
    assert "MOVEDIST 39.370079,78.740157" in fvi
    setup = (folder / "LaserStar-Setup.txt").read_text()
    assert "LaserStar 3602XL" in setup
    assert "LM2 60 W" in setup
    assert "DRAWSPEED: 1500.0 mm/s" in setup
    assert "LASERFREQ starting value: 50.0 kHz" in setup
