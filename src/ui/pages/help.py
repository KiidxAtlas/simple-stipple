from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

LOGGER = logging.getLogger(__name__)


# ── Help content (Markdown-like, rendered as rich HTML) ───────────────────────

HELP_HTML = """
<h1 style="text-align:center; color: var(--heading-color);">Simple Stipple — User Manual</h1>
<p style="text-align:center; color: var(--text-color);">A powerful laser-engraving pattern generator for DXF files.</p>

<hr style="border: none; border-top: 2px solid var(--accent-color); margin: 20px 0;">

<!-- ═══════════════════ GETTING STARTED ═══════════════════ -->
<h2 id="getting-started" style="color: var(--accent-color); border-bottom: 1px solid var(--border-color); padding-bottom: 8px;">
    🚀 Getting Started
</h2>

<h3 style="color: var(--subheading-color);">What is Simple Stipple?</h3>
<p>Simple Stipple is a desktop application that generates intricate laser-engraving patterns inside user-defined outlines. You draw or import an outline, choose a pattern and its parameters, and the software fills the area with mathematically precise vector geometry ready for your laser cutter.</p>

<h3 style="color: var(--subheading-color);">System Requirements</h3>
<ul>
    <li><strong>Python 3.10+</strong> (if running from source)</li>
    <li><strong>Shapely</strong> for computational geometry operations</li>
    <li><strong>PySide6</strong> (Qt6 bindings) for the graphical interface</li>
    <li>A laser cutter that accepts DXF output (most modern controllers do)</li>
</ul>

<h3 style="color: var(--subheading-color);">Quick Start — Your First Pattern</h3>
<ol>
    <li><strong>Open the Pattern page</strong> from the app's navigation sidebar.</li>
    <li><strong>Load an outline</strong> — either draw one using the drawing tools (press <kbd>D</kbd>) or import an existing DXF file via the file picker.</li>
    <li><strong>Select a pattern</strong> from the dropdown (e.g., "Honeycomb").</li>
    <li><strong>Adjust parameters</strong> — size, gap, spacing — using the sidebar controls.</li>
    <li><strong>Click "Generate"</strong> to create the pattern. Use the preview toggle to compare before/after.</li>
    <li><strong>Export</strong> the result as a DXF file for your laser cutter.</li>
</ol>


<!-- ═══════════════════ PATTERN PAGE ═══════════════════ -->
<h2 id="pattern-page" style="color: var(--accent-color); border-bottom: 1px solid var(--border-color); padding-bottom: 8px;">
    📐 Pattern Page — Full Reference
</h2>

<h3 style="color: var(--subheading-color);">Overview</h3>
<p>The Pattern page is the heart of Simple Stipple. It provides a split-view interface: parameter controls on the left, an interactive canvas with your pattern preview on the right.</p>

<h4 style="color: var(--panel-title-color);">Left Panel — Controls</h4>
<ul>
    <li><strong>DXF File Picker:</strong> Load an existing DXF file containing your outline geometry.</li>
    <li><strong>Pattern Type Dropdown:</strong> Choose from 20+ built-in pattern generators.</li>
    <li><strong>Parameter Controls:</strong> Dynamic widgets that change based on the selected pattern (size, gap, spacing, etc.).</li>
    <li><strong>Fill Mode:</strong> Choose how to fill the pattern area — None, Lines (parallel hatch), or Crosshatch (crossed ±45° lines).</li>
    <li><strong>Presets Manager:</strong> Save, load, and manage pattern presets for quick recall.</li>
    <li><strong>Tile Library:</strong> Load pre-made tile patterns from DXF files for repeating tiling effects.</li>
    <li><strong>Halftone Controls:</strong> Generate image-based halftone patterns from imported images.</li>
    <li><strong>Generate / Preview Buttons:</strong> Generate the full pattern or show a live preview.</li>
    <li><strong>Export Button:</strong> Save the result as a DXF file.</li>
</ul>

<h4 style="color: var(--panel-title-color);">Right Panel — Canvas</h4>
<ul>
    <li><strong>Interactive View:</strong> Zoom, pan, and inspect your pattern in real time.</li>
    <li><strong>Layer Tree:</strong> Toggle visibility of outline, pattern, and fill layers.</li>
    <li><strong>Grid:</strong> Toggle a measurement grid for precision work.</li>
    <li><strong>Status Strip:</strong> Shows current dimensions, pattern count, and tool status.</li>
</ul>


<!-- ═══════════════════ PATTERN TYPES ═══════════════════ -->
<h2 id="pattern-types" style="color: var(--accent-color); border-bottom: 1px solid var(--border-color); padding-bottom: 8px;">
    🎨 Pattern Types — Complete Guide
</h2>

<p>Simple Stipple includes <strong>20+ pattern generators</strong>, each with unique characteristics. Below is a complete reference.</p>

<h3 style="color: var(--subheading-color);">Geometric Patterns</h3>

<h4 style="color: var(--panel-title-color);">Honeycomb</h4>
<p>A classic hexagonal grid pattern, ideal for structural engraving and decorative surfaces.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Hex size (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Radius of each hexagonal cell (default: 1.75 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Gap (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Spacing between adjacent hexagons (default: 0.5 mm)</td></tr>
</table>

<h4 style="color: var(--panel-title-color);">Gradient Honeycomb</h4>
<p>A hexagonal grid where cell sizes gradually change across the pattern, creating a flowing gradient effect.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Min size (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Smallest hex cell size (default: 0.8 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Max size (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Largest hex cell size (default: 2.5 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Gap (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Spacing between hexagons (default: 0.5 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Direction (°)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Gradient direction — 0° = left to right, 90° = vertical (default: 0°)</td></tr>
</table>

<h4 style="color: var(--panel-title-color);">Brick</h4>
<p>A classic brickwork pattern with staggered rows, perfect for wall and floor engraving.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Brick width (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Width of each brick (default: 4.0 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Brick height (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Height of each brick (default: 2.0 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Gap (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Mortar gap between bricks (default: 0.5 mm)</td></tr>
</table>

<h4 style="color: var(--panel-title-color);">Square Grid</h4>
<p>A simple orthogonal grid pattern. Clean, minimal, and versatile for any application.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Grid spacing (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Distance between grid lines (default: 1.0 mm)</td></tr>
</table>

<h4 style="color: var(--panel-title-color);">Mesh</h4>
<p>A grid of circles arranged in a regular pattern, creating an elegant mesh effect.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Circle radius (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Radius of each circle (default: 0.35 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Grid spacing (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Centre-to-centre distance (default: 1.2 mm)</td></tr>
</table>

<h4 style="color: var(--panel-title-color);">Concentric Rings</h4>
<p>Series of concentric circles or polygons offset inward from the outline boundary.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Ring spacing (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Distance between successive rings (default: 1.5 mm)</td></tr>
</table>

<h3 style="color: var(--subheading-color);">Organic / Curved Patterns</h3>

<h4 style="color: var(--panel-title-color);">Fish Scale</h4>
<p>Overlapping arc segments that mimic the appearance of fish scales. A decorative classic.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Scale width (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Horizontal span of each scale (default: 3.0 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Scale height (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Vertical height of each scale (default: 2.0 mm)</td></tr>
</table>

<h4 style="color: var(--panel-title-color);">Wave Fill</h4>
<p>Sine-wave rows that create a flowing, organic texture.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Row spacing (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Vertical distance between wave rows (default: 1.5 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Amplitude (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Peak-to-centre height (default: 0.5 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Wavelength (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Horizontal length of one full wave cycle (default: 3.0 mm)</td></tr>
</table>

<h4 style="color: var(--panel-title-color);">Sunburst</h4>
<p>Radiating lines from a central point, creating a dramatic sunburst effect.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Spoke spacing (°)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Angular spacing — 5° gives ~36 spokes, 10° gives ~18 (default: 5.0°)</td></tr>
</table>

<h4 style="color: var(--panel-title-color);">Topographic</h4>
<p>Inward-offset contour lines that simulate topographic map elevation lines.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Contour spacing (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Distance between successive contours (default: 1.5 mm)</td></tr>
</table>

<h3 style="color: var(--subheading-color);">Mathematical / Fractal Patterns</h3>

<h4 style="color: var(--panel-title-color);">Hilbert Curve</h4>
<p>A space-filling fractal curve. Higher orders produce denser, more intricate paths.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Order</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Recursion depth, 1–8 (default: 5). Higher = denser.</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Margin (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Inset from outline bounds (default: 1.0 mm)</td></tr>
</table>

<h4 style="color: var(--panel-title-color);">Reaction Diffusion</h4>
<p>Simulates the Gray-Scott reaction-diffusion model, producing organic labyrinthine patterns.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Preset</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">labyrinth, spots, stripes, or maze (default: labyrinth)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Cell (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Simulation grid cell size (default: 0.8 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Iterations</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Simulation steps (default: 1200)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Threshold</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Contour extraction threshold, 0–1 (default: 0.22)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Seed</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Random seed for reproducibility (default: 42)</td></tr>
</table>

<h4 style="color: var(--panel-title-color);">Voronoi</h4>
<p>A Voronoi diagram partitioning the outline into irregular polygonal cells.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Cell count</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Number of random cells (default: 60)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Gap (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Inset distance between cells (default: 0.15 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Seed</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Random seed (default: 42)</td></tr>
</table>

<h4 style="color: var(--panel-title-color);">Penrose Tiling</h4>
<p>An aperiodic kite-and-dart tiling (P2 symmetry) that never repeats — beautiful for artistic engraving.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Tile size (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Approximate tile size (default: 3.0 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Gap (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Spacing between tiles (default: 0.1 mm)</td></tr>
</table>

<h3 style="color: var(--subheading-color);">Decorative / Woven Patterns</h3>

<h4 style="color: var(--panel-title-color);">Basketweave</h4>
<p>A woven pattern of horizontal and vertical strips, creating an interlaced basket effect.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Strip width (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Width of each woven strip (default: 2.0 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Strip length (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Length of each woven strip (default: 8.0 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Gap (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Gap between strips (default: 0.2 mm)</td></tr>
</table>

<h4 style="color: var(--panel-title-color);">Braid</h4>
<p>Interlocking diagonal strips creating a braid or weave effect at ±45° angles.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Strip width (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Width of each diagonal strip (default: 2.0 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Spacing (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Gap between parallel strips (default: 3.0 mm)</td></tr>
</table>

<h4 style="color: var(--panel-title-color);">Stipple Dots</h4>
<p>Poisson-disk distributed circles creating a stippled, pointillist effect.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Dot radius (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Radius of each dot (default: 0.4 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Spacing (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Centre-to-centre distance (default: 1.2 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Interlaced</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Use offset grid instead of Poisson-disk (checkbox)</td></tr>
</table>

<h4 style="color: var(--panel-title-color);">Diagonal Lines</h4>
<p>Simple parallel diagonal lines at a configurable angle.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Line spacing (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Distance between lines (default: 1.0 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Angle (°)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Angle of the lines (default: 45°)</td></tr>
</table>

<h3 style="color: var(--subheading-color);">Advanced / Artistic Patterns</h3>

<h4 style="color: var(--panel-title-color);">Celtic Knot</h4>
<p>A grid-based Celtic knot pattern with over-under crossings at intersections.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Cell size (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Grid cell size (default: 5.0 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Line width (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Width of the knot band (default: 1.0 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Gap (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Gap at crossings for over-under illusion (default: 0.2 mm)</td></tr>
</table>

<h4 style="color: var(--panel-title-color);">Lissajous</h4>
<p>Lissajous curves repeated in rows, creating complex interference patterns.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Freq X / Freq Y</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Horizontal and vertical frequencies (default: 3, 2)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Row spacing (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Vertical offset between curves (default: 2.0 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Amplitude (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Peak amplitude (default: 5.0 mm)</td></tr>
</table>

<h4 style="color: var(--panel-title-color);">Golden Spiral</h4>
<p>A logarithmic spiral based on the golden ratio (φ).</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Turns</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Number of spiral turns (default: 4.5)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Spacing hint (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Controls point density (default: 1.5 mm)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Direction</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">ccw (counter-clockwise) or cw (clockwise)</td></tr>
</table>

<h4 style="color: var(--panel-title-color);">Rose Curve</h4>
<p>Rhodonea rose curves — mathematical flower-like patterns.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Petals</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Number of rose petals (default: 7)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Copies</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Overlay count with phase offsets (default: 2)</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Margin (mm)</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Inset from outline bounds (default: 1.0 mm)</td></tr>
</table>


<!-- ═══════════════════ FILL MODES ═══════════════════ -->
<h2 id="fill-modes" style="color: var(--accent-color); border-bottom: 1px solid var(--border-color); padding-bottom: 8px;">
    🖌️ Fill Modes — Laser Infill Reference
</h2>

<p>Fill modes determine how the interior of your pattern is filled with laser paths. They work independently of (or alongside) the main pattern type.</p>

<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr style="background-color: var(--accent-bg);">
        <th style="padding:8px; text-align:left; border-bottom:2px solid var(--accent-color);">Mode</th>
        <th style="padding:8px; text-align:left; border-bottom:2px solid var(--accent-color);">Description</th>
    </tr>
    <tr><td style="padding:8px; border-bottom:1px solid var(--border-color);"><strong>None</strong></td><td style="padding:8px; border-bottom:1px solid var(--border-color);">No fill pattern. Only the main pattern is generated.</td></tr>
    <tr><td style="padding:8px; border-bottom:1px solid var(--border-color);"><strong>Lines</strong></td><td style="padding:8px; border-bottom:1px solid var(--border-color);">Parallel hatch lines across the fill region. Configurable spacing and angle.</td></tr>
    <tr><td style="padding:8px; border-bottom:1px solid var(--border-color);"><strong>Crosshatch</strong></td><td style="padding:8px; border-bottom:1px solid var(--border-color);">Two sets of parallel lines at ±45° angles, creating a crossed-hatch grid. Ideal for maximum surface coverage.</td></tr>
</table>

<h4 style="color: var(--panel-title-color);">FillSpec Parameters</h4>
<ul>
    <li><strong>mode:</strong> "none", "lines", or "crosshatch"</li>
    <li><strong>spacing:</strong> Distance between fill lines (mm)</li>
    <li><strong>angle_deg:</strong> Angle of the hatch lines in degrees</li>
    <li><strong>keep_pattern:</strong> Whether to keep the main pattern when applying fill</li>
    <li><strong>inset:</strong> Inset distance from the outline edge (mm)</li>
</ul>


<!-- ═══════════════════ ZONES & EXCLUSIONS ═══════════════════ -->
<h2 id="zones-exclusions" style="color: var(--accent-color); border-bottom: 1px solid var(--border-color); padding-bottom: 8px;">
    🗂️ Zones &amp; Exclusions — Multi-Pattern Workflows
</h2>

<h3 style="color: var(--subheading-color);">Zones</h3>
<p>Zones allow you to assign different patterns to different regions within the same outline. Each zone is a snapshot containing:</p>
<ul>
    <li>A set of outline IDs (the region boundaries)</li>
    <li>A pattern type</li>
    <li>Pattern parameters</li>
    <li>An optional scale (width, height)</li>
    <li>A label for identification</li>
</ul>
<p>This is useful when you need different patterns in different areas of the same engraving — for example, a honeycomb pattern on one side and diagonal lines on another.</p>

<h3 style="color: var(--subheading-color);">Exclusion Cutouts</h3>
<p>Exclusion cutouts are outline IDs that the pattern will <em>avoid</em>. They act as "holes" in your pattern fill. Use this when you want patterns to flow around specific features (e.g., lettering, logos, or mechanical cutouts).</p>


<!-- ═══════════════════ HALFTONE & TILE LIBRARY ═══════════════════ -->
<h2 id="halftone-tiles" style="color: var(--accent-color); border-bottom: 1px solid var(--border-color); padding-bottom: 8px;">
    🖼️ Halftone &amp; Tile Library
</h2>

<h3 style="color: var(--subheading-color);">Halftone Patterns</h3>
<p>Generate patterns from images using halftoning — converting pixel brightness into varying dot sizes or line densities.</p>
<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Cell min</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Minimum cell/dot size</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Cell max</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Maximum cell/dot size</td></tr>
    <tr><td style="padding:4px; border-bottom:1px solid var(--border-color);"><strong>Spacing</strong></td><td style="padding:4px; border-bottom:1px solid var(--border-color);">Distance between halftone cells</td></tr>
</table>

<h3 style="color: var(--subheading-color);">Tile Library</h3>
<p>Load pre-made tile patterns from DXF files stored in a designated folder. These tiles can be used for repeating tiling effects across your pattern area.</p>


<!-- ═══════════════════ PRESETS ═══════════════════ -->
<h2 id="presets" style="color: var(--accent-color); border-bottom: 1px solid var(--border-color); padding-bottom: 8px;">
    💾 Preset System — Save &amp; Share Your Settings
</h2>

<h3 style="color: var(--subheading-color);">Built-in Presets</h3>
<p>Simple Stipple ships with starter presets to help you get started quickly:</p>
<ul>
    <li><strong>Honeycomb — Fine / Standard / Bold:</strong> Three density variants of the hexagonal pattern</li>
    <li><strong>Stipple — Dense / Open:</strong> Two dot-density variants</li>
    <li><strong>Diagonal Lines — Hatch / Cross:</strong> Single-angle and crossed variants</li>
    <li><strong>Brick, Wave — Gentle / Tight:</strong> Various density settings</li>
    <li><strong>Mesh, Voronoi, Hilbert Curve:</strong> Single presets for each pattern type</li>
</ul>

<h3 style="color: var(--subheading-color);">Custom Presets</h3>
<p>Save your own presets with a single click. Each preset stores:</p>
<ul>
    <li>The selected pattern type</li>
    <li>All parameter values (size, gap, spacing, etc.)</li>
    <li>Fill mode and parameters</li>
    <li>A custom name and optional description</li>
</ul>

<h4 style="color: var(--panel-title-color);">Managing Presets</h4>
<ol>
    <li>Configure your desired pattern and parameters.</li>
    <li>Click the <strong>Presets Manager</strong> button (icon looks like a bookmark).</li>
    <li>Click <strong>"Save Current as Preset"</strong>, give it a name, and confirm.</li>
    <li>To load: select the preset from the dropdown and click <strong>"Load"</strong>.</li>
    <li>To export: use the Export button to save presets as a JSON file (<code>simple-stipple-presets/v1</code> format).</li>
    <li>To import: use the Import button to load a previously exported presets file.</li>
</ol>


<!-- ═══════════════════ CANVAS TOOLS ═══════════════════ -->
<h2 id="canvas-tools" style="color: var(--accent-color); border-bottom: 1px solid var(--border-color); padding-bottom: 8px;">
    🖱️ Canvas Tools &amp; Navigation
</h2>

<h3 style="color: var(--subheading-color);">Drawing Tools (Press <kbd>D</kbd>)</h3>
<ul>
    <li><strong>Select (S):</strong> Click and drag to select entities on the canvas.</li>
    <li><strong>Draw (D):</strong> Click to place vertices and draw new outline shapes.</li>
    <li><strong>Edit (E):</strong> Move, resize, or modify existing entities.</li>
</ul>

<h3 style="color: var(--subheading-color);">Navigation</h3>
<ul>
    <li><strong>Fit View (F):</strong> Zoom to fit all content in the viewport.</li>
    <li><strong>Scroll Wheel:</strong> Zoom in and out.</li>
    <li><strong>Middle Mouse / Space+Drag:</strong> Pan the view.</li>
</ul>

<h3 style="color: var(--subheading-color);">Layer Tree</h3>
<p>The layer tree panel shows all entities organized by category:</p>
<ul>
    <li><strong>Outline layer:</strong> Your boundary geometry</li>
    <li><strong>Pattern layer:</strong> The generated pattern paths</li>
    <li><strong>Fill layer:</strong> Any fill/hatch patterns applied</li>
</ul>


<!-- ═══════════════════ KEYBOARD SHORTCUTS ═══════════════════ -->
<h2 id="keyboard-shortcuts" style="color: var(--accent-color); border-bottom: 1px solid var(--border-color); padding-bottom: 8px;">
    ⌨️ Keyboard Shortcuts
</h2>

<table style="width:100%; border-collapse: collapse; margin: 10px 0;">
    <tr style="background-color: var(--accent-bg);">
        <th style="padding:8px; text-align:left; border-bottom:2px solid var(--accent-color);">Shortcut</th>
        <th style="padding:8px; text-align:left; border-bottom:2px solid var(--accent-color);">Action</th>
    </tr>
    <tr><td style="padding:8px; border-bottom:1px solid var(--border-color);"><kbd>S</kbd></td><td style="padding:8px; border-bottom:1px solid var(--border-color);">Switch to Select mode</td></tr>
    <tr><td style="padding:8px; border-bottom:1px solid var(--border-color);"><kbd>D</kbd></td><td style="padding:8px; border-bottom:1px solid var(--border-color);">Switch to Draw mode</td></tr>
    <tr><td style="padding:8px; border-bottom:1px solid var(--border-color);"><kbd>E</kbd></td><td style="padding:8px; border-bottom:1px solid var(--border-color);">Switch to Edit mode</td></tr>
    <tr><td style="padding:8px; border-bottom:1px solid var(--border-color);"><kbd>F</kbd></td><td style="padding:8px; border-bottom:1px solid var(--border-color);">Fit view to all content</td></tr>
    <tr><td style="padding:8px; border-bottom:1px solid var(--border-color);"><kbd>Ctrl+Z</kbd></td><td style="padding:8px; border-bottom:1px solid var(--border-color);">Undo last action</td></tr>
    <tr><td style="padding:8px; border-bottom:1px solid var(--border-color);"><kbd>Ctrl+A</kbd></td><td style="padding:8px; border-bottom:1px solid var(--border-color);">Select all entities</td></tr>
    <tr><td style="padding:8px; border-bottom:1px solid var(--border-color);"><kbd>Shift+Ctrl+A</kbd></td><td style="padding:8px; border-bottom:1px solid var(--border-color);">Deselect all</td></tr>
    <tr><td style="padding:8px; border-bottom:1px solid var(--border-color);"><kbd>Delete</kbd></td><td style="padding:8px; border-bottom:1px solid var(--border-color);">Delete selected entities</td></tr>
    <tr><td style="padding:8px; border-bottom:1px solid var(--border-color);"><kbd>Shift+C</kbd></td><td style="padding:8px; border-bottom:1px solid var(--border-color);">Close current outline</td></tr>
    <tr><td style="padding:8px; border-bottom:1px solid var(--border-color);"><kbd>Shift+O</kbd></td><td style="padding:8px; border-bottom:1px solid var(--border-color);">Open new outline</td></tr>
</table>

<p><em>To view or customize all keyboard shortcuts, open the Keybindings dialog from the Settings menu.</em></p>


<!-- ═══════════════════ WORKSPACE STATE ═══════════════════ -->
<h2 id="workspace-state" style="color: var(--accent-color); border-bottom: 1px solid var(--border-color); padding-bottom: 8px;">
    💼 Workspace State &amp; Persistence
</h2>

<p>Simple Stipple automatically saves your workspace state, including:</p>
<ul>
    <li>The loaded DXF file path</li>
    <li>All parameter values for the current pattern</li>
    <li>Your outline and generated geometry</li>
    <li>Canvas view position and zoom level</li>
    <li>Preview state (whether preview was shown)</li>
    <li>All zone assignments and exclusion cutouts</li>
</ul>

<p>This means you can close the app and resume exactly where you left off when you reopen it.</p>


<!-- ═══════════════════ TROUBLESHOOTING ═══════════════════ -->
<h2 id="troubleshooting" style="color: var(--accent-color); border-bottom: 1px solid var(--border-color); padding-bottom: 8px;">
    🔧 Troubleshooting &amp; Tips
</h2>

<h3 style="color: var(--subheading-color);">Common Issues</h3>

<h4 style="color: var(--panel-title-color);">Pattern generation is slow</h4>
<p>Complex patterns (especially Reaction Diffusion, Hilbert Curve at high orders, and Voronoi with many cells) can take time. Try:</p>
<ul>
    <li>Reducing the cell count or iteration count</li>
    <li>Using a smaller outline area</li>
    <li>Lowering the pattern order (for Hilbert Curve)</li>
</ul>

<h4 style="color: var(--panel-title-color);">Pattern doesn't fill the outline</h4>
<p>This usually means your outline is too small for the pattern parameters. Try:</p>
<ul>
    <li>Increasing the outline size</li>
    <li>Decreasing pattern parameters (size, spacing)</li>
    <li>Using the Fit View command (<kbd>F</kbd>) to inspect your outline</li>
</ul>

<h4 style="color: var(--panel-title-color);">DXF export has missing geometry</h4>
<p>Ensure your outline is a valid closed polyline. Open or self-intersecting outlines may produce unexpected results.</p>

<h3 style="color: var(--subheading-color);">Pro Tips</h3>
<ul>
    <li><strong>Use Presets as starting points:</strong> Load a built-in preset, then tweak parameters to get your exact desired look.</li>
    <li><strong>Combine patterns with zones:</strong> Use different patterns in different zones for complex multi-texture designs.</li>
    <li><strong>Add fill on top:</strong> Apply a Lines or Crosshatch fill to add extra surface coverage over your main pattern.</li>
    <li><strong>Preview before generating:</strong> Use the Preview toggle to quickly check results without waiting for full generation.</li>
    <li><strong>Save custom presets:</strong> Once you find a combination you love, save it as a preset for one-click recall.</li>
    <li><strong>Use exclusions for lettering:</strong> Mark text outlines as exclusion cutouts to have patterns flow around them.</li>
</ul>


<!-- ═══════════════════ SUPPORT ═══════════════════ -->
<h2 id="support" style="color: var(--accent-color); border-bottom: 1px solid var(--border-color); padding-bottom: 8px;">
    📬 Support &amp; Feedback
</h2>

<p>If you encounter bugs, have feature requests, or want to contribute:</p>
<ul>
    <li><strong>GitHub Issues:</strong> Report bugs and request features</li>
    <li><strong>Error Reports:</strong> Simple Stipple includes built-in error reporting — check the Settings to configure it.</li>
    <li><strong>Logging:</strong> Detailed logs are available for debugging (check the app's log directory).</li>
</ul>

<hr style="border: none; border-top: 2px solid var(--accent-color); margin: 30px 0;">
<p style="text-align:center; color: var(--text-color);"><em>Simple Stipple — Precision patterns for precision work.</em></p>
"""


# ── Navigation TOC entries (section id → display label) ───────────────────────

TOC_ENTRIES = [
    ("getting-started", "🚀 Getting Started"),
    ("pattern-page", "📐 Pattern Page"),
    ("pattern-types", "🎨 Pattern Types (20+)"),
    ("fill-modes", "🖌️ Fill Modes"),
    ("zones-exclusions", "🗂️ Zones & Exclusions"),
    ("halftone-tiles", "🖼️ Halftone & Tile Library"),
    ("presets", "💾 Preset System"),
    ("canvas-tools", "🖱️ Canvas Tools & Navigation"),
    ("keyboard-shortcuts", "⌨️ Keyboard Shortcuts"),
    ("workspace-state", "💼 Workspace State"),
    ("troubleshooting", "🔧 Troubleshooting & Tips"),
    ("support", "📬 Support & Feedback"),
]


class HelpDialog(QDialog):
    """Comprehensive help/manual dialog with interactive table of contents."""

    def __init__(
        self, parent: QWidget | None = None, main_window: QMainWindow | None = None
    ):
        super().__init__(parent, Qt.WindowType.Window)
        self._main_window = main_window
        self.setWindowTitle("Simple Stipple — User Manual")
        self.setMinimumSize(900, 650)

        # Apply theme-aware stylesheet
        self._apply_stylesheet()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header bar
        header = QFrame()
        header.setObjectName("helpHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 12)

        title_label = QLabel("📖 User Manual")
        title_label.setObjectName("helpTitle")
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setObjectName("helpCloseButton")
        close_btn.setFixedSize(32, 32)
        close_btn.clicked.connect(self.close)
        header_layout.addWidget(close_btn)

        root.addWidget(header)

        # Splitter: TOC | Content
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left panel — Table of Contents
        toc_widget = QFrame()
        toc_widget.setObjectName("tocPanel")
        toc_layout = QVBoxLayout(toc_widget)
        toc_layout.setContentsMargins(0, 8, 0, 8)

        toc_label = QLabel("Table of Contents")
        toc_label.setObjectName("tocLabel")
        toc_layout.addWidget(toc_label)

        self._toc_list = QListWidget()
        self._toc_list.setObjectName("tocList")
        self._toc_list.setStyleSheet("""
            QListWidget {
                background: transparent;
                border: none;
                font-size: 13px;
                padding: 4px;
            }
            QListWidget::item {
                padding: 6px 12px;
                border-radius: 4px;
            }
            QListWidget::item:selected {
                background: rgba(0, 150, 255, 0.15);
            }
        """)

        for section_id, label in TOC_ENTRIES:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, section_id)
            self._toc_list.addItem(item)

        self._toc_list.currentItemChanged.connect(self._on_toc_changed)
        toc_layout.addWidget(self._toc_list)

        splitter.addWidget(toc_widget)

        # Right panel — Help content
        self._content = QTextBrowser()
        self._content.setObjectName("helpContent")
        self._content.setHtml(HELP_HTML)
        self._content.anchorClicked.connect(self._on_anchor_clicked)

        # Configure font for readability
        font = QFont("system-ui, -apple-system, sans-serif", 13)
        self._content.setFont(font)

        splitter.addWidget(self._content)

        # Set initial splitter sizes (TOC : Content ≈ 1 : 3)
        splitter.setSizes([260, 640])
        root.addWidget(splitter)

    # ── Styling ──────────────────────────────────────────────────────────

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet("""
            QDialog {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(30, 35, 45, 1),
                    stop:1 rgba(25, 30, 40, 1));
            }
            #helpHeader {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(0, 150, 255, 0.2),
                    stop:1 rgba(0, 100, 200, 0.1));
                border-bottom: 2px solid rgba(0, 150, 255, 0.4);
            }
            #helpTitle {
                color: #ffffff;
                font-size: 18px;
                font-weight: bold;
                padding: 4px 0;
            }
            #helpCloseButton {
                background: transparent;
                color: rgba(255, 255, 255, 0.7);
                border: none;
                font-size: 16px;
            }
            #helpCloseButton:hover {
                color: #ffffff;
                background: rgba(255, 255, 255, 0.1);
            }
            #tocPanel {
                background: rgba(25, 30, 40, 0.9);
                border-right: 1px solid rgba(255, 255, 255, 0.08);
            }
            #tocLabel {
                color: rgba(255, 255, 255, 0.6);
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 1px;
                padding: 4px 16px;
            }
            #tocList {
                background: transparent;
            }
            #helpContent {
                background: transparent;
                color: rgba(255, 255, 255, 0.88);
                border: none;
                line-height: 1.7;
            }
        """)

    # ── TOC interaction ──────────────────────────────────────────────────

    def _on_toc_changed(self, current: QListWidgetItem | None) -> None:
        if current is None:
            return
        section_id = current.data(Qt.ItemDataRole.UserRole)
        self._scroll_to_section(section_id)

    def _on_anchor_clicked(self, url: QUrl) -> None:
        anchor = url.fragment()
        if anchor:
            self._scroll_to_section(anchor)

    def _scroll_to_section(self, section_id: str) -> None:
        self._content.scrollToAnchor(section_id)
        # Highlight the corresponding TOC entry
        items = self._toc_list.findItems("", Qt.MatchFlag.MatchContains)
        for item in items:
            if item.data(Qt.ItemDataRole.UserRole) == section_id:
                self._toc_list.setCurrentItem(item)
                break

    # ── Public API ───────────────────────────────────────────────────────

    @classmethod
    def show_help(
        cls, parent: QWidget | None = None, main_window: QMainWindow | None = None
    ) -> HelpDialog:
        """Show the help dialog. Returns the dialog instance."""
        dialog = cls(parent, main_window)
        dialog.exec()
        return dialog
