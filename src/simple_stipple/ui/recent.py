"""Persistence-backed recent-file lists."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from simple_stipple.platform.config import save_settings

DEFAULT_LIMIT = 12

KIND_DXF = "dxf"
KIND_FVI = "fvi"
KIND_IMAGE = "image"
KIND_VECTOR = "vector"


def _key(kind: str) -> str:
    return f"recent_files.{kind}"


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
    if key in settings:
        settings[key] = []
        save_settings(settings)


def prune_missing(settings: dict, kinds: Iterable[str] = (KIND_DXF, KIND_IMAGE)) -> None:
    """Drop entries that no longer exist on disk for the given *kinds*."""
    changed = False
    for kind in kinds:
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
