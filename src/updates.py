"""Update checking and download management for the app."""

from __future__ import annotations

import json
import logging
import platform
import re
import urllib.error
import urllib.request
from importlib import metadata
from pathlib import Path
from typing import NamedTuple

_LOG = logging.getLogger(__name__)

_REPO_OWNER = "KiidxAtlas"
_REPO_NAME = "simple-stipple"


def _read_version_from_pyproject() -> str:
    """Read version directly from pyproject.toml as a source fallback."""
    try:
        repo_root = Path(__file__).resolve().parents[1]
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
    try:
        return metadata.version(_REPO_NAME)
    except metadata.PackageNotFoundError:
        return _read_version_from_pyproject()
    except Exception:  # pragma: no cover - defensive fallback
        return _read_version_from_pyproject()


_CURRENT_VERSION = _detect_current_version()


class UpdateInfo(NamedTuple):
    """Information about an available update."""

    version: str
    url: str
    release_notes: str
    is_newer: bool


def get_current_version() -> str:
    """Get the currently installed app version."""
    return _CURRENT_VERSION


def get_releases_page_url() -> str:
    """Return the canonical GitHub releases URL for this repository."""
    return f"https://github.com/{_REPO_OWNER}/{_REPO_NAME}/releases"


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
        download_url = _get_download_url_for_platform(data.get("assets", []))

        if not download_url:
            _LOG.warning("No release asset found for platform %s", platform.system())
            return None

        return UpdateInfo(
            version=latest_version,
            url=download_url,
            release_notes=release_notes,
            is_newer=is_newer,
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


def _compare_versions(v1: str, v2: str) -> int:
    """Compare two semantic versions.

    Returns:
        > 0 if v1 > v2
        = 0 if v1 == v2
        < 0 if v1 < v2
    """

    def normalize(v: str) -> tuple[int, ...]:
        parts = v.split(".")
        try:
            return tuple(int(p) for p in parts)
        except ValueError:
            return tuple()

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
            name = asset.get("name", "").lower()
            if name.endswith(".exe"):
                return asset.get("browser_download_url")
    elif system == "Linux":
        for asset in assets:
            name = asset.get("name", "").lower()
            if "linux" in name or name.endswith(".tar.gz"):
                return asset.get("browser_download_url")

    return None


def download_update(url: str, dest_path: Path, timeout: int = 60) -> bool:
    """Download an update file.

    Args:
        url: Download URL.
        dest_path: Where to save the file.
        timeout: Request timeout in seconds.

    Returns:
        True if successful, False otherwise.
    """
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(
            url, headers={"User-Agent": "SimpleStipple/update-checker"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            with open(dest_path, "wb") as f:
                f.write(response.read())
        _LOG.info("Downloaded update to %s", dest_path)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        _LOG.error("Failed to download update: %s", exc)
        return False
