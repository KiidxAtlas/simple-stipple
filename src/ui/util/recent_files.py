"""Cross-tab recent-files MRU.

Tracks the most-recently-opened file paths *per kind* (e.g. ``"dxf"``,
``"image"``) inside the shared settings dict and persists them to disk via
:func:`src.settings.save_settings`.

Design notes:

* MRU lists are stored under ``settings["recent_files.<kind>"]``.  The legacy
  ``settings["recent_dxf"]`` key is migrated transparently the first time it is
  read so existing users keep their history.
* Lists are capped at :data:`DEFAULT_LIMIT` entries.  Duplicates collapse to
  the most-recent occurrence.
* :func:`list_recent` filters out paths that no longer exist on disk so the UI
  never offers a dead link.  The on-disk list is left intact; missing files
  reappear if the drive is re-mounted.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from src.settings import save_settings

DEFAULT_LIMIT = 12

KIND_DXF = "dxf"
KIND_IMAGE = "image"


def _key(kind: str) -> str:
    return f"recent_files.{kind}"


def _migrate_legacy(settings: dict, kind: str) -> None:
    """One-time migration of the pre-existing ``recent_dxf`` settings key."""
    if kind != KIND_DXF:
        return
    new_key = _key(kind)
    if new_key in settings:
        return
    legacy = settings.get("recent_dxf")
    if isinstance(legacy, list) and legacy:
        settings[new_key] = [p for p in legacy if isinstance(p, str)]


def _normalize(path: str) -> str:
    """Return a stable absolute path for dedupe purposes."""
    try:
        return str(Path(path).expanduser().resolve(strict=False))
    except (OSError, RuntimeError):
        return str(path)


def record_recent(
    settings: dict,
    kind: str,
    path: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> None:
    """Insert *path* at the head of the MRU list for *kind* and save."""
    if not path:
        return
    norm = _normalize(path)
    _migrate_legacy(settings, kind)
    key = _key(kind)
    current = settings.get(key)
    if not isinstance(current, list):
        current = []
    # Dedupe by normalized form while keeping the original-cased originals out.
    deduped: list[str] = [norm]
    seen = {norm}
    for entry in current:
        if not isinstance(entry, str) or not entry:
            continue
        n = _normalize(entry)
        if n in seen:
            continue
        seen.add(n)
        deduped.append(n)
        if len(deduped) >= max(1, limit):
            break
    if current == deduped:
        return
    settings[key] = deduped
    save_settings(settings)


def list_recent(
    settings: dict,
    kind: str,
    *,
    exists_only: bool = True,
    limit: int | None = None,
) -> list[str]:
    """Return the MRU list for *kind*, optionally filtering missing files."""
    _migrate_legacy(settings, kind)
    raw = settings.get(_key(kind))
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry:
            continue
        if exists_only and not Path(entry).exists():
            continue
        out.append(entry)
        if limit is not None and len(out) >= limit:
            break
    return out


def clear_recent(settings: dict, kind: str) -> None:
    """Wipe the MRU list for *kind*."""
    key = _key(kind)
    if key in settings or (kind == KIND_DXF and "recent_dxf" in settings):
        settings[key] = []
        if kind == KIND_DXF:
            settings.pop("recent_dxf", None)
        save_settings(settings)


def prune_missing(
    settings: dict, kinds: Iterable[str] = (KIND_DXF, KIND_IMAGE)
) -> None:
    """Drop entries that no longer exist on disk for the given *kinds*."""
    changed = False
    for kind in kinds:
        _migrate_legacy(settings, kind)
        key = _key(kind)
        raw = settings.get(key)
        if not isinstance(raw, list):
            continue
        kept = [p for p in raw if isinstance(p, str) and p and Path(p).exists()]
        if kept != raw:
            settings[key] = kept
            changed = True
    if changed:
        save_settings(settings)


__all__ = [
    "DEFAULT_LIMIT",
    "KIND_DXF",
    "KIND_IMAGE",
    "clear_recent",
    "list_recent",
    "prune_missing",
    "record_recent",
]
