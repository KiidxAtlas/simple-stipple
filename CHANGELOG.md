# Changelog

## 0.3.3 — 2026-07-16

### Fixed

- Resolved all repository-wide MyPy diagnostics across backend, DXF, pattern, trace, workspace, and Qt UI boundaries.
- Corrected concrete typing defects involving DXF units, SVG transforms, image conversion, pattern polygonization, workspace models, and nullable Qt widgets.
- Added explicit typing boundaries for the canvas's cooperative Qt mixin architecture.

### Changed

- CI now runs MyPy over all 94 source files instead of two selected modules.
- Compatibility ignores no longer fail inconsistently when third-party type coverage differs across supported Python versions.

## 0.3.2 — 2026-07-16

### Added

- Zone-first pattern editing with clearer assignment, selection highlighting, role controls, and per-zone output ownership.
- Auto-preview controls, cancellable pattern generation, persistent preview edits, and reusable pattern defaults.
- Recovery management for unsaved work, including safer snapshot retention and clearer recovery timestamps.
- File-size limits for JSON persistence and regression coverage for oversized workspace data.

### Fixed

- Pattern selections, cutouts, deleted cells, and newly drawn outlines failing to survive preview regeneration.
- Voronoi preview generation crashing when non-finite geometry or gap values reached Shapely.
- The quality workflow type-checking a task-state module that had moved to the Pattern worker boundary.
- Hidden application windows opening a blocking recovery dialog during background startup processing.
- Workspace loads leaving partially applied state when a page failed, and Save As adopting a path before the write succeeded.
- DXF/FVI/SVG import and export edge cases affecting curve fidelity, layer roles, and destination handling.
- Canvas selection, panning, HUD text, keybindings, properties, and workspace round-trip inconsistencies.

### Changed

- Conversion workflows now confirm replacements and create non-destructive repaired copies by default.
- Refined Pattern, Convert, Trace, Draft, precision, status, settings, and layer-tree layouts and feedback.
- Updated application identity, accessibility labels, help content, and workspace save-state messaging.

## 0.3.1 — 2026-07-15

### Added

- Persistent command lifecycle guidance and a consolidated precision/sketch palette.
- Contextual Properties actions and property-to-canvas highlighting.
- Local-axis manipulators and metadata-preserving resizing for parametric shapes.
- Direct circle and ellipse control editing, plus reliable slot rotation and resizing.
- Parallel/perpendicular inference and exact Trim/Extend hover previews.
- Arithmetic and mixed-unit expressions in Properties, canvas HUDs, dimensions, and grid spacing.
- Unified DXF, SVG, and FVI vector import plus configurable StarFX FVI export.
- Expanded SVG/DXF fidelity, FVI diagnostics, geometry preflight, and curve export fidelity.
- Pattern preset management, role-aware output, responsive cancellation, and stale-result protection.
- Larger interaction targets, cleaner sidebar grouping, edge-aware contextual controls, and spatially anchored feedback.

### Fixed

- Rotation and resize gizmos breaking bounding boxes or failing on parametric shapes.
- Slot gizmo rotation and live Properties angle updates.
- Vertex insertion failures when double-clicking editable path edges.
- Properties failing to recognize shapes created with Polyline.
- Horizontal overflow in the Draw sidebar.
- Repeated macOS `GB18030 Bitmap` font fallback warnings during canvas inference painting.

### Changed

- Consolidated small settings, notification, units, page runtime, and operation modules without adding source-directory sprawl.
- Updated in-app Help with the complete 0.3.1 drafting, interoperability, and workflow feature set.
