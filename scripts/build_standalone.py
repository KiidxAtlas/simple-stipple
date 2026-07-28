"""Build a standalone desktop binary for the current platform via PyInstaller."""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "SimpleStipple"
DATA_FILES = (
    ("src/simple_stipple/ui/style/theme.qss", "simple_stipple/ui/style"),
    ("src/simple_stipple/ui/style/icons", "simple_stipple/ui/style/icons"),
    ("src/simple_stipple/resources/tiles", "simple_stipple/resources/tiles"),
    ("assets/icon.png", "assets"),
)


def _add_data_arg(source: Path, dest: str, system: str | None = None) -> str:
    separator = ";" if (system or platform.system()) == "Windows" else ":"
    return f"{source}{separator}{dest}"


def build_command(system: str | None = None) -> list[str]:
    """Return the one canonical PyInstaller command for a platform."""
    current_system = system or platform.system()
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        APP_NAME,
        "--clean",
        "--noconfirm",
        "--windowed",
    ]

    for source, destination in DATA_FILES:
        cmd += ["--add-data", _add_data_arg(ROOT / source, destination, current_system)]

    if current_system == "Windows":
        cmd += ["--onefile", "--icon", str(ROOT / "assets" / "icon.ico")]
    elif current_system == "Darwin":
        cmd += ["--icon", str(ROOT / "assets" / "icon.png")]
        cmd += ["--osx-bundle-identifier", "com.kiidxatlas.simple-stipple"]

    cmd.append(str(ROOT / "main.py"))
    return cmd


def write_sha256(artifact: Path) -> Path:
    """Write the standard SHA-256 sidecar consumed by the in-app updater."""
    digest = hashlib.sha256()
    with artifact.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    sidecar = artifact.with_name(f"{artifact.name}.sha256")
    sidecar.write_text(f"{digest.hexdigest()}  {artifact.name}\n", encoding="ascii")
    return sidecar


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checksum",
        type=Path,
        metavar="ARTIFACT",
        help="write ARTIFACT.sha256 instead of running PyInstaller",
    )
    parser.add_argument(
        "--print-manifest",
        action="store_true",
        help="print the source-to-bundle resource manifest without building",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.checksum is not None:
        sidecar = write_sha256(args.checksum.resolve())
        print(f"Wrote checksum: {sidecar}")
        return 0
    if args.print_manifest:
        for source, destination in DATA_FILES:
            print(f"{source} -> {destination}")
        return 0

    dist = ROOT / "dist"
    build = ROOT / "build"
    build_environment = os.environ.copy()
    build_environment["PYINSTALLER_CONFIG_DIR"] = str(build / "pyinstaller-cache")
    subprocess.run(
        build_command(),
        cwd=str(ROOT),
        check=True,
        env=build_environment,
    )

    print(f"Built standalone app in: {dist}")
    print(f"Build temp files in: {build}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
