"""Command reference, troubleshooting, support, and production workflow sections."""

from __future__ import annotations

from .overview import _esc


def _build_canvas_commands() -> str:
    """Canvas command reference from the actual registry."""
    try:
        from simple_stipple.editor import commands as cmd_mod

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
    <li><strong>Lettering:</strong> Give text outlines the <em>Cut only</em> treatment and the surrounding pattern flows around them.</li>
    <li><strong>Command palette (⌘K):</strong> Search for any command by name, page, or shortcut — the fastest way to navigate.</li>
    <li><strong>Workspace auto-save:</strong> The app saves named workspaces every 60 seconds and keeps rolling crash-recovery snapshots for unsaved work. Use <strong>File → Recover Unsaved Work…</strong> to restore or permanently delete snapshots; <strong>File → Workspaces and Recovery…</strong> opens the normal workspace library.</li>
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
    <li><strong> Feel free to reach out to me!!</strong>
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

<h3 class="subheading">Installing App Updates</h3>
<p>Choose <strong>Help → Check for Updates</strong>. Downloads are verified against the
release's SHA-256 digest before installation. On Windows, choose <strong>Download &amp;
Install</strong>, then <strong>Yes</strong> when asked to restart; Simple Stipple closes,
replaces the installed EXE with a rollback-safe handoff, and reopens automatically. On
macOS, the verified disk image opens so you can replace the app bundle.</p>

<h3 class="subheading">Multiple Windows</h3>
<p>Open a second, fully independent workspace window with <strong>File → New Window</strong> (<kbd>Ctrl+Shift+N</kbd> / <kbd>⌘⇧N</kbd>). Each window has its own open workspace, undo history, and page state — settings, keybindings, and the radial menu configuration are shared across all open windows.</p>

<hr style="border:none;border-top:2px solid #2f81f7;margin:30px 0;">
<p style="text-align:center;color:#8b949e;"><em>Simple Stipple — Precision patterns for precision work.</em></p>
"""


# ── Full HTML content builder ─────────────────────────────────────────────


def _build_production_workflows() -> str:
    """Task-first routes for the capabilities added across the application."""
    return """
<h2 id="production-workflows" class="section-heading">Production Workflows</h2>
<p>Use these routes when you know the outcome you need, not the feature name.</p>
<h3 class="subheading">Vector artwork → pattern DXF</h3>
<ol><li>In <strong>Draft</strong>, use <strong>Import…</strong> and choose whether to replace or add geometry.</li><li>Clean up paths, then send closed outlines to Pattern.</li><li>Choose pattern and fill, inspect Preview, choose <strong>Format</strong>, then use the single main <strong>Export</strong> button.</li></ol>
<h3 class="subheading">Image → vector outline</h3>
<ol><li>In <strong>Trace</strong>, add a high-contrast image and set physical width.</li><li>Tune threshold and minimum feature size while inspecting the preview.</li><li>Send the result to Draft for cleanup or Pattern for texture, then export from that destination.</li></ol>
<h3 class="subheading">Image engraving inside an outline</h3>
<ol><li>Load or draw a closed Pattern outline, then open <strong>Advanced controls → Image Engraving</strong>.</li><li>Add the image. It is proportionally fitted and centered in the outline; drag its overlay or use placement fields to refine it.</li><li>Choose Whole outline or a zone clip, review laser settings, select <strong>Use engraving export</strong>, then use main Export.</li></ol>
<p><strong>Safety:</strong> material profiles are starting values. Frame the job and test on scrap.</p>
<h3 class="subheading">Precision and construction</h3>
<p>The precision bar controls grid, object snaps, construction geometry, and constraints. Use snap strength to make capture looser or stronger; set it to zero for a temporary freehand pass without losing your saved snap choices. Apply horizontal, vertical, parallel, perpendicular, equal, coincident, midpoint, symmetric, intersection, fixed, projection, and compatible circular/curve constraints from Geometry Constraints or the command palette.</p>
<h3 class="subheading">Shapes, arrays, and context actions</h3>
<p>Right-click the canvas for selection-aware actions. Beyond standard Draw tools, the context and radial menus can create rings, gears, spirals, teardrops, keyholes, superellipses, rounded/chamfered stars, and joinery layouts. Use Align, Distribute, Grid array, Radial array, Group, and Move to layer instead of manually nudging a repeated layout.</p>
"""
