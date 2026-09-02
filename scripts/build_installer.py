"""Compile the Windows Inno Setup installer for the current project version."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
INSTALLER_SCRIPT = ROOT / "installer" / "SimpleStipple.iss"
DIST_DIR = ROOT / "dist"
PAYLOAD = DIST_DIR / "SimpleStipple"


def project_version() -> str:
    """Read the release version from the project metadata."""
    with (ROOT / "pyproject.toml").open("rb") as stream:
        metadata = tomllib.load(stream)
    version = metadata.get("project", {}).get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("pyproject.toml does not define a project version")
    return version.strip()


def _candidate_iscc_paths() -> list[Path]:
    """Return the conventional Inno Setup compiler locations on Windows."""
    candidates: list[Path] = []
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / "Inno Setup 6" / "ISCC.exe")
    return candidates


def find_iscc(system: str | None = None) -> Path:
    """Locate ``ISCC.exe``, failing clearly when run outside Windows."""
    if (system or platform.system()) != "Windows":
        raise OSError("Inno Setup installers can only be built on Windows")

    for executable_name in ("ISCC.exe", "ISCC"):
        resolved = shutil.which(executable_name)
        if resolved:
            return Path(resolved).resolve()

    for candidate in _candidate_iscc_paths():
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(
        "Could not find ISCC.exe. Install Inno Setup 6 or add its directory to PATH."
    )


def artifact_path(version: str) -> Path:
    """Return the exact installer artifact path for ``version``."""
    return DIST_DIR / f"SimpleStipple-Setup-{version}.exe"


def build_command(iscc: Path, version: str) -> list[str]:
    """Build the compiler command with the version injected as a preprocessor define."""
    return [str(iscc), f"/DAppVersion={version}", str(INSTALLER_SCRIPT)]


def build_installer(*, iscc: Path | None = None, version: str | None = None) -> Path:
    """Compile and return the versioned Windows installer artifact."""
    resolved_version = version or project_version()
    if not (PAYLOAD / "SimpleStipple.exe").is_file():
        raise FileNotFoundError(f"PyInstaller payload is missing: {PAYLOAD / 'SimpleStipple.exe'}")

    compiler = iscc or find_iscc()
    subprocess.run(
        build_command(compiler, resolved_version),
        cwd=str(ROOT),
        check=True,
    )

    artifact = artifact_path(resolved_version)
    if not artifact.is_file():
        raise FileNotFoundError(f"Inno Setup did not produce the expected artifact: {artifact}")
    return artifact


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--iscc",
        type=Path,
        help="path to ISCC.exe (defaults to PATH and standard Inno Setup locations)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    artifact = build_installer(iscc=args.iscc)
    print(f"Built installer: {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
