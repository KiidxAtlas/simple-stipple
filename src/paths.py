"""Centralized user-data and config paths."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_APP_NAME = "simple-stipple"


def user_data_dir() -> Path:
    """Return per-user app data directory, creating it if needed."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / _APP_NAME
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / _APP_NAME
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = (Path(xdg) if xdg else Path.home() / ".local" / "share") / _APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def user_runtime_dir() -> Path:
    """Return per-user runtime directory (lockfiles, sockets)."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / _APP_NAME
    elif os.name == "nt":
        base = (
            Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / _APP_NAME
            / "runtime"
        )
    else:
        xdg = os.environ.get("XDG_RUNTIME_DIR")
        base = (
            Path(xdg) / _APP_NAME
            if xdg
            else Path.home() / ".cache" / _APP_NAME / "runtime"
        )
    base.mkdir(parents=True, exist_ok=True)
    return base


def user_log_dir() -> Path:
    """Return per-user log directory."""
    base = user_data_dir() / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base


def user_cache_dir() -> Path:
    """Return per-user cache directory."""
    base = user_data_dir() / "cache"
    base.mkdir(parents=True, exist_ok=True)
    return base


__all__ = ["user_cache_dir", "user_data_dir", "user_log_dir", "user_runtime_dir"]
