"""Default settings for the Pattern page — edit here to change defaults.

Shared by ``tab.py`` (widget construction) and ``params.py`` (preset /
workspace restore fallbacks) so the two can never drift apart. String
defaults stay strings: they feed ``QLineEdit(default)`` and
``make_resettable_line_edit``, whose reset-to-default expects the exact
display text.

Per-pattern generator parameters (hex radius, flow spacing, …) live in
``_spec.PARAM_SPECS``, and Settings-dialog-backed defaults live in
``src.core.settings`` — neither is duplicated here.
"""

# ── Modifiers ─────────────────────────────────────────────────────────────
DEFAULT_PATTERN_ROTATION = "0"  # degrees
DEFAULT_BORDER_FADE = "0"  # mm; 0 = off
DEFAULT_DENSITY_STRENGTH = "0.75"  # 0..1
DEFAULT_DENSITY_ANGLE = "0"  # degrees
DEFAULT_DENSITY_MODE = "Uniform"

# ── Fill ──────────────────────────────────────────────────────────────────
DEFAULT_FILL_MODE = "none"  # none | lines | crosshatch
DEFAULT_FILL_SPACING = "0.5"  # mm
DEFAULT_FILL_ANGLE = "0"  # degrees
DEFAULT_FILL_INSET = "0"  # mm
FILL_SPACING_FLOOR_MM = 0.05  # hard lower clamp when parsing

# ── Export / fabrication ──────────────────────────────────────────────────
DEFAULT_MIN_SEGMENT = "0"  # mm; 0 disables
DEFAULT_MIN_ISLAND_AREA = "0"  # mm²; 0 disables
DEFAULT_PREVIEW_QUALITY = "balanced"  # fast | balanced | high

# ── Scale field bounds ────────────────────────────────────────────────────
SCALE_MIN_MM = 0.001
SCALE_MAX_MM = 1e9

# ── Canvas ────────────────────────────────────────────────────────────────
DEFAULT_GRID_VISIBLE = True
DEFAULT_GRID_SPACING_MM = 1.0

# ── Preview scheduling ────────────────────────────────────────────────────
PREVIEW_DEBOUNCE_MS = 100
