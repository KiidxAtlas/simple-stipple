"""JSON persistence helpers with atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

MAX_JSON_FILE_BYTES = 32 * 1024 * 1024
# Workspaces can legitimately contain hundreds of thousands of generated
# vertices. Keep the conservative default for settings/metadata while giving
# explicitly identified workspace documents a practical, still-bounded limit.
MAX_WORKSPACE_FILE_BYTES = 256 * 1024 * 1024


def read_json_file(
    path: str | Path,
    default: Any | None = None,
    *,
    max_bytes: int = MAX_JSON_FILE_BYTES,
) -> Any:
    """Read a UTF-8 JSON file or return a default value when missing."""
    file_path = Path(path)
    if not file_path.exists():
        return default
    try:
        size = file_path.stat().st_size
        if size > max_bytes:
            raise ValueError(
                f"{file_path.name} is too large to open safely "
                f"({size / (1024 * 1024):.1f} MB; limit {max_bytes / (1024 * 1024):.0f} MB)."
            )
        return json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Return the provided default on read/parse errors so callers can
        # handle missing/invalid files robustly.
        return default


def write_json_file_atomic(path: str | Path, payload: Any) -> None:
    """Write JSON atomically by replacing the destination with a temp file."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=file_path.name, dir=file_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, file_path)
    except (OSError, TypeError, ValueError):
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_via(path: str | Path, writer: Callable[[Path], None]) -> None:
    """Run ``writer`` against a temp path next to ``path`` then atomically swap.

    Use this for libraries (ezdxf, PIL) that take a path and write to it
    directly. ``writer`` receives a Path within the same directory so
    ``os.replace`` is atomic on every supported platform.
    """
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{file_path.name}.", suffix=".tmp", dir=file_path.parent
    )
    os.close(fd)
    tmp_path_p = Path(tmp_path)
    try:
        writer(tmp_path_p)
        os.replace(tmp_path_p, file_path)
    except Exception:
        try:
            tmp_path_p.unlink()
        except OSError:
            pass
        raise
