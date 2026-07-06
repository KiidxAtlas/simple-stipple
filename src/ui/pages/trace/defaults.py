"""Persistent, user-configurable default values for new Trace sessions.

These are the values a freshly-opened image (or a cleared workspace)
starts with — e.g. "Max resolution" or "Simplify tolerance" — as opposed to
per-workspace saved state (src/ui/pages/trace/session.py), which restores
whatever was last used on a specific, already-traced workspace file.
Editable from the Settings dialog under "Trace Defaults"; stored in
settings["trace_defaults"] as the same string a QLineEdit holds.
"""

from __future__ import annotations

#: (settings key, display label, tooltip) for every editable numeric default.
TRACE_DEFAULT_FIELDS: tuple[tuple[str, str, str], ...] = (
    (
        "max_res",
        "Max resolution (px)",
        "Maximum pixel dimension when loading the image.",
    ),
    (
        "blur",
        "Blur radius",
        "Gaussian blur radius applied before thresholding / edge detection.",
    ),
    (
        "simplify",
        "Simplify tolerance",
        "Tolerance for polygon simplification (higher = fewer points).",
    ),
    ("min_area", "Min area (px²)", "Discard contours smaller than this area."),
    ("close_r", "Closing radius", "Morphological closing to fill small gaps in edges."),
    (
        "width_mm",
        "Default width (mm)",
        "Target output width in millimetres for a newly loaded image.",
    ),
)

#: Built-in fallback used when a key has never been set in trace_defaults.
TRACE_DEFAULTS: dict[str, str] = {
    "blur": "1.2",
    "threshold": "128",
    "canny_low": "50",
    "canny_high": "150",
    "simplify": "1.5",
    "min_area": "10",
    "max_area": "",
    "close_r": "1",
    "width_mm": "50.0",
    "max_res": "2200",
}


def trace_default(settings: dict | None, key: str) -> str:
    """Return the configured default for *key*, falling back to the
    built-in value if the user has never overridden it."""
    overrides = (settings or {}).get("trace_defaults") or {}
    if key in overrides:
        return str(overrides[key])
    return TRACE_DEFAULTS[key]
