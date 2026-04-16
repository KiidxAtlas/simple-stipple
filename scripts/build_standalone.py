"""Build a standalone desktop binary for the current platform via PyInstaller."""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
