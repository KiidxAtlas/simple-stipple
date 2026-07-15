# Changelog

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

