"""Small persistence-backed UI helpers: recent-files MRU + file-dialog memory.

Merged from the former ``ui/util/recent_files.py`` and ``ui/util/dialog_paths.py``
— both were small, single-purpose modules and ``dialog_paths`` already
depended on ``recent_files``, so there was no reason for them to be two files.

── Recent-files MRU ──────────────────────────────────────────────────────────

Tracks the most-recently-opened file paths *per kind* (e.g. ``"dxf"``,
``"image"``) inside the shared settings dict and persists them to disk via
:func:`src.settings.save_settings`.

Design notes:

* MRU lists are stored under ``settings["recent_files.<kind>"]``.
* Lists are capped at :data:`DEFAULT_LIMIT` entries.  Duplicates collapse to
  the most-recent occurrence.
* :func:`list_recent` filters out paths that no longer exist on disk so the UI
  never offers a dead link.  The on-disk list is left intact; missing files
  reappear if the drive is re-mounted.

── File-dialog memory ────────────────────────────────────────────────────────

Wraps :class:`PySide6.QtWidgets.QFileDialog` so the *last folder* used for
each logical purpose (e.g. ``"pattern_output"``, ``"halftone_image"``) is
remembered in the shared settings dict and persisted to disk.

Conservative wrappers — they only:

* Read the saved directory from ``settings`` to seed the dialog.
* Save the directory of the chosen path back to ``settings`` on success.
* Call :func:`src.settings.save_settings` so the value survives a crash.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from collections import deque
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from PySide6.QtWidgets import QFileDialog

from src.infra.settings import save_settings

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

DEFAULT_LIMIT = 12

KIND_DXF = "dxf"
KIND_FVI = "fvi"
KIND_IMAGE = "image"
KIND_VECTOR = "vector"

UnitSystem = Literal["mm", "in"]
DEFAULT_UNIT_SYSTEM: UnitSystem = "mm"
_MM_PER_INCH = 25.4
_notification_history: deque[tuple[str, str]] = deque(maxlen=200)

_EXPR_OPS: dict[type[ast.operator | ast.unaryop], Callable[..., Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def parse_numeric_expression(text: str, unit: str = "mm", *, is_length: bool = True) -> float:
    """Evaluate a small arithmetic/unit expression and return mm for lengths.

    Supports ``12/2``, ``1in + 3mm``, parentheses, and decimal arithmetic;
    names, calls, attributes, and every other Python construct are rejected.
    Bare values use the active display unit.
    """
    source = str(text).strip().lower().replace("×", "*")
    if not source:
        raise ValueError("Enter a value")
    factor = _MM_PER_INCH if unit == "in" and is_length else 1.0
    if is_length:
        source = re.sub(
            r"(?<![\w.])(\d+(?:\.\d+)?|\.\d+)\s*(mm|in)\b",
            lambda m: str(
                float(m.group(1))
                * (_MM_PER_INCH if m.group(2) == "in" else 1.0)
                / factor
            ),
            source,
        )
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Invalid expression") from exc

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value) * factor
        if isinstance(node, ast.UnaryOp) and type(node.op) in _EXPR_OPS:
            return float(_EXPR_OPS[type(node.op)](_eval(node.operand)))
        if isinstance(node, ast.BinOp) and type(node.op) in _EXPR_OPS:
            left, right = _eval(node.left), _eval(node.right)
            # Multipliers/divisors are dimensionless when written bare.
            if is_length and isinstance(node.op, (ast.Mult, ast.Div, ast.Pow, ast.Mod)):
                right /= factor
            return float(_EXPR_OPS[type(node.op)](left, right))
        raise ValueError("Only arithmetic and mm/in units are supported")

    try:
        value = _eval(tree)
    except ArithmeticError as exc:
        raise ValueError("Invalid arithmetic") from exc
    if not math.isfinite(value):
        raise ValueError("Result must be finite")
    return value


def to_display(value_mm: float, unit: str) -> float:
    return value_mm / _MM_PER_INCH if unit == "in" else value_mm


def from_display(value: float, unit: str) -> float:
    return value * _MM_PER_INCH if unit == "in" else value


def suffix(unit: str) -> str:
    return "in" if unit == "in" else "mm"


def format_length(value_mm: float, unit: str, *, decimals: int = 2) -> str:
    return f"{to_display(value_mm, unit):.{decimals}f} {suffix(unit)}"


def record_notification(text: str) -> None:
    if text:
        _notification_history.append((datetime.now().strftime("%H:%M:%S"), str(text)))


def notification_history() -> list[tuple[str, str]]:
    return list(_notification_history)


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


def _settings_key(slot: str) -> str:
    """Return the canonical settings key used to remember a dialog directory."""
    return f"dialog_dir.{slot}"


def remembered_dir(settings: dict, slot: str, *, fallback: str = "") -> str:
    """Return the last-remembered directory for *slot*, or *fallback*."""
    value = settings.get(_settings_key(slot))
    if isinstance(value, str) and value:
        return value
    if isinstance(fallback, str) and fallback:
        return fallback
    return ""


def remember_dir(settings: dict, slot: str, path: str) -> None:
    """Persist the parent directory of *path* under *slot*."""
    if not path:
        return
    parent = str(Path(path).expanduser().parent)
    if not parent:
        return
    if settings.get(_settings_key(slot)) == parent:
        return
    settings[_settings_key(slot)] = parent
    save_settings(settings)


def pick_open_file(
    parent: QWidget | None,
    settings: dict,
    slot: str,
    caption: str,
    file_filter: str,
    *,
    fallback_dir: str = "",
    recent_kind: str | None = None,
) -> str:
    """Show an *open file* dialog seeded with the remembered directory.

    When *recent_kind* is provided, a successful pick is also pushed onto the
    matching recent-files MRU (see the recent-files functions above).
    """
    start = remembered_dir(settings, slot, fallback=fallback_dir)
    path, _ = QFileDialog.getOpenFileName(parent, caption, start, file_filter)
    if path:
        remember_dir(settings, slot, path)
        if recent_kind:
            record_recent(settings, recent_kind, path)
    return path


def pick_save_file(
    parent: QWidget | None,
    settings: dict,
    slot: str,
    caption: str,
    default_name: str,
    file_filter: str,
    *,
    fallback_dir: str = "",
) -> str:
    """Show a *save file* dialog seeded with the remembered directory.

    *default_name* is appended to the directory to pre-fill the filename field.
    """
    start_dir = remembered_dir(settings, slot, fallback=fallback_dir)
    if start_dir:
        seed = str(Path(start_dir) / default_name)
    else:
        seed = default_name
    path, _ = QFileDialog.getSaveFileName(parent, caption, seed, file_filter)
    if path:
        remember_dir(settings, slot, path)
    return path


def pick_directory(
    parent: QWidget | None,
    settings: dict,
    slot: str,
    caption: str,
    *,
    fallback_dir: str = "",
) -> str:
    """Show a *choose folder* dialog seeded with the remembered directory."""
    start = remembered_dir(settings, slot, fallback=fallback_dir)
    path = QFileDialog.getExistingDirectory(parent, caption, start)
    if path:
        # Folder picks remember themselves, not the parent.
        if settings.get(_settings_key(slot)) != path:
            settings[_settings_key(slot)] = path
            save_settings(settings)
    return path


__all__ = [
    "DEFAULT_LIMIT",
    "KIND_DXF",
    "KIND_FVI",
    "KIND_IMAGE",
    "KIND_VECTOR",
    "DEFAULT_UNIT_SYSTEM",
    "clear_recent",
    "list_recent",
    "pick_directory",
    "pick_open_file",
    "pick_save_file",
    "prune_missing",
    "record_recent",
    "record_notification",
    "notification_history",
    "to_display",
    "from_display",
    "suffix",
    "format_length",
    "remember_dir",
    "remembered_dir",
]
