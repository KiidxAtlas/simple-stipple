"""Update checking and download management for the app."""

from __future__ import annotations

import json
import logging
import platform
import plistlib
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from importlib import metadata
from pathlib import Path
from typing import Any, NamedTuple, cast

_LOG = logging.getLogger(__name__)

_REPO_OWNER = "KiidxAtlas"
_REPO_NAME = "simple-stipple"
_SHA256_PATTERN = re.compile(r"\A([0-9a-fA-F]{64})(?:\s+[*]?(\S+))?\s*\Z")
_PACKAGED_FALLBACK_VERSION = "0.3.20"


def _read_version_from_pyproject() -> str:
    """Read version directly from pyproject.toml as a source fallback."""
    try:
        repo_root = Path(__file__).resolve().parents[3]
        pyproject = repo_root / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
        if match:
            return match.group(1).strip()
    except (OSError, ValueError):
        pass
    return "0.0.0"


def _detect_current_version() -> str:
    """Resolve current app version from package metadata or project source."""
    # In a source checkout (including editable installs), distribution
    # metadata can lag behind pyproject.toml after a version bump. Prefer the
    # checked-out project declaration so the updater does not offer the
    # version that was installed before the current release. Frozen builds do
    # not ship the repository file, so they fall back to package metadata.
    source_version = _read_version_from_pyproject()
    if source_version != "0.0.0":
        return source_version
    # macOS app bundles do not necessarily retain Python distribution
    # metadata. Their signed Info.plist is the authoritative release value.
    try:
        info = Path(sys.executable).resolve().parents[1] / "Info.plist"
        if info.is_file():
            value = plistlib.loads(info.read_bytes()).get("CFBundleShortVersionString")
            if isinstance(value, str) and value.strip() and value.strip() != "0.0.0":
                return value.strip()
    except (OSError, ValueError, plistlib.InvalidFileException):
        pass
    # Windows frozen apps carry the release version in the executable's
    # VersionInfo resource, not in an Info.plist or importlib metadata.
    windows_version = _read_windows_executable_version(Path(sys.executable))
    if windows_version:
        return windows_version
    try:
        return metadata.version(_REPO_NAME)
    except metadata.PackageNotFoundError:
        return _PACKAGED_FALLBACK_VERSION
    except Exception:  # defensive fallback
        return _PACKAGED_FALLBACK_VERSION


def _read_windows_executable_version(executable: Path) -> str | None:
    """Read ``FileVersion`` from a Windows EXE without a third-party module."""
    if platform.system() != "Windows" or executable.suffix.casefold() != ".exe":
        return None
    try:
        import ctypes

        winapi: Any = cast(Any, ctypes).windll
        size = winapi.version.GetFileVersionInfoSizeW(str(executable), None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not winapi.version.GetFileVersionInfoW(str(executable), 0, size, data):
            return None
        value = ctypes.c_void_p()
        value_len = ctypes.c_uint()
        if not winapi.version.VerQueryValueW(
            data,
            "\\StringFileInfo\\040904B0\\FileVersion",
            ctypes.byref(value),
            ctypes.byref(value_len),
        ):
            return None
        if value.value is None:
            return None
        text = ctypes.wstring_at(value.value, value_len.value).strip().rstrip("\x00")
        return text if text and text != "0.0.0" else None
    except (AttributeError, OSError, ValueError):
        return None


_CURRENT_VERSION = _detect_current_version()


class UpdateInfo(NamedTuple):
    """Information about an available update."""

    version: str
    url: str
    release_notes: str
    is_newer: bool
    sha256: str | None = None


def get_current_version() -> str:
    """Get the currently installed app version."""
    return _CURRENT_VERSION


def get_releases_page_url() -> str:
    """Return the canonical GitHub releases URL for this repository."""
    return f"https://github.com/{_REPO_OWNER}/{_REPO_NAME}/releases"


def can_install_update_windows() -> bool:
    """Return whether this process can launch the native Windows installer."""
    executable = Path(sys.executable)
    return (
        platform.system() == "Windows"
        and bool(getattr(sys, "frozen", False))
        and executable.suffix.casefold() == ".exe"
        and executable.is_file()
    )


def update_staging_path(version: str, system: str | None = None) -> Path:
    """Return a private temporary path for a downloaded update artifact."""
    safe_version = re.sub(r"[^0-9A-Za-z._-]+", "-", version)
    safe_version = re.sub(r"\.{2,}", ".", safe_version).strip("-.") or "update"
    current_system = system or platform.system()
    suffix = {
        "Windows": ".exe",
        "Darwin": ".dmg",
        "Linux": ".tar.gz",
    }.get(current_system, ".download")
    root = Path(tempfile.gettempdir()) / "simple-stipple-updates"
    if current_system == "Windows":
        return root / f"SimpleStipple-Setup-{safe_version}{suffix}"
    return root / f"SimpleStipple-{safe_version}{suffix}"


def launch_windows_installer(installer_path: Path) -> bool:
    """Launch a verified Inno Setup installer in a detached process.

    Inno Setup handles closing the running application and replacing the
    installed files. The caller is responsible for verifying the downloaded
    artifact before invoking this function.
    """
    installer_path = Path(installer_path)
    if (
        not can_install_update_windows()
        or installer_path.suffix.casefold() != ".exe"
        or not installer_path.is_file()
    ):
        return False

    creation_flags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    try:
        subprocess.Popen(
            [
                str(installer_path),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/CLOSEAPPLICATIONS",
            ],
            close_fds=True,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError):
        _LOG.exception("Could not launch the Windows installer")
        return False
    return True


def check_for_updates(timeout: int = 10) -> UpdateInfo | None:
    """Check GitHub releases for a newer version.

    Args:
        timeout: Request timeout in seconds.

    Returns:
        UpdateInfo if a newer version is available, None otherwise.
    """
    try:
        url = f"https://api.github.com/repos/{_REPO_OWNER}/{_REPO_NAME}/releases/latest"
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "SimpleStipple/update-checker")

        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode())

        latest_version = data.get("tag_name", "").lstrip("v")
        release_notes = (data.get("body") or "").strip()

        if not latest_version:
            _LOG.warning("No tag_name in release response")
            return None

        is_newer = _compare_versions(latest_version, _CURRENT_VERSION) > 0

        # Determine download URL based on platform
        assets = data.get("assets", [])
        download_url = _get_download_url_for_platform(assets)

        if not download_url:
            _LOG.warning("No release asset found for platform %s", platform.system())
            return None

        selected: dict[str, Any] = next(
            (asset for asset in assets if asset.get("browser_download_url") == download_url), {}
        )
        digest = str(selected.get("digest") or "")
        sha256 = (
            _normalize_sha256(digest.split(":", 1)[1])
            if digest.lower().startswith("sha256:")
            else None
        )
        if not sha256:
            sha256 = _sha256_from_sidecar(selected, assets, timeout)
        return UpdateInfo(
            version=latest_version,
            url=download_url,
            release_notes=release_notes,
            is_newer=is_newer,
            sha256=sha256,
        )
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        _LOG.warning("Network error checking for updates: %s", exc)
        return None
    except (json.JSONDecodeError, KeyError) as exc:
        _LOG.warning("Invalid response format from GitHub API: %s", exc)
        return None
    except (TypeError, ValueError) as exc:
        _LOG.error("Unexpected error checking for updates: %s", exc)
        return None


def _normalize_sha256(value: str) -> str | None:
    """Return a normalized SHA-256 hex digest, or None for malformed input."""
    normalized = value.strip().lower()
    return normalized if re.fullmatch(r"[0-9a-f]{64}", normalized) else None


def _sha256_from_sidecar(
    artifact: dict[str, Any], assets: list[dict[str, Any]], timeout: int
) -> str | None:
    """Fetch and validate the release checksum paired with an artifact."""
    artifact_name = str(artifact.get("name") or "")
    if not artifact_name:
        return None
    sidecar_name = f"{artifact_name}.sha256"
    sidecar_url = next(
        (
            str(asset.get("browser_download_url") or "")
            for asset in assets
            if str(asset.get("name") or "") == sidecar_name
        ),
        "",
    )
    if not sidecar_url:
        return None

    try:
        request = urllib.request.Request(
            sidecar_url,
            headers={"User-Agent": "SimpleStipple/update-checker"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read(1024).decode("ascii").strip()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, UnicodeError) as exc:
        _LOG.warning("Could not read update checksum sidecar: %s", exc)
        return None

    match = _SHA256_PATTERN.fullmatch(text)
    if not match:
        _LOG.warning("Invalid SHA-256 sidecar for %s", artifact_name)
        return None
    listed_name = match.group(2)
    if listed_name and Path(listed_name).name != artifact_name:
        _LOG.warning("SHA-256 sidecar names a different artifact: %s", listed_name)
        return None
    return _normalize_sha256(match.group(1))


def _compare_versions(v1: str, v2: str) -> int:
    """Compare two semantic versions.

    Returns:
        > 0 if v1 > v2
        = 0 if v1 == v2
        < 0 if v1 < v2
    """

    def normalize(v: str) -> tuple[int, ...]:
        # Strip leading 'v' and any pre-release/build suffix (PEP 440-ish).
        v = v.strip().lstrip("vV")
        for sep in ("-", "+"):
            if sep in v:
                v = v.split(sep, 1)[0]
        parts = v.split(".")
        out: list[int] = []
        for p in parts:
            try:
                out.append(int(p))
            except ValueError:
                # Stop at first non-numeric segment instead of returning ()
                # which would treat "1.2.3rc1" as equal to all others.
                break
        return tuple(out)

    v1_parts = normalize(v1)
    v2_parts = normalize(v2)

    # Pad with zeros
    max_len = max(len(v1_parts), len(v2_parts))
    v1_parts = v1_parts + (0,) * (max_len - len(v1_parts))
    v2_parts = v2_parts + (0,) * (max_len - len(v2_parts))

    if v1_parts > v2_parts:
        return 1
    elif v1_parts < v2_parts:
        return -1
    else:
        return 0


def _get_download_url_for_platform(assets: list[dict]) -> str | None:
    """Extract the appropriate download URL for the current platform."""
    system = platform.system()

    if system == "Darwin":  # macOS
        for asset in assets:
            name = asset.get("name", "").lower()
            if "macos" in name and name.endswith(".dmg"):
                return asset.get("browser_download_url")
    elif system == "Windows":
        for asset in assets:
            name = str(asset.get("name") or "").casefold()
            if name.startswith("simplestipple-setup-") and name.endswith(".exe"):
                return asset.get("browser_download_url")
    elif system == "Linux":
        for asset in assets:
            name = asset.get("name", "").lower()
            if "linux" in name or name.endswith(".tar.gz"):
                return asset.get("browser_download_url")

    return None


def _notify_download_progress(progress_cb, bytes_done: int, total: int | None) -> None:
    if progress_cb is None:
        return
    try:
        progress_cb(bytes_done, total)
    except Exception:  # noqa: BLE001 - progress is best-effort
        pass


def _remove_partial_download(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _checksum_matches(digest, expected_sha256: str | None) -> bool:
    if not expected_sha256:
        return True
    actual = digest.hexdigest().lower()
    expected = expected_sha256.strip().lower()
    if actual == expected:
        return True
    _LOG.error("Update sha256 mismatch: got %s expected %s", actual, expected)
    return False


def download_update(
    url: str,
    dest_path: Path,
    timeout: int = 60,
    *,
    expected_sha256: str | None = None,
    progress_cb=None,
) -> bool:
    """Stream-download an update file with optional SHA-256 verification.

    Args:
        url: Download URL.
        dest_path: Where to save the file.
        timeout: Per-read timeout in seconds.
        expected_sha256: If provided, the download is rejected when its
            SHA-256 digest does not match (case-insensitive).
        progress_cb: Optional callable ``(bytes_done, total_or_None)``.

    Returns:
        True if successful, False otherwise.
    """
    import hashlib
    import os
    import tempfile

    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "SimpleStipple/update-checker"})
        digest = hashlib.sha256()
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{dest_path.name}.", suffix=".part", dir=dest_path.parent
        )
        tmp_path = Path(tmp_name)
        bytes_done = 0
        total: int | None = None
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                length = response.headers.get("Content-Length")
                if length and length.isdigit():
                    total = int(length)
                with os.fdopen(fd, "wb") as f:
                    while chunk := response.read(64 * 1024):
                        f.write(chunk)
                        digest.update(chunk)
                        bytes_done += len(chunk)
                        _notify_download_progress(progress_cb, bytes_done, total)
            if not _checksum_matches(digest, expected_sha256):
                _remove_partial_download(tmp_path)
                return False
            os.replace(tmp_path, dest_path)
        except BaseException:
            _remove_partial_download(tmp_path)
            raise
        _LOG.info("Downloaded update to %s (%d bytes)", dest_path, bytes_done)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        _LOG.error("Failed to download update: %s", exc)
        return False
