"""Pattern preset persistence, import/export, and built-in starter packs.

Pattern presets capture the full state of the Pattern page's parameter form
(see :func:`src.ui.pages.pattern.params.collect_form_state`).  This module
provides serialization, file import/export with a versioned envelope, conflict
resolution helpers, and a curated set of built-in starter presets that ship
with the app.

Storage shape
-------------

Presets live under ``settings["pattern_presets"]`` as a plain mapping::

    {
        "My Honeycomb Fine": {"pattern": "Honeycomb", "hex_r": "1.2", ...},
        "Stipple Dense":      {"pattern": "Stipple Dots", "stip_r": "0.2", ...},
    }

Built-ins are seeded once on first run; the user may delete them and they will
not reappear (a sentinel flag tracks that seeding has happened).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

SCHEMA_ID = "simple-stipple-presets/v1"
SETTINGS_KEY = "pattern_presets"
SEEDED_FLAG = "pattern_presets_builtins_seeded"


# ---------------------------------------------------------------------------
# Built-in starter presets
# ---------------------------------------------------------------------------
#
# Keep these conservative: only a handful per pattern type, named so they show
# up at the top of the alphabetical combo box.  Field names mirror those
# produced by :func:`collect_form_state` (always strings — the form parses
# numbers lazily).

_DEFAULTS_COMMON: dict[str, str | bool] = {
    "rotation": "0",
    "scale_w": "",
    "scale_h": "",
    "ar_locked": True,
    "interlace": False,
    "invert_fill": False,
    "include_border": True,
    "border_fade": "0",
    "mirror_v": False,
    "mirror_h": False,
    "fill_mode": "none",
    "fill_spacing": "0.5",
    "fill_angle": "0",
    # UI default is to keep pattern strokes by default; presets should match.
    "fill_keep_outline": True,
    "fill_target_outline": False,
    "fill_target_pattern": True,
}


def _preset(pattern: str, **overrides: str | bool | float) -> dict:
    """Build a preset payload by merging *overrides* over the common defaults."""
    payload: dict = dict(_DEFAULTS_COMMON)
    payload["pattern"] = pattern
    for key, value in overrides.items():
        # collect_form_state stores strings for numeric line edits.
        if isinstance(value, bool):
            payload[key] = value
        else:
            payload[key] = str(value)
    return payload


BUILTIN_PRESETS: dict[str, dict] = {
    "★ Honeycomb — Fine": _preset("Honeycomb", hex_r="1.2", hex_gap="0.3"),
    "★ Honeycomb — Standard": _preset("Honeycomb", hex_r="2.5", hex_gap="0.5"),
    "★ Honeycomb — Bold": _preset("Honeycomb", hex_r="5.0", hex_gap="0.8"),
    "★ Stipple — Dense": _preset(
        "Stipple Dots", stip_r="0.2", stip_spacing="0.8", stip_layout=True
    ),
    "★ Stipple — Open": _preset(
        "Stipple Dots", stip_r="0.4", stip_spacing="2.5", stip_layout=False
    ),
    "★ Diagonal — Hatch": _preset(
        "Diagonal Lines", diag_spacing="1.5", diag_angle="45"
    ),
    "★ Diagonal — Cross": _preset(
        "Diagonal Lines", diag_spacing="2.0", diag_angle="30"
    ),
    "★ Brick — Standard": _preset("Brick", brick_w="20", brick_h="8", brick_gap="0.6"),
    "★ Wave — Gentle": _preset(
        "Wave Fill", wave_spacing="2.5", wave_amplitude="1.0", wave_wavelength="10"
    ),
    "★ Wave — Tight": _preset(
        "Wave Fill", wave_spacing="1.5", wave_amplitude="0.6", wave_wavelength="5"
    ),
    "★ Mesh — Light": _preset("Mesh", mesh_r="0.5", mesh_spacing="2.5"),
    "★ Voronoi — Cells": _preset(
        "Voronoi", vor_cells="120", vor_gap="0.4", vor_seed="42"
    ),
    "★ Hilbert — Order 5": _preset(
        "Hilbert Curve", hilbert_order="5", hilbert_margin="1.0"
    ),
}


# ---------------------------------------------------------------------------
# Validation / normalization
# ---------------------------------------------------------------------------


def _coerce_payload(payload: object) -> dict | None:
    """Return *payload* as a plain ``dict[str, ...]`` or ``None`` if invalid."""
    if not isinstance(payload, dict):
        return None
    out: dict = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            return None
        # Only allow JSON-friendly leaf types so we never store stray Qt objects.
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        else:
            # Skip unknown values rather than failing the whole preset.
            continue
    if "pattern" not in out:
        return None
    return out


def _coerce_preset_map(data: object) -> dict[str, dict]:
    """Best-effort coercion of an input mapping to ``{name: payload}``."""
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for name, payload in data.items():
        if not isinstance(name, str) or not name.strip():
            continue
        coerced = _coerce_payload(payload)
        if coerced is None:
            continue
        out[name.strip()] = coerced
    return out


# ---------------------------------------------------------------------------
# Serialization envelope
# ---------------------------------------------------------------------------


def serialize_presets(presets: dict[str, dict]) -> dict:
    """Wrap *presets* in a versioned envelope ready to be written to JSON."""
    return {
        "schema": SCHEMA_ID,
        "version": 1,
        "presets": {name: dict(payload) for name, payload in presets.items()},
    }


def deserialize_presets(data: object) -> dict[str, dict]:
    """Accept either a v1 envelope or a raw ``{name: payload}`` mapping."""
    if (
        isinstance(data, dict)
        and "presets" in data
        and isinstance(data["presets"], dict)
    ):
        return _coerce_preset_map(data["presets"])
    return _coerce_preset_map(data)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def export_to_file(presets: dict[str, dict], path: str | Path) -> None:
    """Write *presets* to *path* as a v1 envelope (pretty-printed JSON)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = serialize_presets(presets)
    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def import_from_file(path: str | Path) -> dict[str, dict]:
    """Load presets from *path*; raises :class:`ValueError` for empty/invalid."""
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not a valid JSON file: {exc}") from exc
    presets = deserialize_presets(data)
    if not presets:
        raise ValueError("File contains no recognizable presets.")
    return presets


# ---------------------------------------------------------------------------
# Merge / conflict resolution
# ---------------------------------------------------------------------------


def _unique_name(existing: Iterable[str], desired: str) -> str:
    """Return *desired*, suffixed ``" (2)"``, ``" (3)"``, … to avoid collisions."""
    used = set(existing)
    if desired not in used:
        return desired
    n = 2
    while True:
        candidate = f"{desired} ({n})"
        if candidate not in used:
            return candidate
        n += 1


def merge_presets(
    existing: dict[str, dict],
    incoming: dict[str, dict],
    *,
    strategy: str = "rename",
) -> tuple[dict[str, dict], dict[str, int]]:
    """Merge *incoming* presets into *existing* using *strategy*.

    Strategies:

    * ``"rename"``  — keep both; collisions get a ``" (2)"`` suffix.
    * ``"overwrite"`` — incoming wins on collision.
    * ``"skip"``    — incoming is dropped on collision.

    Returns ``(merged, summary)`` where ``summary`` counts ``added``,
    ``replaced``, ``skipped``, ``renamed``.
    """
    if strategy not in {"rename", "overwrite", "skip"}:
        raise ValueError(f"Unknown merge strategy: {strategy!r}")
    merged = {name: dict(payload) for name, payload in existing.items()}
    summary = {"added": 0, "replaced": 0, "skipped": 0, "renamed": 0}
    for name, payload in incoming.items():
        if name not in merged:
            merged[name] = dict(payload)
            summary["added"] += 1
            continue
        if strategy == "overwrite":
            merged[name] = dict(payload)
            summary["replaced"] += 1
        elif strategy == "skip":
            summary["skipped"] += 1
        else:  # rename
            new_name = _unique_name(merged.keys(), name)
            merged[new_name] = dict(payload)
            summary["renamed"] += 1
    return merged, summary


# ---------------------------------------------------------------------------
# Built-in seeding
# ---------------------------------------------------------------------------


def ensure_builtins_seeded(
    settings: dict,
    presets: dict[str, dict],
) -> dict[str, dict]:
    """Add :data:`BUILTIN_PRESETS` to *presets* once, tracked in *settings*.

    Subsequent calls are no-ops so that user deletions are respected.  The
    caller is responsible for persisting *settings* afterwards.
    """
    if settings.get(SEEDED_FLAG):
        return presets
    merged, _ = merge_presets(presets, BUILTIN_PRESETS, strategy="skip")
    settings[SEEDED_FLAG] = True
    return merged


def reset_to_builtins(presets: dict[str, dict]) -> dict[str, dict]:
    """Re-add any missing built-ins; never deletes user presets."""
    merged, _ = merge_presets(presets, BUILTIN_PRESETS, strategy="skip")
    return merged


__all__ = [
    "BUILTIN_PRESETS",
    "SCHEMA_ID",
    "SEEDED_FLAG",
    "SETTINGS_KEY",
    "deserialize_presets",
    "ensure_builtins_seeded",
    "export_to_file",
    "import_from_file",
    "merge_presets",
    "reset_to_builtins",
    "serialize_presets",
]
