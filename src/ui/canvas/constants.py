"""Canvas visual and interaction constants — single source of truth.

``SNAP_DIST``/``MIN_SCALE`` are re-exported from ``src.backend.cad.geometry``
(the actual single source of truth for those two) rather than duplicated
here, so the interactive canvas and the pure-Python snap-resolution logic in
``src.backend.cad.snapping`` can't silently drift apart.
"""

from PySide6.QtGui import QColor

from src.backend.cad.geometry import MIN_SCALE, SNAP_DIST

# ── Base canvas palette ───────────────────────────────────────────────────────
BG = "#0d1117"
POLY = "#4a9eff"
SEL = "#f47067"
DIM = "#8b949e"
Q_BG = QColor(BG)

# Minimum pointer travel before a press becomes a drag (pixels).
DRAG_THRESH = 5

# ── Vertex handle colors ──────────────────────────────────────────────────────
HANDLE = QColor("#4a9eff")  # vertex handle — matches poly accent
HANDLE_HOVER = QColor("#00c8aa")  # hover — teal
HANDLE_ACTIVE = QColor("#f5a623")  # active drag — amber

# ── Mode colors ───────────────────────────────────────────────────────────────
SNAP_CLOSE = QColor("#00c8aa")  # snap ring — teal
DRAW_COLOR = QColor("#f5a623")  # draw mode in-progress — amber
MEASURE_COLOR = QColor("#22d3ee")  # measure — cyan
SELECT_PT = QColor("#79c0ff")  # select-mode vertex indicator
SELECT_PT_ACTIVE = QColor("#f5a623")  # active drag vertex indicator

# ── Grid colors ───────────────────────────────────────────────────────────────
GRID_MINOR = QColor("#1a2432")
GRID_MAJOR = QColor("#243244")
GRID_AXIS = QColor("#31516e")

# ── Overlay colors ────────────────────────────────────────────────────────────
CONSTRUCTION_COLOR = QColor("#9933cc")
ORTHO_COLOR = QColor("#334466")
GUIDE_COLOR = QColor("#22d3ee")

# ── Badge colors ──────────────────────────────────────────────────────────────
BADGE_BG = QColor(20, 24, 36, 200)
BADGE_TEXT = QColor("#ffffff")
BADGE_DIM = QColor("#aabbcc")

# ── Hit / snap radii (pixels) ─────────────────────────────────────────────────
HANDLE_R = 4  # rendered handle radius
CLOSE_SNAP_DIST = 14  # same as snap so visual indicator matches click behavior
VERT_HIT = 8  # vertex click hit radius
EDGE_HIT = 6  # edge click hit radius

# ── Grid spacing bounds (mm) ──────────────────────────────────────────────────
# Shared clamp for every grid-spacing setter (precision bar, canvas commands).
GRID_SPACING_MIN_MM = 0.1
GRID_SPACING_MAX_MM = 100.0

# ── Line widths ───────────────────────────────────────────────────────────────
DRAW_VERT_R = 5  # vertex dot radius in draw mode
DRAW_LINE_W = 2.0  # placed segment line width
RUBBER_W = 1.5  # rubber-band line width

__all__ = [
    "BADGE_BG",
    "BADGE_DIM",
    "BADGE_TEXT",
    "CLOSE_SNAP_DIST",
    "CONSTRUCTION_COLOR",
    "DRAW_COLOR",
    "DRAW_LINE_W",
    "DRAW_VERT_R",
    "DRAG_THRESH",
    "BG",
    "DIM",
    "EDGE_HIT",
    "GRID_AXIS",
    "GRID_MAJOR",
    "GRID_MINOR",
    "GRID_SPACING_MAX_MM",
    "GRID_SPACING_MIN_MM",
    "GUIDE_COLOR",
    "HANDLE",
    "HANDLE_ACTIVE",
    "HANDLE_HOVER",
    "HANDLE_R",
    "MEASURE_COLOR",
    "MIN_SCALE",
    "ORTHO_COLOR",
    "POLY",
    "Q_BG",
    "RUBBER_W",
    "SELECT_PT",
    "SELECT_PT_ACTIVE",
    "SEL",
    "SNAP_CLOSE",
    "SNAP_DIST",
    "VERT_HIT",
]
