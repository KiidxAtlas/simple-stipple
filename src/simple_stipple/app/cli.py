"""Small, Qt-free command-line workflows for repeatable shop jobs."""

from __future__ import annotations

import argparse
from pathlib import Path

from simple_stipple.core.formats.dxf import load_dxf_polylines, write_polylines_dxf
from simple_stipple.core.imaging import image_to_outlines
from simple_stipple.core.patterns.processing import PatternProcessor


def _source_files(path: Path, suffixes: tuple[str, ...], recursive: bool) -> list[Path]:
    if path.is_file():
        if path.suffix.lower() not in suffixes:
            raise ValueError(f"{path} is not a supported input file")
        return [path]
    if not path.is_dir():
        raise ValueError(f"Input path does not exist: {path}")
    matcher = path.rglob if recursive else path.glob
    sources = sorted(candidate for suffix in suffixes for candidate in matcher(f"*{suffix}"))
    if not sources:
        raise ValueError(f"No supported input files found in {path}")
    return sources


def _destination_for(source: Path, input_path: Path, output_path: Path, suffix: str) -> Path:
    if input_path.is_file():
        return output_path.with_suffix(".dxf")
    if output_path.suffix:
        raise ValueError("A folder input requires a folder output")
    output_path.mkdir(parents=True, exist_ok=True)
    return output_path / f"{source.stem}{suffix}.dxf"


def _pattern_file(source: Path, output: Path, args: argparse.Namespace) -> None:
    outlines = load_dxf_polylines(str(source))
    if not outlines:
        raise ValueError("contains no supported polyline outlines")
    points = [point for poly in outlines for point in poly]
    width = max(x for x, _y in points) - min(x for x, _y in points)
    height = max(y for _x, y in points) - min(y for _x, y in points)
    if args.preset == "honeycomb":
        pattern, params = "Honeycomb", {"r": args.size, "gap": args.spacing}
    else:
        pattern, params = (
            "Stipple Dots",
            {
                "r": args.size,
                "spacing": max(args.spacing, args.size * 2),
                "seed": args.seed,
            },
        )
    processor = PatternProcessor()
    processor.lattice_seed = args.seed
    result = processor.build_pattern_polys(
        outlines,
        pattern=pattern,
        params=params,
        scale=(1.0, 1.0),
        orig_w=width,
        orig_h=height,
    )
    if not result:
        raise ValueError("generated no pattern geometry")
    output.parent.mkdir(parents=True, exist_ok=True)
    write_polylines_dxf(result, str(output))


def _pattern_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="simple-stipple pattern")
    parser.add_argument("input", type=Path, help="Closed DXF outline to pattern")
    parser.add_argument("output", type=Path, help="Destination DXF pattern file")
    parser.add_argument("--preset", choices=("honeycomb", "stipple"), default="honeycomb")
    parser.add_argument("--size", type=float, default=1.0, help="Cell/dot radius in millimeters")
    parser.add_argument("--spacing", type=float, default=0.25, help="Cell gap or dot spacing in mm")
    parser.add_argument("--seed", type=int, default=1, help="Deterministic seed for stipple")
    parser.add_argument("--recursive", action="store_true", help="Include DXFs in subfolders")
    args = parser.parse_args(argv)
    if args.size <= 0 or args.spacing < 0:
        parser.error("--size must be positive and --spacing cannot be negative")
    try:
        sources = _source_files(args.input, (".dxf",), args.recursive)
    except ValueError as exc:
        parser.error(str(exc))
    failed = False
    for source in sources:
        try:
            output = _destination_for(source, args.input, args.output, "_pattern")
            _pattern_file(source, output, args)
            print(f"Patterned {source.name} → {output}")
        except (OSError, ValueError) as exc:
            print(f"Failed {source}: {exc}")
            failed = True
    return int(failed)


def _trace_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="simple-stipple trace")
    parser.add_argument("input", type=Path, help="Image file or folder of images")
    parser.add_argument("output", type=Path, help="Destination DXF file or folder")
    parser.add_argument("--width-mm", type=float, default=100.0, help="Traced image width in mm")
    parser.add_argument("--threshold", type=int, default=127, help="Threshold from 0 to 255")
    parser.add_argument("--recursive", action="store_true", help="Include images in subfolders")
    args = parser.parse_args(argv)
    if args.width_mm <= 0 or not 0 <= args.threshold <= 255:
        parser.error("--width-mm must be positive and --threshold must be from 0 to 255")
    try:
        sources = _source_files(args.input, (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"), args.recursive)
    except ValueError as exc:
        parser.error(str(exc))
    failed = False
    for source in sources:
        try:
            _display, outlines, _width, _height = image_to_outlines(
                str(source), threshold=args.threshold, width_mm=args.width_mm
            )
            if not outlines:
                raise ValueError("generated no trace outlines")
            output = _destination_for(source, args.input, args.output, "_trace")
            output.parent.mkdir(parents=True, exist_ok=True)
            write_polylines_dxf(outlines, str(output))
            print(f"Traced {source.name} → {output}")
        except (OSError, ValueError) as exc:
            print(f"Failed {source}: {exc}")
            failed = True
    return int(failed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="simple-stipple")
    parser.add_argument("command", choices=("pattern", "trace"))
    args, rest = parser.parse_known_args(argv)
    if args.command == "pattern":
        return _pattern_command(rest)
    if args.command == "trace":
        return _trace_command(rest)
    return 2


__all__ = ["main"]
