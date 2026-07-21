"""Comprehensive help system with searchable table of contents.

Provides a fully-fledged, searchable, navigable user manual accessible from
the Help menu and the command palette.  Content is generated dynamically so
it always stays in sync with the actual canvas command registry, page
registry, and application settings.

Usage:
    from src.ui.pages.help import HelpDialog
    HelpDialog.show_help(parent, main_window)
"""

from __future__ import annotations

import logging
from html import escape as _html_escape

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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


# ── Helpers ────────────────────────────────────────────────────────────────


def _esc(text: str) -> str:
    """HTML-escape a string."""
    return _html_escape(str(text))


def _toc_entry(section_id: str, label: str) -> tuple[str, str]:
    """Create a TOC entry."""
    return (section_id, label)


# ── Content generators ────────────────────────────────────────────────────


def _build_getting_started() -> str:
    """Getting Started section."""
    return """
<h2 id="getting-started" class="section-heading">
    Getting Started
</h2>

<h3 class="subheading">What is Simple Stipple?</h3>
<p>Simple Stipple is a desktop application that generates intricate laser-engraving patterns inside user-defined outlines. You draw or import an outline, choose a pattern and its parameters, and the software fills the area with mathematically precise vector geometry ready for your laser cutter.</p>

<h3 class="subheading">System Requirements</h3>
<ul>
    <li><strong>Python 3.10+</strong> (if running from source)</li>
    <li><strong>Shapely</strong> for computational geometry operations</li>
    <li><strong>PySide6</strong> (Qt6 bindings) for the graphical interface</li>
    <li>A laser cutter that accepts DXF output (most modern controllers do)</li>
</ul>

<h3 class="subheading">Quick Start — Your First Pattern</h3>
<ol>
    <li><strong>Open the Pattern page</strong> from the tab bar at the top (or press <kbd>Alt+2</kbd>).</li>
    <li><strong>Load an outline</strong> — either draw one using the drawing tools (press <kbd>D</kbd>) or import an existing DXF file via the <strong>Open DXF</strong> button.</li>
    <li><strong>Select a pattern</strong> from the dropdown (e.g., "Honeycomb").</li>
    <li><strong>Adjust parameters</strong> — size, gap, spacing — using the sidebar controls.</li>
    <li><strong>Click "Generate"</strong> to create the pattern. Use the preview toggle to compare before/after.</li>
    <li><strong>Export</strong> the result as a DXF file for your laser cutter.</li>
</ol>

<h3 class="subheading">The Five Pages</h3>
<table style="width:100%; border-collapse:collapse; margin:12px 0;">
    <tr style="background-color:#1c2e4a;">
        <th style="padding:8px;text-align:left;border-bottom:2px solid #2f81f7;">Page</th>
        <th style="padding:8px;text-align:left;border-bottom:2px solid #2f81f7;">Shortcut</th>
        <th style="padding:8px;text-align:left;border-bottom:2px solid #2f81f7;">Purpose</th>
    </tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Draft</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Alt+1</td><td style="padding:6px;border-bottom:1px solid #30363d;">2D drafting — draw, edit, import/export DXF/SVG geometry.</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Pattern</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Alt+2</td><td style="padding:6px;border-bottom:1px solid #30363d;">Generate 20+ laser-engraving patterns inside outlines.</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Trace</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Alt+3</td><td style="padding:6px;border-bottom:1px solid #30363d;">Convert images to vector outlines (tracing).</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Convert</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Alt+4</td><td style="padding:6px;border-bottom:1px solid #30363d;">Utilities — FVI→DXF, SVG↔DXF conversion, repair.</td></tr>
    </table>

<p><em>Repository sync (Git pull/commit/push) lives under <strong>File ▸ Repository Sync…</strong> (Alt+5).</em></p>

<p><em>Switch between pages using the tab bar or keyboard shortcuts. Each page maintains its own workspace state which is saved automatically.</em></p>
"""


def _build_whats_new_031() -> str:
    """Release-level coverage for features added since the previous manual pass."""
    return """
<h2 id="whats-new-031" class="section-heading">What’s New in 0.3.1</h2>

<h3 class="subheading">Guided precision drafting</h3>
<ul>
    <li><strong>Persistent command guidance:</strong> the bottom status chip identifies the active command, the next point or selection it expects, and the keys that finish or cancel it.</li>
    <li><strong>Sketch palette:</strong> Grid, object snaps, Construction mode, and geometric constraints now share one persistent precision bar. The Constrain menu supports Horizontal, Vertical, Parallel, Perpendicular, Equal Length, Coincident, Fix, and removal.</li>
    <li><strong>Relationship inference:</strong> new segments acquire exact parallel or perpendicular directions when the pointer approaches an existing edge direction. Disable Angle Constraints in Snap to bypass this behavior.</li>
    <li><strong>Expression entry:</strong> dimensional fields accept arithmetic, parentheses, and mixed units, including <code>25/2</code>, <code>(10+5)*2</code>, and <code>1in + 3mm</code>. Bare lengths use the active display unit.</li>
</ul>

<h3 class="subheading">Direct and contextual editing</h3>
<ul>
    <li><strong>Contextual Properties actions:</strong> selection-aware Edit Vertices, Duplicate, Open/Close Path, and Delete actions appear only when applicable.</li>
    <li><strong>Property highlighting:</strong> hover or focus a position, size, radius, or rotation field to highlight the corresponding geometry on the canvas.</li>
    <li><strong>Local transform frames:</strong> rotated rectangles, rounded rectangles, ellipses, circles, and slots resize along their own axes. Width/height edits preserve their parametric shape data instead of silently converting them to generic paths.</li>
    <li><strong>Shape controls:</strong> circles and ellipses retain editable radius controls; arcs and rectangles retain their defining controls. Double-clicking a path edge in Edit mode inserts a vertex.</li>
    <li><strong>Reliable gizmos:</strong> rotation, edge/corner resize, move, and enlarged invisible hit targets work across parametric shapes, including slots.</li>
</ul>

<h3 class="subheading">Safer operations and clearer previews</h3>
<ul>
    <li><strong>Trim and Extend previews:</strong> hovering shows the exact segment to remove or extension to create before a click commits it.</li>
    <li><strong>Geometry health:</strong> preflight reports open paths, invalid or duplicate geometry, tiny/zero-length segments, and the minimum segment before fabrication.</li>
    <li><strong>Curve fidelity:</strong> spline and parametric geometry is re-tessellated for export and cross-page transfer rather than using stale low-resolution points.</li>
    <li><strong>Responsive cancellation:</strong> long Pattern, Trace, Convert, and batch operations expose cancellation and avoid applying stale worker results.</li>
</ul>

<h3 class="subheading">Interoperability and workflow</h3>
<ul>
    <li><strong>Unified vector import:</strong> Draft opens, adds, or accepts dropped DXF, SVG, and FVI files through Import Vector.</li>
    <li><strong>StarFX FVI:</strong> geometry-only import/export supports MOVEDIST, DRAWLINE, and DRAWARC, configurable origin/margins/precision/Y orientation, travel optimization, path reversal, native arc preservation, comments, and explicit reports for unsupported machine commands.</li>
    <li><strong>Improved SVG/DXF handling:</strong> transforms, curves, view boxes, nested groups, bulges/arcs, and layer-aware export retain substantially more source fidelity.</li>
    <li><strong>Pattern presets:</strong> presets can be renamed, duplicated, imported/exported as JSON, and restored from built-ins. Pattern output roles and layers remain distinct during export.</li>
    <li><strong>Architecture and recovery:</strong> consolidated settings, notifications, units, page runtime, and operation handling reduce duplicated state while preserving multi-window workspace recovery.</li>
</ul>
"""


def _build_draft_page() -> str:
    """Draft page section."""
    return """
<h2 id="draft-page" class="section-heading">
    Draft Page — Full Reference
</h2>

<h3 class="subheading">Overview</h3>
<p>The Draft page is your 2D drafting canvas. It provides a full-featured vector editing environment for creating, importing, and exporting geometry.</p>

<h3 class="subheading">Toolbar</h3>
<ul>
    <li><strong>Import Vector:</strong> Open DXF, SVG, or FVI geometry (replaces the current drawing). Use the dropdown arrow to add it without replacing, or drop a supported file onto the page.</li>
    <li><strong>Recent Vectors:</strong> Quick access to recently imported DXF, SVG, and FVI files.</li>
    <li><strong>Explode:</strong> Break selected grouped shapes into individual segments.</li>
    <li><strong>Merge:</strong> Combine selected connected segments into single objects.</li>
    <li><strong>Export DXF:</strong> Save as grouped DXF (shapes sharing a layer are treated as one group by the laser). Use the adjacent <strong>⋯</strong> menu for FVI or SVG export.</li>
    <li><strong>Drawing Modes:</strong> Select (S), Draw (D), Edit (E) — also accessible via single-letter keys.</li>
    <li><strong>Fit:</strong> Zoom to fit all content (F).</li>
</ul>

<h3 class="subheading">Drawing Primitives</h3>
<p>In Draw mode, pick a primitive from the "+" tool picker in the Draw sidebar, or from the <kbd>Q</kbd> radial menu (Polyline, Rectangle, Circle, Polygon, Line, Arc, Pen are included by default). Once picked:</p>
<ul>
    <li><strong>Rectangle, Circle, Ellipse, Polygon, Arc:</strong> click-drag to size the shape.</li>
    <li><strong>Polyline / Line:</strong> click to place vertices, double-click or <kbd>Enter</kbd> to finish, <kbd>Esc</kbd> to cancel.</li>
    <li><strong>Spline:</strong> click to place points; a smooth curve is fit through them (Catmull-Rom). For direct handle control, use the Pen tool instead (see below).</li>
</ul>

<h3 class="subheading">Quick-Shape Drag (Select Mode)</h3>
<p>A faster way to drop a shape without leaving Select mode: press one of these to arm "quick shape" drag-to-create, then drag anywhere on the canvas.</p>
<ul>
    <li><strong>Shift+R:</strong> Rectangle</li>
    <li><strong>Shift+C:</strong> Circle</li>
    <li><strong>Shift+S:</strong> Slot (rounded oblong)</li>
    <li><strong>Shift+P:</strong> Hexagon</li>
</ul>
<p>Holding <kbd>Alt</kbd> while dragging switches to Circle for that drag; holding <kbd>Ctrl</kbd> switches to Slot. Press <kbd>Esc</kbd> to turn quick-shape mode back off.</p>

<h3 class="subheading">Editing Features</h3>
<ul>
    <li><strong>Vertex Editing:</strong> In Edit mode, click on shape vertices to move them individually.</li>
    <li><strong>8-Handle Resize:</strong> Select a shape to see 8 resize handles (corners + edges). Rotated parametric shapes use their local axes. Corners resize both dimensions, edges resize one; hold <kbd>Shift</kbd> to lock aspect ratio or <kbd>Alt</kbd> to resize from center.</li>
    <li><strong>Properties &amp; Expressions:</strong> edit position, local size, rotation, and shape parameters without leaving the selection. Arithmetic and mixed units are accepted. Hover/focus a field to identify the geometry it controls.</li>
    <li><strong>Contextual Actions:</strong> Edit Vertices, Duplicate, Open/Close Path, and Delete appear in Properties when valid for the current selection.</li>
    <li><strong>Rounding Corners:</strong> In Edit mode, hover a corner and press <kbd>R</kbd> to round it with a configurable radius.</li>
    <li><strong>Offset:</strong> Create an offset copy of selected shapes (O). Specify distance in mm.</li>
    <li><strong>Text:</strong> Add parametric text at cursor position (T) — see Text &amp; Typography for multi-line and text-on-path.</li>
    <li><strong>Array Duplication:</strong> "Array — Grid…" and "Array — Radial…" (command palette or right-click menu) repeat a selection in a grid or around a circle, each in a single undo step.</li>
</ul>

<h3 class="subheading">Boolean Operations</h3>
<table style="width:100%;border-collapse:collapse;margin:8px 0;">
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Union (Weld)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Merge overlapping shapes into one (Ctrl+U)</td></tr>
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Subtract</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Cut one shape from another (Ctrl+Shift+U)</td></tr>
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Intersect</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Keep only the overlapping region (Ctrl+Alt+U)</td></tr>
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Divide</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Split shapes at intersection points (Ctrl+Alt+Shift+U)</td></tr>
</table>

<h3 class="subheading">Path Operations</h3>
<ul>
    <li><strong>Close Polyline:</strong> Close an open path by connecting the last vertex to the first (Shift+C).</li>
    <li><strong>Open Polyline:</strong> Open a closed path by removing the closing segment (Shift+O).</li>
    <li><strong>Construction Mode:</strong> Draw construction lines (dashed) that don't appear in export. Toggle with <kbd>X</kbd>.</li>
    <li><strong>Simplify, Smooth, Fit to Curve:</strong> see the dedicated Path Cleanup Tools section.</li>
</ul>

<h3 class="subheading">Trim &amp; Extend Tools</h3>
<ul>
    <li><strong>Trim (K):</strong> Hover to preview the exact part that will be removed, then click to commit. The tool stays active until <kbd>Esc</kbd>.</li>
    <li><strong>Extend (L):</strong> Hover an open endpoint to preview its extension to the next intersection, then click to commit.</li>
</ul>

<h3 class="subheading">Rulers &amp; Guides</h3>
<p>Show rulers with <kbd>Ctrl+R</kbd>. Drag from the top ruler to create horizontal guides, or from the left ruler for vertical guides. Guides participate in snapping. Drag a guide back onto its ruler to delete it.</p>
"""


def _build_bezier_pen_tool() -> str:
    """Bezier Pen tool section."""
    return """
<h2 id="bezier-pen-tool" class="section-heading">
    Bezier Pen Tool
</h2>

<p>The Pen tool (the "Bezier Pen" entry in the Draw tool picker) draws true cubic-bezier curves with draggable tangent handles — a different, more powerful tool than the Spline primitive (which fits a curve through plain points with no direct handle control).</p>
<ul>
    <li><strong>Click:</strong> places a corner anchor (a sharp point — zero-length tangent handles).</li>
    <li><strong>Click and drag:</strong> places a smooth anchor with a symmetric pair of tangent handles extending from the drag direction — drag further for a rounder curve.</li>
    <li><strong>Double-click, or press Enter:</strong> finishes the curve.</li>
    <li><strong>Esc:</strong> cancels the curve in progress.</li>
</ul>
<p>Pen curves snap to existing vertices, edges, and guides while placing anchors, same as the Draw tool. Once finished, a curve is a normal selectable/editable entity — Simplify, Smooth, Boolean operations, and Move/Rotate/Scale all work on it, and it exports as a native DXF entity where possible (falling back to a smooth tessellated polyline otherwise) rather than a jagged approximation.</p>
"""


def _build_dimension_tool() -> str:
    """Dimension / annotation tool section."""
    return """
<h2 id="dimension-tool" class="section-heading">
    Dimension Tool
</h2>

<p>The Dimension tool (<kbd>Shift+M</kbd>, or the dimension button in the canvas corner) creates persistent CAD-style measurements. Driving dimensions reshape supported geometry when edited; reference dimensions report geometry without changing it.</p>
<ul>
    <li><strong>Select segments:</strong> the same segment measures length; intersecting segments measure angle; parallel segments measure spacing; separate segments measure their shortest distance. Circles produce diameter dimensions. Point-to-point dimensions remain available for exact vertex measurements.</li>
    <li><strong>Edit a value:</strong> double-click a supported driving dimension and enter a value or expression. Its fixed anchor stays in place while the affected edge or angle changes. If other dimensions cannot all be satisfied, the conflicting target is marked visibly instead of displaying a false value.</li>
    <li><strong>Place the label:</strong> move the pointer and click to choose the dimension-line offset. Drag a placed dimension later to adjust that offset.</li>
    <li>Dimensions snap to vertices/edges/guides while placing, exactly like the Draw and Scale tools.</li>
    <li><strong>Deleting one:</strong> select it (click its line) and press Delete or Backspace.</li>
    <li>Dimensions are saved with the workspace and exported as DXF dimension entities. Linear dimensions use <kbd>Shift+M</kbd>; diameter dimensions can be placed directly on circles, and Angular Dimension is available from the command palette.</li>
    <li>Right-click a dimension to change display precision, toggle driving/reference behavior where supported, or delete it. Press <kbd>Esc</kbd> to leave Dimension mode.</li>
</ul>
"""


def _build_radial_menu() -> str:
    """Quick radial menu section."""
    return """
<h2 id="radial-menu" class="section-heading">
    Quick Radial Menu
</h2>

<p>Press <kbd>Q</kbd> anywhere, in any mode, to open a radial quick-launcher under the cursor — click a wedge (or click elsewhere/press Q again to cancel). By default it offers the draw-shape primitives (Polyline, Rectangle, Circle, Polygon, Line, Arc, Pen), since those don't otherwise have a dedicated single-key shortcut.</p>
<p>The wheel is fully customizable: open <strong>Settings → Customize radial menu…</strong> to choose from the app's entire command set — undo/redo, clipboard, duplicate, delete, selection, grouping, boolean operations, round/chamfer corner, text, view/grid toggles, and more — and drag to reorder them. At least 3 must stay checked. The wheel grows to fit more wedges (up to 12) and long labels are shortened with an ellipsis rather than spilling past the edge.</p>
"""


def _build_path_cleanup() -> str:
    """Simplify / Smooth / Fit to Curve section."""
    return """
<h2 id="path-cleanup" class="section-heading">
    Path Cleanup Tools
</h2>

<p>Right-click a selected polyline for these — useful for cleaning up traced or hand-drawn geometry:</p>
<table style="width:100%;border-collapse:collapse;margin:8px 0;">
    <tr style="background-color:#1c2e4a;">
        <th style="padding:8px;text-align:left;border-bottom:2px solid #2f81f7;">Operation</th>
        <th style="padding:8px;text-align:left;border-bottom:2px solid #2f81f7;">What it does</th>
    </tr>
    <tr><td style="padding:8px;border-bottom:1px solid #30363d;"><strong>Simplify…</strong></td><td style="padding:8px;border-bottom:1px solid #30363d;">Reduces vertex count with Douglas-Peucker simplification, staying within a tolerance (mm) of the original shape. Stays a straight-segment polyline.</td></tr>
    <tr><td style="padding:8px;border-bottom:1px solid #30363d;"><strong>Smooth</strong></td><td style="padding:8px;border-bottom:1px solid #30363d;">Rounds sharp vertices using Chaikin's corner-cutting algorithm. Also stays a polyline — more points, softer corners.</td></tr>
    <tr><td style="padding:8px;border-bottom:1px solid #30363d;"><strong>Fit to Curve…</strong></td><td style="padding:8px;border-bottom:1px solid #30363d;">Converts a dense/jagged polyline (e.g. Trace output) into a genuine editable bezier curve — far fewer control points, with real corners (sharp turns) automatically detected and kept sharp instead of rounded off.</td></tr>
</table>
"""


def _build_text_tools() -> str:
    """Multi-line text + text-on-path section."""
    return """
<h2 id="text-tools" class="section-heading">
    Text &amp; Typography
</h2>

<p>The Add Text dialog (<kbd>T</kbd>) supports multi-line text — press Enter inside the text box for a new line, each line lays out as a separate row using the chosen font's natural line spacing.</p>
<p>To flow a text object along an existing path: select exactly one text object and one open path, then use <strong>Attach Text to Path</strong> (command palette or right-click menu). Each character's contour is repositioned and rotated to sit tangent to the path at even spacing. Editing the text later ("Rebuild Text") re-flows it along the same path automatically.</p>
"""


def _build_layers() -> str:
    """Layer management section."""
    return """
<h2 id="layers" class="section-heading">
    Layers
</h2>

<p>The layer tree (right side of the Draft page) lists every layer and, expanded, every shape on it.</p>
<ul>
    <li><strong>Rename:</strong> double-click a layer or shape name, or press <kbd>F2</kbd> with it selected, then type a new name and press Enter.</li>
    <li><strong>Color:</strong> right-click a layer to assign it a color swatch, shown next to its name and used for that layer's geometry on the canvas.</li>
    <li><strong>Visibility:</strong> the checkbox next to each layer/shape toggles whether it's shown (and exported) — layer visibility cascades to everything on it.</li>
    <li><strong>Reorder:</strong> drag layers to change their order, or use the up/down controls in the layer panel.</li>
    <li><strong>New / Delete:</strong> use the layer panel's toolbar buttons; deleting a layer removes everything on it (with a confirmation prompt).</li>
</ul>
"""


def _build_pattern_page() -> str:
    """Pattern page section."""
    return """
<h2 id="pattern-page" class="section-heading">
    Pattern Page — Full Reference
</h2>

<h3 class="subheading">Overview</h3>
<p>The Pattern page is the heart of Simple Stipple. It provides a split-view interface: parameter controls on the left, an interactive canvas with your pattern preview on the right.</p>

<h3 class="subheading">Left Panel — Controls</h3>
<ul>
    <li><strong>DXF File Picker:</strong> Load an existing DXF file containing your outline geometry.</li>
    <li><strong>Pattern Type Dropdown:</strong> Choose from 20+ built-in pattern generators.</li>
    <li><strong>Parameter Controls:</strong> Dynamic widgets that change based on the selected pattern (size, gap, spacing, etc.).</li>
    <li><strong>Fill Mode:</strong> Choose how to fill the pattern area — None, Lines (parallel hatch), or Crosshatch (crossed ±45° lines).</li>
    <li><strong>Presets Manager:</strong> Save, load, and manage pattern presets for quick recall.</li>
    <li><strong>Generate / Preview Buttons:</strong> Generate the full pattern or show a live preview.</li>
    <li><strong>Export Button:</strong> Save the result as a DXF file.</li>
</ul>

<h3 class="subheading">Right Panel — Canvas</h3>
<ul>
    <li><strong>Interactive View:</strong> Zoom, pan, and inspect your pattern in real time.</li>
    <li><strong>Layer Tree:</strong> Toggle visibility of outline, pattern, and fill layers.</li>
    <li><strong>Grid:</strong> Toggle a measurement grid for precision work.</li>
    <li><strong>Status Strip:</strong> Shows current dimensions, pattern count, and tool status.</li>
</ul>

<h3 class="subheading">Zones &amp; Exclusions</h3>
<p><strong>Zones</strong> allow you to assign different patterns to different regions within the same outline. Each zone is a snapshot containing:</p>
<ul>
    <li>A set of outline IDs (the region boundaries)</li>
    <li>A pattern type</li>
    <li>Pattern parameters</li>
    <li>An optional scale (width, height)</li>
    <li>A label for identification</li>
</ul>
<p><strong>Exclusion cutouts</strong> are outline IDs that the pattern will <em>avoid</em>. They act as "holes" in your pattern fill — use this for lettering, logos, or mechanical cutouts.</p>

<h3 class="subheading">Fill Modes</h3>
<table style="width:100%;border-collapse:collapse;margin:8px 0;">
    <tr style="background-color:#1c2e4a;">
        <th style="padding:8px;text-align:left;border-bottom:2px solid #2f81f7;">Mode</th>
        <th style="padding:8px;text-align:left;border-bottom:2px solid #2f81f7;">Description</th>
    </tr>
    <tr><td style="padding:8px;border-bottom:1px solid #30363d;"><strong>None</strong></td><td style="padding:8px;border-bottom:1px solid #30363d;">No fill pattern. Only the main pattern is generated.</td></tr>
    <tr><td style="padding:8px;border-bottom:1px solid #30363d;"><strong>Lines</strong></td><td style="padding:8px;border-bottom:1px solid #30363d;">Parallel hatch lines across the fill region. Configurable spacing and angle.</td></tr>
    <tr><td style="padding:8px;border-bottom:1px solid #30363d;"><strong>Crosshatch</strong></td><td style="padding:8px;border-bottom:1px solid #30363d;">Two sets of parallel lines at ±45° angles, creating a crossed-hatch grid.</td></tr>
</table>

<h3 class="subheading">Presets System</h3>
<p><strong>Built-in presets:</strong> Honeycomb (Fine/Standard/Bold), Stipple (Dense/Open), Brick, Mesh, and Voronoi.</p>
<p><strong>Custom presets:</strong> Save your current pattern + all parameters as a named preset. Export/import presets as JSON files (<code>simple-stipple-presets/v1</code> format).</p>
"""


def _build_pattern_types() -> str:
    """Available pattern types and parameters."""
    return """
<h2 id="pattern-types" class="section-heading">
    Pattern Types — Complete Guide
</h2>

<p>Simple Stipple includes geometric, organic, and custom-tile pattern generators.</p>

<h3 class="subheading">Geometric Patterns</h3>

<h4 class="panel-title">Honeycomb</h4>
<p>A classic hexagonal grid pattern, ideal for structural engraving and decorative surfaces.</p>
<table style="width:100%;border-collapse:collapse;margin:6px 0;">
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Hex size (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Radius of each hexagonal cell (default: 1.75 mm)</td></tr>
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Gap (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Spacing between adjacent hexagons (default: 0.5 mm)</td></tr>
</table>

<h4 class="panel-title">Gradient Honeycomb</h4>
<p>A hexagonal grid where cell sizes gradually change across the pattern, creating a flowing gradient effect.</p>
<table style="width:100%;border-collapse:collapse;margin:6px 0;">
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Min size (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Smallest hex cell (default: 0.8 mm)</td></tr>
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Max size (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Largest hex cell (default: 2.5 mm)</td></tr>
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Gap (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Spacing between hexagons (default: 0.5 mm)</td></tr>
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Direction (°)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Gradient direction — 0° = left to right, 90° = vertical (default: 0°)</td></tr>
</table>

<h4 class="panel-title">Brick</h4>
<p>A classic brickwork pattern with staggered rows, perfect for wall and floor engraving.</p>
<table style="width:100%;border-collapse:collapse;margin:6px 0;">
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Brick width (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Width of each brick (default: 4.0 mm)</td></tr>
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Brick height (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Height of each brick (default: 2.0 mm)</td></tr>
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Gap (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Mortar gap (default: 0.5 mm)</td></tr>
</table>

<h4 class="panel-title">Mesh</h4>
<p>A grid of circles arranged in a regular pattern, creating an elegant mesh effect.</p>
<table style="width:100%;border-collapse:collapse;margin:6px 0;">
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Circle radius (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Radius of each circle (default: 0.35 mm)</td></tr>
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Grid spacing (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Centre-to-centre distance (default: 1.2 mm)</td></tr>
</table>

<h3 class="subheading">Organic / Curved Patterns</h3>

<h4 class="panel-title">Fish Scale</h4>
<p>Overlapping arc segments that mimic the appearance of fish scales.</p>
<table style="width:100%;border-collapse:collapse;margin:6px 0;">
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Scale width (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Horizontal span (default: 3.0 mm)</td></tr>
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Scale height (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Vertical height (default: 2.0 mm)</td></tr>
</table>

<h4 class="panel-title">Topographic</h4>
<p>Inward-offset contour lines that simulate topographic map elevation lines.</p>
<table style="width:100%;border-collapse:collapse;margin:6px 0;">
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Contour spacing (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Distance between successive contours (default: 1.5 mm)</td></tr>
</table>

<h3 class="subheading">Mathematical / Fractal Patterns</h3>

<h4 class="panel-title">Voronoi</h4>
<p>A Voronoi diagram partitioning the outline into irregular polygonal cells.</p>
<table style="width:100%;border-collapse:collapse;margin:6px 0;">
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Cell count</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Number of random cells (default: 60)</td></tr>
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Gap (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Inset distance between cells (default: 0.15 mm)</td></tr>
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Seed</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Random seed (default: 42)</td></tr>
</table>

<h3 class="subheading">Decorative / Woven Patterns</h3>

<h4 class="panel-title">Basketweave</h4>
<p>A woven pattern of horizontal and vertical strips, creating an interlaced basket effect.</p>
<table style="width:100%;border-collapse:collapse;margin:6px 0;">
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Strip width (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Width of each woven strip (default: 2.0 mm)</td></tr>
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Strip length (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Length of each strip (default: 8.0 mm)</td></tr>
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Gap (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Gap between strips (default: 0.2 mm)</td></tr>
</table>

<h4 class="panel-title">Braid</h4>
<p>Interlocking diagonal strips creating a braid or weave effect at ±45° angles.</p>
<table style="width:100%;border-collapse:collapse;margin:6px 0;">
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Strip width (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Width of each diagonal strip (default: 2.0 mm)</td></tr>
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Spacing (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Gap between parallel strips (default: 3.0 mm)</td></tr>
</table>

<h4 class="panel-title">Stipple Dots</h4>
<p>Poisson-disk distributed circles creating a stippled, pointillist effect.</p>
<table style="width:100%;border-collapse:collapse;margin:6px 0;">
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Dot radius (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Radius of each dot (default: 0.4 mm)</td></tr>
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Spacing (mm)</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Centre-to-centre distance (default: 1.2 mm)</td></tr>
    <tr><td style="padding:4px;border-bottom:1px solid #30363d;"><strong>Interlaced</strong></td><td style="padding:4px;border-bottom:1px solid #30363d;">Use offset grid instead of Poisson-disk (checkbox)</td></tr>
</table>

"""


def _build_trace_page() -> str:
    """Trace page section."""
    return """
<h2 id="trace-page" class="section-heading">
    Trace Page — Image to Outline
</h2>

<h3 class="subheading">Overview</h3>
<p>The Trace page converts raster images (PNG, JPG, BMP, TIFF, GIF, WebP) into vector outlines suitable for laser cutting. It uses image tracing algorithms to detect edges and convert them to DXF-compatible polylines.</p>

<h3 class="subheading">Workflow</h3>
<ol>
    <li><strong>Import an image</strong> — drag and drop or use the file picker. Supported formats: PNG, JPG, JPEG, BMP, TIFF, GIF, WebP.</li>
    <li><strong>Adjust tracing parameters</strong> — threshold, smoothing, minimum feature size.</li>
    <li><strong>Preview the trace</strong> — see the vector overlay on your image.</li>
    <li><strong>Export as DXF</strong> — save the traced outlines for your laser cutter.</li>
</ol>

<h3 class="subheading">Parameters</h3>
<ul>
    <li><strong>Threshold:</strong> Controls the brightness cutoff for edge detection. Higher values capture more detail but may add noise.</li>
    <li><strong>Smoothing:</strong> Reduces jagged edges in the traced output.</li>
    <li><strong>Minimum feature size:</strong> Ignores features smaller than this value (in mm), reducing noise.</li>
    <li><strong>Output width (mm):</strong> Physical size of the traced output.</li>
    <li><strong>Aspect ratio lock:</strong> When enabled, maintains the original image proportions.</li>
</ul>

<h3 class="subheading">Features</h3>
<ul>
    <li><strong>Real-time preview:</strong> See tracing results update as you adjust parameters.</li>
    <li><strong>Background image:</strong> The original image is shown as a semi-transparent background for alignment.</li>
    <li><strong>Progress bar:</strong> Shows tracing progress with status messages.</li>
    <li><strong>Cancel:</strong> Cancel a long-running trace operation.</li>
    <li><strong>Send to Draft/Pattern:</strong> Export traced geometry directly to the Draft or Pattern pages.</li>
</ul>

<h3 class="subheading">Tips</h3>
<ul>
    <li><strong>High-contrast images work best.</strong> Black-and-white or high-contrast photos trace most accurately.</li>
    <li><strong>Use line art or logos</strong> for cleanest results. Photographs may produce noisy outlines.</li>
    <li><strong>Adjust threshold carefully</strong> — too low misses details, too high adds noise.</li>
    <li><strong>Increase minimum feature size</strong> to filter out small artifacts from noisy images.</li>
</ul>
"""


def _build_convert_page() -> str:
    """Convert page section."""
    return """
<h2 id="convert-page" class="section-heading">
    Convert Page — Utilities &amp; Conversion
</h2>

<h3 class="subheading">Overview</h3>
<p>The Convert page provides a suite of file conversion and repair utilities for working with DXF, SVG, and FVI (Fiverr Vector Interface) files.</p>

<h3 class="subheading">Sub-tabs</h3>

<h4 class='panel-title'>FVI Converter</h4>
<p>Convert FIV (Fiverr Vector Interface) files to DXF format for laser cutting.</p>
<ul>
    <li><strong>Single file mode:</strong> Convert one FVI file at a time.</li>
    <li><strong>Folder (batch) mode:</strong> Convert all FVI files in a folder at once.</li>
    <li><strong>Output path:</strong> Optional — blank means output alongside source files.</li>
</ul>

<h4 class='panel-title'>SVG ↔ DXF Converter</h4>
<p>Convert between SVG and DXF formats.</p>
<ul>
    <li><strong>SVG to DXF:</strong> Convert SVG vector paths to DXF polylines.</li>
    <li><strong>DXF to SVG:</strong> Convert DXF polylines to SVG paths.</li>
</ul>

<h4 class='panel-title'>DXF Repair</h4>
<p>Repair and fix common DXF issues:</p>
<ul>
    <li><strong>Single or batch:</strong> Repair one file or every DXF directly inside a selected folder.</li>
    <li><strong>Fix open polylines:</strong> Attempt to close unclosed paths.</li>
    <li><strong>Remove duplicates:</strong> Eliminate duplicate vertices and overlapping segments.</li>
    <li><strong>Simplify geometry:</strong> Reduce unnecessary vertices while preserving shape.</li>
    <li><strong>Important:</strong> Repair is a normalized export: supported entities are flattened to polylines and written on layer 0.</li>
</ul>

<h3 class="subheading">Status Messages</h3>
<p>Each sub-tab displays status messages with color coding:</p>
<ul>
    <li><strong style="color:#3fb950;">Green:</strong> Operation completed successfully.</li>
    <li><strong style="color:#f85149;">Red:</strong> Operation failed — check the message for details.</li>
    <li><strong style="color:#8b949e;">Gray:</strong> Neutral status information.</li>
</ul>

<h3 class="subheading">Ignored Entities</h3>
<p>When converting, some DXF entities may be ignored (unsupported types). The status message shows a count and summary of ignored entities.</p>
"""


def _build_repo_page() -> str:
    """Repo page section."""
    return """
<h2 id="repo-page" class="section-heading">
    Repository Sync — Git Workflow
</h2>

<h3 class="subheading">Overview</h3>
<p>Repository Sync (<strong>File ▸ Repository Sync…</strong>, Alt+5) provides a simplified Git workflow for managing your project repository directly from the application.</p>

<h3 class="subheading">Setup</h3>
<ol>
    <li><strong>Select repository folder:</strong> Browse to your Git repository root directory.</li>
    <li>The page will automatically detect and display the current repository status.</li>
</ol>

<h3 class="subheading">Workflow Cards</h3>

<h4 class='panel-title'>Pull</h4>
<p>Pull the latest changes from the remote repository. This fetches and merges updates from your configured remote.</p>

<h4 class='panel-title'>Commit</h4>
<p>Stage all changes and commit with a message. The default message is "Update project files" but you can customize it.</p>

<h4 class='panel-title'>Push</h4>
<p>Push committed changes to the remote repository. This is marked as a primary action (highlighted button).</p>

<h3 class="subheading">Secondary Actions</h3>
<ul>
    <li><strong>Status:</strong> Show the current repository status (modified files, staged changes, branch info).</li>
    <li><strong>Open Folder:</strong> Open the repository folder in Finder.</li>
</ul>

<h3 class="subheading">Git Log</h3>
<p>The right panel displays the Git log showing recent commits with:</p>
<ul>
    <li>Commit hash and message</li>
    <li>Author and date</li>
    <li>Branch information</li>
</ul>

<h3 class="subheading">Settings Integration</h3>
<p>The repository folder path is persisted in your settings and remembered between sessions. You can configure the default repo directory in Settings.</p>
"""


def _build_canvas_commands() -> str:
    """Canvas command reference from the actual registry."""
    try:
        from src.ui.canvas.interaction import commands as cmd_mod

        rows = cmd_mod.shortcut_reference_rows()
    except Exception:  # noqa: BLE001 — graceful fallback if commands module unavailable
        rows = []

    sections_html = ""
    for label, keys in rows:
        if not label:
            continue
        if not keys:
            sections_html += f"<h4 class='panel-title'>{_esc(label)}</h4>"
        else:
            sections_html += (
                f"<tr><td style='padding:4px;border-bottom:1px solid #30363d;'>"
                f"<strong>{_esc(label)}</strong></td>"
                f"<td style='padding:4px;border-bottom:1px solid #30363d;'>"
                f"{_esc(keys)}</td></tr>"
            )

    if sections_html:
        return f"""
<h2 id="canvas-commands" class="section-heading">
    Canvas Commands — Complete Reference
</h2>

<p>This reference is generated directly from the application's command registry, so it always stays in sync with your installed version.</p>

<table style="width:100%;border-collapse:collapse;margin:8px 0;">
    <tr style="background-color:#1c2e4a;">
        <th style="padding:8px;text-align:left;border-bottom:2px solid #2f81f7;">Command</th>
        <th style="padding:8px;text-align:left;border-bottom:2px solid #2f81f7;">Shortcut</th>
    </tr>
    {sections_html}
</table>

<h3 class="subheading">Canvas Interaction</h3>
<ul>
    <li><strong>Pan:</strong> Space-drag or middle mouse button</li>
    <li><strong>Zoom:</strong> Mouse wheel, ⌘+ / ⌘−</li>
    <li><strong>Nudge selection:</strong> Arrow keys (⇧ = 1 mm step)</li>
    <li><strong>Delete selected:</strong> Backspace / Del</li>
    <li><strong>Quick radial menu:</strong> Press Q to open — customizable in Settings, see Draft Page → Quick Radial Menu</li>
    <li><strong>Grid:</strong> Toggle with G, snap to grid with Shift+G</li>
    <li><strong>Rulers:</strong> Toggle with Ctrl+R. Drag from rulers to create guides.</li>
    <li><strong>Scale by reference:</strong> Press M, pick the fixed base point and a second reference point, then enter the real target distance. The explicit selection is scaled; with no selection, all visible unlocked geometry is used. Expressions and the active unit are supported. Shift constrains the reference angle, Alt bypasses snapping, right-click steps back, and Undo reverses the result.</li>
    <li><strong>Smart dimensions:</strong> Press Shift+M and hover an edge to highlight its exact segment. Selecting the same segment twice measures its length; two intersecting segments create an angle; two parallel segments create perpendicular spacing; and two separate segments create their shortest distance. Precise vertex-to-vertex dimensions and circle diameters remain available. Free point dimensions use two snapped points plus a third click for label offset. Drag a placed linear dimension to change its offset, double-click it to edit precision, or right-click for precision and delete actions. Dimensions persist with the workspace.</li>
    <li><strong>Fit view:</strong> Double-click empty space or press F</li>
    <li><strong>Snap engine:</strong> Automatically snaps to vertices, edges, and guides</li>
    <li><strong>Rubber band selection:</strong> Plain drag = window select (fully enclosed), Shift+drag = crossing select (touched)</li>
</ul>

<h3 class="subheading">Entity Properties</h3>
<p>Each entity on the canvas can have properties set via the properties panel:</p>
<ul>
    <li><strong>Hidden:</strong> Entity is not displayed or exported</li>
    <li><strong>Locked:</strong> Entity cannot be selected or moved</li>
    <li><strong>Construction:</strong> Drawn as dashed lines, not exported to DXF</li>
    <li><strong>Group:</strong> Entities grouped together move as one object</li>
    <li><strong>Layer:</strong> Organize entities into named layers (visible in layer tree)</li>
</ul>
"""
    return ""


def _build_shortcuts() -> str:
    """Keyboard shortcuts reference."""
    return """
<h2 id="keyboard-shortcuts" class="section-heading">
    Keyboard Shortcuts — Quick Reference
</h2>

<h3 class="subheading">Canvas Modes (Single Letter)</h3>
<table style="width:100%;border-collapse:collapse;margin:8px 0;">
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>S</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Switch to Select mode</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>D</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Switch to Draw mode</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>E</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Switch to Edit mode</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>F</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Fit view to all content</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>G</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Toggle grid visibility</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>P</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Switch to Pen tool (bezier curves)</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>M</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Toggle Scale-by-reference tool</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Shift+M</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Toggle Dimension tool (persistent annotation)</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>K</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Toggle Trim tool</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>L</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Toggle Extend tool</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>O</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Offset selection…</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>X</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Toggle construction mode</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>T</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Add text at cursor</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Q</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Open the quick radial menu (customizable — see Settings)</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>R</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Round hovered corner (Edit mode)</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Shift+G</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Toggle snap to grid</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>]</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Grid spacing ×2 (coarser)</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>[</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Grid spacing ÷2 (finer)</td></tr>
</table>

<h3 class="subheading">Selection &amp; Editing</h3>
<table style="width:100%;border-collapse:collapse;margin:8px 0;">
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Ctrl+Z</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Undo last action</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Ctrl+Shift+Z</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Redo</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Ctrl+A</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Select all</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Ctrl+Shift+A</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Deselect all</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Ctrl+I</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Invert selection</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Ctrl+G</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Group selected</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Ctrl+Shift+G</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Ungroup selected</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Ctrl+D</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Duplicate selected</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Ctrl+Shift+D</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Duplicate with offset</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Del / Backspace</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Delete selected</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Ctrl+X / C / V</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Cut / Copy / Paste</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Arrow keys</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Nudge selection (⇧ = 1 mm)</td></tr>
</table>

<h3 class="subheading">Path Operations</h3>
<table style="width:100%;border-collapse:collapse;margin:8px 0;">
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Shift+C</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Close selected polyline(s)</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Shift+O</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Open selected polyline(s)</td></tr>
</table>

<h3 class="subheading">Boolean Operations</h3>
<table style="width:100%;border-collapse:collapse;margin:8px 0;">
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Ctrl+U</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Union (Weld)</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Ctrl+Shift+U</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Subtract</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Ctrl+Alt+U</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Intersect</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Ctrl+Alt+Shift+U</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Divide</td></tr>
</table>

<h3 class="subheading">Application &amp; Navigation</h3>
<table style="width:100%;border-collapse:collapse;margin:8px 0;">
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>⌘+K</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Open command palette (searchable)</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Ctrl+,</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Open Settings</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Ctrl+Shift+N</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">New Window (independent workspace)</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Alt+1 … Alt+4</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Switch to pages (Draft, Pattern, Trace, Convert)</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Alt+5</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Open Repository Sync window</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Esc</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Cancel current tool / exit mode</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Shift+R / Shift+C / Shift+S / Shift+P</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Quick-shape drag (rectangle, circle, slot, hexagon) — Select mode</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>Ctrl+R</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Toggle rulers</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>+</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Zoom in</td></tr>
    <tr><td style="padding:6px;border-bottom:1px solid #30363d;"><strong>−</strong></td><td style="padding:6px;border-bottom:1px solid #30363d;">Zoom out</td></tr>
</table>

<p><em>To view or customize all keyboard shortcuts, open the Keybindings dialog from Settings.</em></p>
"""


def _build_troubleshooting() -> str:
    """Troubleshooting section."""
    return """
<h2 id="troubleshooting" class="section-heading">
    Troubleshooting &amp; Tips
</h2>

<h3 class="subheading">Common Issues</h3>

<h4 class='panel-title'>Pattern generation is slow</h4>
<p>Complex patterns (especially Voronoi with many cells) can take time. Try:</p>
<ul>
    <li>Reducing the cell count</li>
    <li>Using a smaller outline area</li>
</ul>

<h4 class='panel-title'>Pattern doesn't fill the outline</h4>
<p>This usually means your outline is too small for the pattern parameters. Try:</p>
<ul>
    <li>Increasing the outline size</li>
    <li>Decreasing pattern parameters (size, spacing)</li>
    <li>Using the Fit View command (<kbd>F</kbd>) to inspect your outline</li>
</ul>

<h4 class='panel-title'>DXF export has missing geometry</h4>
<p>Ensure your outline is a valid closed polyline. Open or self-intersecting outlines may produce unexpected results.</p>

<h4 class='panel-title'>Trace produces noisy output</h4>
<p>Increase the <strong>minimum feature size</strong> parameter to filter out small artifacts. Use higher-contrast source images.</p>

<h3 class="subheading">Pro Tips</h3>
<ul>
    <li><strong>Use Presets as starting points:</strong> Load a built-in preset, then tweak parameters to get your exact desired look.</li>
    <li><strong>Combine patterns with zones:</strong> Use different patterns in different zones for complex multi-texture designs.</li>
    <li><strong>Add fill on top:</strong> Apply a Lines or Crosshatch fill to add extra surface coverage over your main pattern.</li>
    <li><strong>Preview before generating:</strong> Use the Preview toggle to quickly check results without waiting for full generation.</li>
    <li><strong>Save custom presets:</strong> Once you find a combination you love, save it as a preset for one-click recall.</li>
    <li><strong>Use exclusions for lettering:</strong> Mark text outlines as exclusion cutouts to have patterns flow around them.</li>
    <li><strong>Command palette (⌘K):</strong> Search for any command by name, page, or shortcut — the fastest way to navigate.</li>
    <li><strong>Workspace auto-save:</strong> The app saves named workspaces every 60 seconds and keeps rolling crash-recovery snapshots for unsaved work. Use <strong>File → Recover Workspace…</strong> to restore or permanently delete snapshots; <strong>File → Saved Workspaces…</strong> opens the normal workspace library.</li>
    <li><strong>Send between pages:</strong> Use the "Send to Pattern" button on Draft, or "Send to Draft" / "Send to Pattern" on Trace pages to move geometry between workflows.</li>
</ul>

<h3 class="subheading">Understanding Entity Properties</h3>
<ul>
    <li><strong>Hidden entities</strong> are invisible and excluded from exports — useful for reference geometry.</li>
    <li><strong>Locked entities</strong> cannot be selected or moved — protects important geometry while you work around it.</li>
    <li><strong>Construction entities</strong> are drawn as dashed lines and excluded from DXF export — perfect for alignment guides.</li>
    <li><strong>Groups</strong> allow multiple entities to move as one — use Group (Ctrl+G) and Ungroup (Ctrl+Shift+G).</li>
</ul>

<h3 class="subheading">Selection Techniques</h3>
<ul>
    <li><strong>Click:</strong> Select a single entity.</li>
    <li><strong>Shift+Click:</strong> Add to current selection.</li>
    <li><strong>Drag (no Shift):</strong> Window select — only fully enclosed entities are selected.</li>
    <li><strong>Shift+Drag:</strong> Crossing select — any entity touched by the selection box is selected.</li>
    <li><strong>Groups:</strong> Clicking any entity in a group selects the entire group.</li>
</ul>

<h3 class="subheading">Snap Engine</h3>
<p>The snap engine automatically snaps your cursor to:</p>
<ul>
    <li><strong>Vertices</strong> — corners and endpoints of shapes.</li>
    <li><strong>Edges</strong> — points along shape edges.</li>
    <li><strong>Guides</strong> — horizontal and vertical guide lines you create from rulers.</li>
    <li><strong>Middle points</strong> — midpoints of line segments.</li>
</ul>
<p>Snap can be toggled on/off. When active, a snap indicator appears near your cursor.</p>
"""


def _build_support() -> str:
    """Support section."""
    return """
<h2 id="support" class="section-heading">
    Support &amp; Feedback
</h2>

<h3 class="subheading">Getting Help</h3>
<ul>
    <li><strong>This manual:</strong> You're reading it! Use the search box (top-left of this dialog) to find topics quickly.</li>
    <li><strong>Keyboard Shortcuts dialog:</strong> Open from the Help menu to see a full list of all shortcuts.</li>
    <li><strong>Command Palette (⌘K):</strong> Search for any command by name, page, or shortcut.</li>
    <li><strong>Error Reports:</strong> Simple Stipple includes built-in error reporting — check the Settings to configure it.</li>
    <li><strong>Logging:</strong> Detailed logs are available for debugging (check the app's log directory).</li>
</ul>

<h3 class="subheading">Settings</h3>
<p>Open Settings (the gear icon in the header, or <kbd>Ctrl+,</kbd> / <kbd>⌘,</kbd>) to configure:</p>
<ul>
    <li><strong>Workspace folder</strong> — default location for workspace files.</li>
    <li><strong>Pattern library folder</strong> — where pattern presets are stored.</li>
    <li><strong>Output folders</strong> — default locations for DXF, SVG, and trace outputs.</li>
    <li><strong>Repository folder</strong> — default Git repository directory (Repository Sync window).</li>
    <li><strong>Auto-fetch on startup</strong> — automatically fetch remote repository metadata.</li>
    <li><strong>Check for updates on startup</strong> — silently check for app updates.</li>
    <li><strong>Display units</strong> — millimeters or inches. Changes every ruler, coordinate readout, and numeric-entry field across the app; the underlying geometry always stays in millimeters internally, so switching units never changes your actual drawing.</li>
    <li><strong>Edit shortcuts…</strong> — opens the Keybindings dialog covering every command in the app (draw tools, edit/selection operations, booleans, view/grid toggles, and more), each independently rebindable and searchable via its filter box. "Reset all to defaults" restores the originals.</li>
    <li><strong>Customize radial menu…</strong> — choose which commands appear as wedges in the <kbd>Q</kbd> quick menu (see Draft Page → Quick Radial Menu) and drag to reorder them.</li>
</ul>

<h3 class="subheading">Multiple Windows</h3>
<p>Open a second, fully independent workspace window with <strong>File → New Window</strong> (<kbd>Ctrl+Shift+N</kbd> / <kbd>⌘⇧N</kbd>). Each window has its own open workspace, undo history, and page state — settings, keybindings, and the radial menu configuration are shared across all open windows.</p>

<hr style="border:none;border-top:2px solid #2f81f7;margin:30px 0;">
<p style="text-align:center;color:#8b949e;"><em>Simple Stipple — Precision patterns for precision work.</em></p>
"""


# ── Full HTML content builder ─────────────────────────────────────────────


def build_help_html() -> str:
    """Build the complete help HTML from all sections."""
    sections = [
        _build_getting_started(),
        _build_whats_new_031(),
        _build_draft_page(),
        _build_bezier_pen_tool(),
        _build_dimension_tool(),
        _build_radial_menu(),
        _build_path_cleanup(),
        _build_text_tools(),
        _build_layers(),
        _build_pattern_page(),
        _build_pattern_types(),
        _build_trace_page(),
        _build_convert_page(),
        _build_repo_page(),
        _build_canvas_commands(),
        _build_shortcuts(),
        _build_troubleshooting(),
        _build_support(),
    ]

    return f"""
<style>
    body {{ color: #c9d1d9; }}
    p {{ color: #c9d1d9; line-height: 1.6; }}
    li {{ color: #c9d1d9; margin-bottom: 4px; }}
    strong {{ color: #f0f6fc; }}
    em {{ color: #8b949e; }}
    a {{ color: #58a6ff; }}
    .section-heading {{
        color: #f0f6fc;
        font-size: 22px;
        font-weight: 700;
        border-bottom: 2px solid #2f81f7;
        padding-bottom: 6px;
        margin-top: 28px;
        margin-bottom: 14px;
    }}
    .subheading {{
        color: #79c0ff;
        font-size: 16px;
        font-weight: 600;
        margin-top: 20px;
        margin-bottom: 8px;
    }}
    .panel-title {{
        color: #e6edf3;
        font-size: 13px;
        font-weight: 700;
        margin-top: 14px;
        margin-bottom: 4px;
    }}
    kbd {{
        background-color: #21262d;
        border: 1px solid #30363d;
        border-radius: 4px;
        padding: 1px 6px;
        font-family: Menlo, Consolas, Courier;
        font-size: 12px;
        color: #e6edf3;
    }}
    code {{
        background-color: #161b22;
        border: 1px solid #30363d;
        border-radius: 3px;
        padding: 1px 4px;
        font-family: Menlo, Consolas, Courier;
        color: #79c0ff;
    }}
</style>

<h1 style="text-align:center;color:#f0f6fc;">Simple Stipple — User Manual</h1>
<p style="text-align:center;color:#8b949e;">A powerful laser-engraving pattern generator for DXF files.</p>

<hr style="border:none;border-top:2px solid #2f81f7;margin:20px 0;">

{"".join(sections)}
"""


# ── TOC entries (section id → display label) ───────────────────────────────

TOC_ENTRIES: list[tuple[str, str]] = [
    _toc_entry("getting-started", "Getting Started"),
    _toc_entry("whats-new-031", "What’s New in 0.3.1"),
    _toc_entry("draft-page", "Draft Page"),
    _toc_entry("bezier-pen-tool", "Bezier Pen Tool"),
    _toc_entry("dimension-tool", "Dimension Tool"),
    _toc_entry("radial-menu", "Quick Radial Menu"),
    _toc_entry("path-cleanup", "Path Cleanup Tools"),
    _toc_entry("text-tools", "Text & Typography"),
    _toc_entry("layers", "Layers"),
    _toc_entry("pattern-page", "Pattern Page"),
    _toc_entry("pattern-types", "Pattern Types (20+)"),
    _toc_entry("trace-page", "Trace Page"),
    _toc_entry("convert-page", "Convert / Utilities"),
    _toc_entry("repo-page", "Repository Sync (Git)"),
    _toc_entry("canvas-commands", "Canvas Commands"),
    _toc_entry("keyboard-shortcuts", "Keyboard Shortcuts"),
    _toc_entry("troubleshooting", "Troubleshooting & Tips"),
    _toc_entry("support", "Support & Settings"),
]


# ── Help Dialog ───────────────────────────────────────────────────────────


class HelpDialog(QDialog):
    """Fully-fledged help dialog with searchable table of contents.

    Features:
    - Searchable TOC filter box (filters entries as you type)
    - Splitter between TOC and content (drag to resize)
    - Clickable TOC entries scroll to the corresponding section
    - In-content anchor links update the TOC highlight
    - Content is generated dynamically from the command registry
    """

    def __init__(self, parent: QWidget | None = None, main_window: QMainWindow | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self._main_window = main_window
        self.setWindowTitle("Simple Stipple — User Manual")
        self.setMinimumSize(950, 700)

        # Build content dynamically
        self._html_content = build_help_html()
        self._toc_entries = list(TOC_ENTRIES)
        self._last_find_query: str | None = None

        # Apply theme-aware stylesheet
        self._apply_stylesheet()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header bar ───────────────────────────────────────────────
        header = QFrame()
        header.setObjectName("helpHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 12)

        title_label = QLabel("User Manual")
        title_label.setObjectName("helpTitle")
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setObjectName("helpCloseButton")
        close_btn.setFixedSize(32, 32)
        # A QPushButton inside a QDialog defaults to autoDefault=True, so as
        # the dialog's only button it silently auto-triggers on every Enter
        # press anywhere in the dialog (including the search box) and closes
        # it — even though the search box's own returnPressed handler also
        # fires correctly. Without this, "search then press Enter" always
        # closed the manual before you could see any result.
        close_btn.setAutoDefault(False)
        close_btn.setDefault(False)
        close_btn.clicked.connect(self.close)
        header_layout.addWidget(close_btn)

        root.addWidget(header)

        # ── Splitter: TOC | Content ──────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left panel — TOC with search ─────────────────────────────
        toc_widget = QFrame()
        toc_widget.setObjectName("tocPanel")
        toc_layout = QVBoxLayout(toc_widget)
        toc_layout.setContentsMargins(0, 8, 0, 8)
        toc_layout.setSpacing(4)

        # Search/filter box
        search_label = QLabel("FILTER")
        search_label.setObjectName("tocLabel")
        toc_layout.addWidget(search_label)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search topics… (Enter finds text on the page)")
        self._search_box.setObjectName("tocSearch")
        self._search_box.setClearButtonEnabled(True)
        self._search_box.textChanged.connect(self._filter_toc)
        self._search_box.returnPressed.connect(self._find_in_content)
        toc_layout.addWidget(self._search_box)

        self._toc_list = QListWidget()
        self._toc_list.setObjectName("tocList")

        for section_id, label in self._toc_entries:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, section_id)
            self._toc_list.addItem(item)

        self._toc_list.currentItemChanged.connect(self._on_toc_changed)
        toc_layout.addWidget(self._toc_list)

        splitter.addWidget(toc_widget)

        # ── Right panel — Help content ───────────────────────────────
        self._content = QTextBrowser()
        self._content.setObjectName("helpContent")
        self._content.setHtml(self._html_content)
        self._content.anchorClicked.connect(self._on_anchor_clicked)

        # QFont(str, ...) treats a comma-separated CSS font stack as one
        # (bogus) family name — Qt falls back to its default font, which on
        # some platforms is monospace. Use the families-list constructor,
        # matching the family fallback chain the rest of the app uses.
        font = QFont(["Arial", "Helvetica Neue"], 13)
        self._content.setFont(font)

        splitter.addWidget(self._content)

        # Set initial splitter sizes (TOC : Content ≈ 1 : 3)
        splitter.setSizes([280, 670])
        # Without an explicit stretch factor here, Qt had no basis to keep
        # the header compact — it and the splitter both defaulted to
        # stretch 0, and the header ballooned to fill most of the dialog
        # instead of the content area. Matches the app shell's own header
        # pattern (src/app.py's central_layout.addWidget(self._tabs, stretch=1)).
        root.addWidget(splitter, stretch=1)

    # ── Styling ────────────────────────────────────────────────────────

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
            #tocSearch {
                background: rgba(30, 35, 45, 0.9);
                color: rgba(255, 255, 255, 0.88);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
                margin: 0 8px 4px 8px;
            }
            #tocSearch:focus {
                border-color: rgba(0, 150, 255, 0.5);
            }
            #tocSearch[error="true"] {
                border-color: #f85149;
                background: rgba(248, 81, 73, 0.08);
            }
            #tocSearch::placeholder {
                color: rgba(255, 255, 255, 0.4);
            }
            #tocList {
                background: transparent;
            }
            QListWidget::item:selected {
                background: rgba(0, 150, 255, 0.15);
            }
            #helpContent {
                background: transparent;
                color: rgba(255, 255, 255, 0.88);
                border: none;
                line-height: 1.7;
            }
        """)

    # ── Search / Filter ────────────────────────────────────────────────

    def _filter_toc(self, text: str) -> None:
        """Filter TOC entries based on search text."""
        # Any edit invalidates the in-page find cursor position, so the
        # next Enter press starts a fresh search from the top of the
        # document instead of continuing from wherever a previous, now-
        # stale search left the cursor.
        self._last_find_query = None
        query = text.strip().lower()

        for i in range(self._toc_list.count()):
            item = self._toc_list.item(i)
            if not query:
                item.setHidden(False)
            else:
                label = item.text().lower()
                section_id = item.data(Qt.ItemDataRole.UserRole).lower()
                # Also search the HTML content for matches
                found = query in label or query in section_id
                # Search within the HTML for this section's content
                if not found and self._html_content:
                    try:
                        section_start = f'id="{section_id}"'
                        idx = self._html_content.find(section_start)
                        if idx >= 0:
                            # `id="..."` and `class="section-heading"` are
                            # both attributes of the *same* <h2> tag, so
                            # searching for the next `class="section-heading"`
                            # starting right after `idx` immediately found
                            # this same tag's own class attribute a few
                            # characters later — `section_text` below ended
                            # up being a near-empty slice, and body text
                            # (e.g. "polyline", "fit") never matched anything.
                            # Skip past this tag's closing '>' first so the
                            # search actually looks for the *next* section.
                            tag_end = self._html_content.find(">", idx)
                            search_from = tag_end + 1 if tag_end >= 0 else idx + len(section_start)
                            next_section = self._html_content.find(
                                'class="section-heading"', search_from
                            )
                            if next_section < 0:
                                next_section = len(self._html_content)
                            section_text = self._html_content[idx:next_section].lower()
                            found = query in section_text
                    except Exception:  # noqa: BLE001
                        pass
                item.setHidden(not found)

        # Auto-select first visible item
        for i in range(self._toc_list.count()):
            item = self._toc_list.item(i)
            if not item.isHidden():
                self._toc_list.setCurrentItem(item)
                break

    def _find_in_content(self) -> None:
        """Enter in the search box jumps to, selects (highlighted via the
        app's selection color), and scrolls to the actual matched text on
        the page — the TOC filter above only narrows down which *section*
        to open, it doesn't locate a specific word within one.

        The first Enter after typing a new query always finds the first
        match from the top of the document (not from wherever the cursor
        last happened to be, e.g. after TOC navigation); pressing Enter
        again with the same query advances to the next match and wraps
        around once the end is reached.
        """
        query = self._search_box.text().strip()
        if not query:
            return
        if query != self._last_find_query:
            cursor = self._content.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self._content.setTextCursor(cursor)
            self._last_find_query = query
        found = self._content.find(query)
        if not found:
            # Wrap around: reset to the document start and retry once so
            # repeated Enter presses cycle instead of dead-ending.
            cursor = self._content.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self._content.setTextCursor(cursor)
            found = self._content.find(query)
        self._search_box.setProperty("error", not found)
        self._search_box.style().unpolish(self._search_box)
        self._search_box.style().polish(self._search_box)

    # ── TOC interaction ────────────────────────────────────────────────

    def _on_toc_changed(self, current: QListWidgetItem | None) -> None:
        if current is None:
            return
        section_id = current.data(Qt.ItemDataRole.UserRole)
        self._scroll_to_section(str(section_id))

    def _on_anchor_clicked(self, url: QUrl) -> None:
        anchor = url.fragment()
        if anchor:
            self._scroll_to_section(anchor)

    def _scroll_to_section(self, section_id: str) -> None:
        self._content.scrollToAnchor(section_id)

        # Highlight the corresponding TOC entry (considering filtered items)
        for i in range(self._toc_list.count()):
            item = self._toc_list.item(i)
            if item.isHidden():
                continue
            if item.data(Qt.ItemDataRole.UserRole) == section_id:
                self._toc_list.setCurrentItem(item)
                # Scroll TOC to show the item
                self._toc_list.scrollToItem(item)
                break

    # ── Public API ─────────────────────────────────────────────────────

    @classmethod
    def show_help(
        cls, parent: QWidget | None = None, main_window: QMainWindow | None = None
    ) -> HelpDialog:
        """Show the help dialog. Returns the dialog instance."""
        dialog = cls(parent, main_window)
        dialog.exec()
        return dialog
