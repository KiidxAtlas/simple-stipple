# Simple Stipple

Desktop app for drafting, tracing, and generating pattern fills for laser/vector workflows.

The source layout and placement rules are documented in
[ARCHITECTURE.md](ARCHITECTURE.md). That file is the maintained project memory
for module responsibilities and dependency boundaries.

## StarFX FVI workflow

The Draft page has one **Import Vector** entry point for opening, adding, or
dragging DXF, FVI, and SVG files onto the canvas. Use the **⋯** beside the
right-panel **Export DXF** button to open the configurable FVI exporter. The export dialog controls
program origin, margin, coordinate precision, Y orientation, travel optimization,
open-path reversal, native arc preservation, and comments.

FVI support is intentionally geometry-only: `MOVEDIST`, `DRAWLINE`, and
`DRAWARC`. Hardware I/O, loops, file calls, laser parameters, and Z-axis motion
are reported but never executed or generated. Always verify an exported program
with StarFX's red trace/profile preview before enabling the laser.

## Run locally

```bash
python main.py
```

## Install as app command

```bash
pipx install .
simple-stipple
```

## Optional CAD extras

```bash
pip install .[cad]
```

## Release both desktop artifacts from macOS

From your Mac, create and push a version tag to trigger the GitHub Actions release workflow for both platforms:

```bash
./scripts/release.sh v0.3.4
```

That single tag push builds and publishes:

- Windows executable: `SimpleStipple.exe`
- macOS disk image: `SimpleStipple-macOS.dmg`

If you prefer manual steps, do the equivalent:
