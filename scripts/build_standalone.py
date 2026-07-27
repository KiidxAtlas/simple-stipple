"""Build a standalone desktop binary for the current platform via PyInstaller."""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _add_data_arg(source: Path, dest: str) -> str:
    separator = ";" if platform.system() == "Windows" else ":"
    return f"{source}{separator}{dest}"


def main() -> int:
    app_name = "SimpleStipple"
    entry = ROOT / "main.py"
    dist = ROOT / "dist"
    build = ROOT / "build"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        app_name,
        "--noconfirm",
        "--windowed",
        "--add-data",
        _add_data_arg(
            ROOT / "src" / "simple_stipple" / "ui" / "style" / "theme.qss",
            "simple_stipple/ui/style",
        ),
        "--add-data",
        _add_data_arg(
            ROOT / "src" / "simple_stipple" / "ui" / "style" / "icons",
            "simple_stipple/ui/style/icons",
        ),
        "--add-data",
        _add_data_arg(
            ROOT / "src" / "simple_stipple" / "resources" / "tiles",
            "simple_stipple/resources/tiles",
        ),
        "--add-data",
        _add_data_arg(ROOT / "assets" / "icon.png", "assets"),
        str(entry),
    ]

    if platform.system() == "Darwin":
        cmd += ["--osx-bundle-identifier", "com.kiidxatlas.simple-stipple"]

    subprocess.run(cmd, cwd=str(ROOT), check=True)

    print(f"Built standalone app in: {dist}")
    print(f"Build temp files in: {build}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
