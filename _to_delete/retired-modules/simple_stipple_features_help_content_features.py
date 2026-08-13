"""Manual sections for the Pattern, Trace, Convert, and Repository features."""

from __future__ import annotations


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

<h3 class="subheading">Adjusting Pattern Values</h3>
<p>Drag a parameter slider for quick adjustment, or type an exact value in its field. Slider-driven decimal values are rounded to <strong>two decimal places</strong>, so a drag produces readable values such as <code>1.05</code> rather than long floating-point numbers. Integer controls, such as a seed or cell count, remain whole numbers.</p>

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
<p>A region with a treatment of its own is <em>automatically</em> subtracted from the region containing it, so a pattern always flows around inner shapes. Use <strong>Cut only</strong> for lettering, logos, or mechanical holes that should be cut but not filled.</p>

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
