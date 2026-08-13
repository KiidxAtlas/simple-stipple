"""Onboarding, drafting, and general-operation manual sections."""

from __future__ import annotations

from html import escape as _html_escape


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

<h3 class="subheading">Before You Start</h3>
<ul>
    <li>Use <strong>Draft</strong> to draw or clean geometry, <strong>Pattern</strong> to fill an outline, <strong>Trace</strong> to turn an image into paths, and <strong>Convert</strong> for file-format utilities.</li>
    <li>Save a workspace when you want to return to the same pages, geometry, and settings later.</li>
    <li>Simple Stipple prepares geometry; it does not control laser hardware. Always preview and verify exported files in your machine software before enabling the laser.</li>
</ul>

<h3 class="subheading">Quick Start — Your First Pattern</h3>
<ol>
    <li><strong>Open the Pattern page</strong> from the tab bar at the top (or press <kbd>Alt+2</kbd>).</li>
    <li><strong>Load an outline</strong> — either draw one using the drawing tools (press <kbd>D</kbd>) or import a DXF, FVI, or SVG file via the <strong>Browse…</strong> button.</li>
    <li><strong>Select a pattern</strong> from the dropdown (e.g., "Honeycomb").</li>
    <li><strong>Adjust parameters</strong> — size, gap, spacing — using the sidebar controls.</li>
    <li><strong>Click "Generate"</strong> to create the pattern. Use the preview toggle to compare before/after.</li>
    <li><strong>Export</strong> the result as a DXF file for your laser cutter.</li>
</ol>

<h3 class="subheading">The Four Pages</h3>
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


def _build_common_tasks() -> str:
    """Goal-first routes for users who do not know the feature names."""
    return """
<h2 id="common-tasks" class="section-heading">What Do You Want to Do?</h2>
<p>Start with the result you want. The control names are included so you can
also find them with the command palette (<kbd>Ctrl+K</kbd>/<kbd>⌘K</kbd>).</p>
<table style="width:100%; border-collapse:collapse; margin:12px 0;">
  <tr style="background-color:#1c2e4a;">
    <th style="padding:8px;text-align:left;">Goal</th>
    <th style="padding:8px;text-align:left;">Where to start</th>
  </tr>
  <tr><td style="padding:7px;border-bottom:1px solid #30363d;">Draw a precise part</td><td style="padding:7px;border-bottom:1px solid #30363d;"><strong>Draft</strong> → Draw, then use Grid, Snap, Constraints, and Dimensions.</td></tr>
  <tr><td style="padding:7px;border-bottom:1px solid #30363d;">Round or bevel a corner</td><td style="padding:7px;border-bottom:1px solid #30363d;">Hover the vertex in Draft → <strong>Round Corner</strong> or <strong>Chamfer Corner</strong>.</td></tr>
  <tr><td style="padding:7px;border-bottom:1px solid #30363d;">Fill a shape for engraving</td><td style="padding:7px;border-bottom:1px solid #30363d;"><strong>Pattern</strong> → load an outline → choose a pattern → Generate.</td></tr>
  <tr><td style="padding:7px;border-bottom:1px solid #30363d;">Keep lettering or holes clear</td><td style="padding:7px;border-bottom:1px solid #30363d;"><strong>Pattern</strong> → give the inner region the <em>Cut only</em> treatment.</td></tr>
  <tr><td style="padding:7px;border-bottom:1px solid #30363d;">Turn an image into vectors</td><td style="padding:7px;border-bottom:1px solid #30363d;"><strong>Trace</strong> → load image → tune threshold → Send to Draft or Pattern.</td></tr>
  <tr><td style="padding:7px;border-bottom:1px solid #30363d;">Repair or change a file format</td><td style="padding:7px;border-bottom:1px solid #30363d;"><strong>Convert</strong> for FVI, DXF, and SVG conversion or repair.</td></tr>
  <tr><td style="padding:7px;border-bottom:1px solid #30363d;">Recover lost work</td><td style="padding:7px;border-bottom:1px solid #30363d;"><strong>File → Recover Unsaved Work…</strong></td></tr>
  <tr><td style="padding:7px;border-bottom:1px solid #30363d;">Change units or controls</td><td style="padding:7px;border-bottom:1px solid #30363d;"><strong>Settings</strong> → Display &amp; Interaction or Keybindings.</td></tr>
</table>
"""


def _build_files_and_recovery() -> str:
    """Explain the complete file lifecycle and recovery choices."""
    return """
<h2 id="files-recovery" class="section-heading">Files, Workspaces &amp; Recovery</h2>
<h3 class="subheading">Workspace or export file?</h3>
<ul>
  <li><strong>Workspace:</strong> preserves pages, editable geometry, parameters, and app state so you can continue later.</li>
  <li><strong>DXF/SVG/FVI export:</strong> creates an interchange or production file. It is not a complete editable workspace.</li>
</ul>
<h3 class="subheading">Safe working routine</h3>
<ol>
  <li>Use <strong>File → Save Workspace</strong> early and give the job a recognizable name.</li>
  <li>Use <strong>File → Workspaces and Recovery…</strong> to browse and reopen named jobs.</li>
  <li>Export only after fitting the view, checking layers, closed outlines, region treatments, and dimensions.</li>
  <li>Open the exported file in your machine software and verify scale, units, layers, and operation order.</li>
</ol>
<h3 class="subheading">Autosave and crash recovery</h3>
<p>Named workspaces autosave periodically. Unsaved work receives rolling recovery
snapshots. Choose <strong>File → Recover Unsaved Work…</strong> after a crash or accidental
close; inspect a snapshot before restoring or permanently deleting it.</p>
<h3 class="subheading">Importing and moving work</h3>
<p>Draft accepts vector geometry for editing. Trace accepts bitmap images. Use
<strong>Send to Draft</strong> or <strong>Send to Pattern</strong> when the next step is in
another page; this keeps the geometry inside the current workspace.</p>
<p>StarFX FVI conversion supports geometry commands including <code>MOVEDIST</code>,
<code>DRAWLINE</code>, and <code>DRAWARC</code>. Machine-control commands are reported
as unsupported rather than silently converted.</p>
"""


def _build_precision_editing() -> str:
    """Task-focused drafting and editing reference."""
    return """
<h2 id="precision-editing" class="section-heading">Select, Edit &amp; Draw Precisely</h2>
<h3 class="subheading">Selection</h3>
<ul>
  <li><strong>Click</strong> selects one entity; <strong>Shift+click</strong> adds or removes one.</li>
  <li>A left-to-right window selects fully enclosed objects. A crossing window selects touched objects.</li>
  <li>Lock reference geometry when it must remain visible but uneditable; hide layers to reduce selection clutter.</li>
</ul>
<h3 class="subheading">Corners, paths, and shapes</h3>
<ul>
  <li><strong>Round / fillet:</strong> hover the target corner, run <strong>Round Corner</strong>, then enter the radius.</li>
  <li><strong>Chamfer / bevel:</strong> hover the target corner, run <strong>Chamfer Corner</strong>, then enter the setback.</li>
  <li><strong>Offset:</strong> select a path and run <strong>Offset Path</strong>; use a positive or negative distance for the required side.</li>
  <li><strong>Boolean operations:</strong> select overlapping closed shapes, then Union, Subtract, Intersect, or Divide.</li>
  <li><strong>Edit vertices:</strong> enter Edit mode; drag nodes and handles, or double-click an edge to insert a vertex.</li>
</ul>
<h3 class="subheading">Accuracy tools</h3>
<ul>
  <li><strong>Grid</strong> gives regular increments; <strong>object snaps</strong> acquire endpoints, midpoints, and edges.</li>
  <li><strong>Constraints</strong> preserve relationships such as horizontal, vertical, parallel, perpendicular, equal length, coincident, and fixed.</li>
  <li><strong>Dimensions</strong> make lengths, angles, and radii explicit. Numeric fields accept arithmetic and mixed units such as <code>25/2</code> or <code>1in + 3mm</code>.</li>
  <li><strong>Construction geometry</strong> supports alignment and is excluded from production export.</li>
</ul>
"""


def _build_settings_updates() -> str:
    """Explain navigation and operational settings in user language."""
    return """
<h2 id="settings-updates" class="section-heading">Settings, Shortcuts &amp; Updates</h2>
<p>Settings opens with <strong>All settings</strong> visible. Use the section selector
to jump to one category without losing access to the complete list.</p>
<ul>
  <li><strong>Files &amp; folders:</strong> workspace, pattern library, export, trace, and repository locations.</li>
  <li><strong>Display &amp; interaction:</strong> millimeters/inches and canvas behavior. Changing display units never rescales geometry.</li>
  <li><strong>Draft and Trace defaults:</strong> initial tool, smoothing, and tracing behavior for new work.</li>
  <li><strong>Keybindings:</strong> search commands, reassign shortcuts, or reset defaults.</li>
  <li><strong>Radial menu:</strong> choose and reorder the commands shown by <kbd>Q</kbd>.</li>
  <li><strong>Updates:</strong> enable startup checks or use <strong>Help → Check for Updates</strong>. Windows downloads, verifies, replaces, and reopens the app automatically; macOS opens the verified disk image.</li>
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
