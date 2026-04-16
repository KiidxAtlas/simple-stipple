"""JSON persistence helpers with atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_json_file(path: str | Path, default: Any | None = None) -> Any:
    """Read a UTF-8 JSON file or return a default value when missing."""
    file_path = Path(path)
    if not file_path.exists():
        return {} if default is None else default
    return json.loads(file_path.read_text(encoding="utf-8"))


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