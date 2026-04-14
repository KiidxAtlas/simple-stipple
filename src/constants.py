"""Shared visual constants and list constants."""

from PySide6.QtGui import QColor

# ── Canvas colors ────────────────────────────────────────────────────────────
# (hex strings kept for convenience; QColor versions for Qt)
BG = "#0d1117"  # canvas background — deepest layer
POLY = "#4a9eff"  # polyline normal
SEL = "#f47067"  # polyline selected / danger accent
DIM = "#8b949e"  # muted labels / secondary text

Q_BG = QColor(BG)
Q_POLY = QColor(POLY)
Q_SEL = QColor(SEL)
Q_DIM = QColor(DIM)

# ── Semantic status colors (for setStyleSheet calls in tabs) ─────────────────
SUCCESS = "#3fb950"  # green — saved, done, ok
ERROR = "#f85149"  # red   — failed, error
WARN = "#d29922"  # amber — warning

# Interaction
DRAG_THRESH = 5  # pixels

# Pattern and shape option lists
PATTERNS = [
    "— None —",
    "Honeycomb",
    "Gradient Honeycomb",
    "Basketweave",
    "Fish Scale",
    "Stipple Dots",
    "Brick",
    "Diagonal Lines",
    "Square Grid",
    "Concentric Rings",
    "Wave Fill",
    "Sunburst",
    "Voronoi",
    "Penrose Tiling",
    "Topographic",
    "Image Halftone",
]

SHAPES = ["Rectangle", "Circle", "Ellipse", "Regular Polygon", "Slot"]
