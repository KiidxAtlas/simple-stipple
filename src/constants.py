"""Shared visual constants and list constants."""

from PySide6.QtGui import QColor

# ── Canvas colors ────────────────────────────────────────────────────────────
# (hex strings kept for convenience; QColor versions for Qt)
BG = "#0d1117"  # canvas background — deepest layer
POLY = "#4a9eff"  # polyline normal
SEL = "#f47067"  # polyline selected / danger accent
DIM = "#8b949e"  # muted labels / secondary text

Q_BG = QColor(BG)

# Interaction
DRAG_THRESH = 5  # pixels

# Pattern and shape option lists
PATTERNS = [
    "— None —",
    "Basketweave",
    "Brick",
    "Celtic Knot",
    "Concentric Rings",
    "Gradient Honeycomb",
    "Hilbert Curve",
    "Honeycomb",
    "Image Halftone",
    "Mesh",
    "Penrose Tiling",
    "Reaction Diffuse",
    "Stipple Dots",
    "Sunburst",
    "Square Grid",
    "Topographic",
    "Voronoi",
]
