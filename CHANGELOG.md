# Changelog

## Unreleased

### Release readiness

- Expanded the repository landing page with download CTAs, workflow guidance, safety notes,
  support routes, and development instructions.
- Added contributor, security, release checklist, issue-form, and pull-request guidance.
- Hardened desktop publication so tagged releases require macOS signing and notarization,
  publish both platform artifacts together, include checksums, and use the matching changelog
  section as release notes.
- Added a release-notes action to the About dialog.
- Resolved the repository's Pyright errors across dynamic Qt mixins and geometry typing boundaries.
- Added a visible Buy Me a Coffee link to the app's Support dialog and repository metadata.

## 0.3.6 — 2026-08-12

### Added

#### Product workflows

- Added a rebuilt, searchable in-app Help system as the `features.help` package, with a
  dedicated dialog and browsable sections for getting started, Draft, Pattern, Trace,
  Convert, Repository, commands, shortcuts, production safety, troubleshooting, settings,
  updates, recovery, and support.
- Added a Support dialog reachable from the Help menu.
- Added structured Convert task forms for FVI conversion, DXF repair, SVG conversion, and
  shared task-form behavior, replacing the previous single oversized task implementation.
- Added named background-job builders for Pattern preview work and Trace work so page classes
  retain UI coordination while jobs carry immutable processing inputs.
- Added a named Draft DXF export-plan builder that collects dimensions, layer order, entity
  kinds, metadata, and extra layers before serialization.
- Added a named Trace outlined-DXF export workflow covering geometry preflight, destination
  selection, metadata-aware export, reveal availability, and visible result status.
- Added a reusable imported-SVG backdrop workflow for Draft. SVG artwork embedded alongside
  imported paths is placed as editable reference art, updates status while transformed, and
  can be removed through the existing canvas interaction.
- Added pure Pattern outline helpers for import/transfer normalization, stable-ID
  reconciliation, canvas-record and layer construction, bounds, and smallest-containing
  region selection.
- Added dedicated Pattern outline-file import routing for DXF, FVI, and SVG assets.

#### Editor and geometry

- Added `editor.rendering.dense_preview`, which owns retained-path batching and overscan
  raster-cache behavior for dense previews while preserving the renderer's scene ordering.
- Added `editor.view.preferences`, which owns CanvasView grid, snap, context-menu,
  presentation, zoom, and cursor-status preferences while retaining CanvasView's public Qt
  surface.
- Added `engine.cad.curves` for authored spline and Bezier control-point behavior without
  changing ShapeFactory's serialization/deserialization role.
- Added focused editor modules for model ownership, document bridging, objects, hit testing,
  grouping, layer services, tool bases, dimension backend, and radial-menu behavior.
- Added engine-focused geometry, imaging, and editing implementations as the canonical homes
  for JIT, spatial indexing, Voronoi, raster processing, tracing, boolean operations, and
  clipping.

#### Quality and documentation

- Added focused regression suites for document replacement, SVG backdrops, dense preview
  caching, editor hit testing, view preferences, engine behavior, Pattern outline state,
  anisotropic shape scaling, Trace DXF export, and phase characterization.
- Added architecture guards for canonical module homes, direct and dynamic imports, feature
  isolation, Qt-free engine/document boundaries, and retired module absence.
- Expanded [ARCHITECTURE.md](ARCHITECTURE.md) into an onboarding guide with the eight
  capability homes, dependency rules, lifecycle diagrams, execution paths, placement guide,
  extension seams, and release-verification expectations.

### Changed

#### Application structure

- Completed the capability-first runtime consolidation. The canonical top-level homes are now
  `app`, `document`, `editor`, `engine`, `features`, `platform`, `resources`, and `ui`.
- Renamed the reusable vector-editor package from `canvas` to `editor`, including the widget,
  renderer, runtime, commands, snapping, dialogs, layers, operations, tools, views, and
  widgets. Application code, tests, dynamic imports, monkeypatch targets, frozen-build
  configuration, and module-home checks now use `editor`.
- Consolidated pure geometry, imaging, and editing code under `engine`; all ordinary consumers
  now use the focused engine modules rather than a parallel domain facade.
- Moved shared UI services to their final semantic homes:
  `ui.components.units`, `ui.components.notifications`, `ui.components.recent`, and
  `ui.dialogs.files`. Platform path policy now lives in `platform.settings`.
- Reorganized Pattern internals into named responsibilities: form widgets/specification,
  regions/treatments, export jobs/output, outline I/O/state, preview jobs, and inline preset
  behavior.
- Reorganized Help from one module into a feature package with content, assembly, and dialog
  responsibilities so the manual stays browsable without becoming a single large source file.
- Reorganized Convert form construction into dedicated FVI, repair, and SVG task-form modules.
- Reorganized Draft, Pattern, Trace, renderer, view, and CAD coordinators by extracting only
  cohesive responsibilities while preserving their existing public callback/action surfaces.
- Updated packaging, hidden-import handling, package exports, launcher imports, and module
  metadata for the final canonical package tree.

#### Canvas, document, and file behavior

- Kept the document service as the command-oriented mutation boundary while moving its
  geometry calls to the focused CAD/editing APIs.
- Clarified transform semantics: command-based scaling is uniform; the editor's preview and
  commit path remains responsible for non-uniform gizmo scaling and metadata preservation.
- Updated DXF, SVG, and FVI adapters to use the canonical engine geometry/imaging/editing
  implementations and preserve the tested round-trip/import behavior after consolidation.
- Updated Draft export preparation to consistently preserve dimensions, layers, entity kinds,
  and metadata through a single export plan.
- Updated Pattern engraving-job construction and preview worker invocation to take explicit
  parameter snapshots rather than relying on page-owned implementation details.

#### User interface and accessibility

- Tightened the Draw sidebar: resize gutters are now 12 px instead of 24 px, outer/content
  margins and section spacing are reduced, and tool grids use compact gaps. This returns useful
  width to the two-column tool palette and reduces unnecessary vertical whitespace.
- Added the semantic `precision-control` theme role and applied it to Pan, Grid, Snap,
  Construction, and Constraints controls so the precision bar uses compact, consistent padding
  rather than inheriting oversized general-purpose button spacing.
- Made Pattern preset actions fit narrow inspectors without truncation: `Apply Preset` is now
  context-aware `Apply`, `Options` is now `More`, fixed action widths were removed, and both
  actions have explicit accessible names/tooltips.
- Kept long Pattern outline paths readable by placing the line-edit cursor at the beginning
  after selection, drop, recent-file load, reload, and workspace restore; the full path remains
  available in the field tooltip.
- Made recent-file controls use a minimum-width layout policy so their label and disclosure
  affordance do not compete with adjacent file actions.
- Pattern parameter sliders now round decimal values to two places. Dragging a slider shows
  readable values such as `1.05` instead of floating-point interpolation artifacts.
- Updated the Help manual and changelog references for two-decimal Pattern slider precision.

### Fixed

- Fixed clipped Pattern preset labels in narrow side panels.
- Fixed long selected vector paths appearing horizontally scrolled to an unhelpful trailing
  fragment instead of their leading context.
- Fixed Draw sidebar padding and resize chrome consuming disproportionate canvas/tool space.
- Fixed compact precision-bar controls inheriting spacing that could crowd labels and menu
  indicators at narrower widths.
- Fixed workspace Pattern-path restoration to preserve readable path presentation while
  retaining a safe fallback for minimal non-Qt state harnesses.
- Fixed direct imports, string-based imports, frozen-build imports, and test monkeypatch paths
  that still referenced retired `canvas`, `domain`, UI-facade, or platform-path modules.
- Fixed Help and architecture documentation drift so the documented package tree uses
  `editor`, not the retired `canvas` name.

### Removed

- Removed the retired `canvas/` package after migration to `editor/`.
- Removed the retired parallel `domain/` facade tree after restoring its focused engine
  implementations.
- Removed retired shared-UI facades: `ui.units`, `ui.notifications`, `ui.recent`, and
  `ui.files`.
- Removed the retired `platform.paths` facade in favor of `platform.settings`.
- Removed obsolete geometry-service facade code now represented by focused engine modules.
- Removed superseded restructuring inventories and stale release documentation that described
  the previous package layout.

### Breaking changes for integrations

- Python integrations importing `simple_stipple.canvas.*` must import the corresponding
  `simple_stipple.canvas.*` module instead.
- Python integrations importing `simple_stipple.domain.*` must import the corresponding
  `simple_stipple.core.{geometry,imaging,editing}` module instead.
- Python integrations importing `simple_stipple.ui.{units,notifications,recent,files}` must
  use `simple_stipple.ui.components.{units,notifications,recent}` or
  `simple_stipple.ui.dialogs.files` instead.
- Python integrations importing `simple_stipple.platform.paths` must use
  `simple_stipple.platform.settings` instead.
- The Help implementation is now imported from `simple_stipple.features.help` rather than the
  retired single-file `simple_stipple.features.help.py` module; its package-level public API is
  retained.

### Verification

- Added and updated focused regression coverage for the new module homes, behavior seams,
  slider precision, long-path presentation, compact preset actions, compact precision roles,
  Draw-sidebar resize space, Help content, and architecture boundaries.
- `ruff check src tests`, `git diff --check`, focused offscreen UI regression tests, and the
  canonical dependency/module-home suites pass for the current changes. Some local combined
  Qt runs may still be affected by the documented PySide6 `QListWidgetItem` teardown crash; an
  incomplete runner summary is not represented as a full-suite pass.

## 0.3.5 — 2026-07-27

Baseline: `v0.3.1` (`c000273`, 2026-07-15)

Release preparation state: 2026-07-27

This is the cumulative release record for everything changed after `v0.3.1`, including
the tagged `v0.3.2`, `v0.3.3`, and `v0.3.4` releases plus the work prepared for `v0.3.5`.
The committed `v0.3.1..HEAD` range alone touches 293 files with approximately 39,215
additions and 31,516 removals; the current working tree adds the latest architecture,
Help, Settings, layout, precision, and updater improvements described below.

### Features added since v0.3.1

#### New user workflows

- **Trace workspace** for converting raster images into editable vector outlines with live
  preview, threshold/Canny processing, smoothing, area filters, size controls,
  cancellation, workspace persistence, and DXF export.
- **Convert workspace** with FVI-to-DXF, DXF repair, DXF-to-SVG, SVG-to-DXF, batch
  conversion, previews, readiness checks, cancellation, and non-destructive outputs.
- **Repository Sync** window for selecting a repository, viewing status, fetching, pulling,
  committing, and pushing without leaving the application.
- **Multi-window editing** with independent documents, page state, undo history, workspace
  paths, and recovery state.

#### New drawing and CAD tools

- **Bezier Pen** for creating smooth editable paths.
- **Dimension tool** for linear, angular, radius, and reference measurements.
- **Text tools** for multiline text and text placed along paths.
- **Quick-shape shortcuts** for drag-created rectangles, circles, and slots.
- **Parametric shape recognition** for converting suitable polylines into editable
  rectangles, circles, ellipses, arcs, rounded rectangles, and slots.
- **Construction geometry** and guides.
- **Geometric constraints** including horizontal, vertical, parallel, perpendicular,
  equal-length, coincident, and fixed relationships.
- **Curvature visualization** and **geometry-health diagnostics**.
- **Trim and Extend previews** showing the exact result before committing.
- **Boolean operations**: union, difference, intersection, and XOR.
- **Path operations**: offset, split, merge, explode, resample, simplify, smooth,
  open/close, duplicate, array, align, distribute, mirror, rotate, and scale.
- **Local-axis transform gizmos** for rotated and parametric shapes.
- **Direct vertex insertion** by double-clicking editable path edges.

#### New snapping and precision capabilities

- Endpoint, midpoint, center, edge, curve, intersection, tangent, extension,
  equal-length, and axis-alignment snapping.
- Parallel, perpendicular, and angle relationship inference.
- Configurable rotation snapping and grid spacing.
- Arithmetic expressions and mixed `mm`/`in` input in supported numeric fields.
- Persistent command guidance describing the next expected click, selection, or key.
- A consolidated precision bar for grid, snapping, construction, and constraints.

#### New Pattern capabilities

- Zone-first pattern assignment and editing.
- Per-zone pattern type, parameters, roles, output ownership, and selection highlighting.
- Cutouts, cell deletion, regenerated-outline persistence, and retained preview edits.
- More than 20 built-in procedural pattern choices.
- Custom tile DXF assets and motif management.
- Pattern preset manager with save, rename, duplicate, import, export, reset, and built-in
  preset seeding.
- Automatic preview, explicit generation, cancellable workers, and stale-result rejection.
- Role-aware layered pattern output.
- Raster engraving controls and combined vector/raster LaserStar packages.

#### New import, export, and machine-format capabilities

- Unified Draft import for DXF, SVG, and FVI, including replace, add, recent files, and
  drag/drop.
- SVG import/export with transforms, nested groups, view boxes, curves, sizing, and DXF
  round trips.
- Geometry-only StarFX FVI import/export supporting `MOVEDIST`, `DRAWLINE`, and `DRAWARC`.
- Configurable FVI origin, margins, coordinate precision, Y orientation, travel
  optimization, path reversal, native arcs, and comments.
- Unsupported FVI command diagnostics rather than silent execution or data loss.
- LaserStar vector/raster package export.
- DXF import preview with layer inspection.
- DXF repair and validation reports.
- Layer- and role-aware DXF/SVG/FVI output.
- Curve re-tessellation at export for improved spline and parametric-shape fidelity.

#### New workspace and persistence capabilities

- Schema-validated workspace documents.
- Save, Save As, recent workspaces, and reusable workspace library.
- Automatic recovery snapshots after crashes or unexpected exits.
- Recovery selection with timestamps and safer retention.
- Stable entity IDs across document mutations and serialization.
- Command-oriented document changes with undo/redo history.
- Transactional workspace loading that avoids partially applied state.
- Persistence size limits and clearer malformed-file errors.

#### New application and interface capabilities

- Searchable **Command Palette**.
- Customizable **keyboard shortcuts**.
- Configurable **Quick Radial Menu**.
- Configurable Draw sidebar sections and tools.
- Configurable canvas context-menu sections and overflow.
- Schema-driven Settings for appearance, interface scale, control density, units, paths,
  snapping, smoothing, accessibility, updates, and Trace defaults.
- High-contrast indicators, reduced-motion behavior, persistent notifications, accessible
  labels, visible focus handling, and larger interaction targets.
- Recent-file menus for vector and image workflows.
- Import preview, FVI export, LaserStar export, multi-paste, text, update, customization,
  shape recognition, preset manager, and workspace library dialogs.
- Searchable in-app user manual organized around user tasks.
- Update checking on startup.
- Verified in-app downloads using GitHub release SHA-256 digests.
- Seamless Windows EXE replacement and automatic restart with rollback protection.

#### New engineering and maintainability capabilities

- Installed `src/simple_stipple` package and `python -m simple_stipple` entry point.
- Capability-first `app`, `document`, `engine`, `features`, `canvas`, `ui`, `platform`,
  and `resources` package architecture.
- Executable dependency, Qt-isolation, feature-boundary, and canonical-module-home rules.
- Circular-import scanner and retrieval-oriented project architecture map.
- Packaged DXF tiles, QSS, and SVG icon resources.
- Repository-wide Ruff, MyPy, Pyright/Pylance, complexity, packaging, import, and
  offscreen-UI validation.
- Numba-accelerated geometry paths, pyclipper-backed editing operations, and optional
  Sentry crash reporting.

### Product workflows

- Rebuilt Draft as an interaction-first 2D CAD workspace with:
  - Polyline, spline, Bezier pen, rectangle, rounded rectangle, slot, circle, ellipse,
    polygon, star, arc, text, and dimension creation.
  - Select, box-select, vertex editing, insertion, deletion, duplicate, array, transform,
    align, distribute, group, explode, merge, split, trim, extend, smooth, simplify,
    resample, offset, and boolean operations.
  - Parametric shape recognition and metadata-preserving edits for rectangles, circles,
    ellipses, arcs, rounded rectangles, and slots.
  - Local-axis resize and rotation gizmos, larger invisible hit targets, live property
    updates, and property-to-canvas highlighting.
  - Construction geometry, guides, geometric constraints, geometry-health diagnostics,
    curvature display, and exact relationship inference.
- Expanded Pattern into a zone-first workflow with:
  - Per-zone pattern assignment, selection highlighting, role controls, cutouts, deleted
    cells, outlines, and persistent preview edits.
  - More than 20 pattern types, custom tile assets, motif management, reusable defaults,
    built-in/custom presets, preset import/export, rename, duplicate, and reset.
  - Automatic preview, explicit generation, cancellable workers, stale-result protection,
    output diagnosis, and role-aware layered exports.
  - Raster engraving controls and combined vector/raster LaserStar package output.
- Expanded Trace with:
  - Image-to-outline conversion, edge detection, threshold/Canny controls, blur,
    simplification, area filtering, closing radius, output sizing, and resolution limits.
  - Live preview, cancellation, stale-result protection, view preservation across retraces,
    workspace state, reusable defaults, and DXF export.
- Expanded Convert with:
  - FVI-to-DXF conversion, DXF repair, DXF-to-SVG, SVG-to-DXF, and batch processing.
  - Shared input handling, readiness states, replacement confirmation, output previews,
    cancellation, and non-destructive repaired copies.
- Added Repository Sync for pull, fetch, commit, push, repository selection, status
  reporting, and remembered repository locations.
- Added independent multi-window workspaces with shared application settings and separate
  page state, undo history, and recovery behavior.

### Canvas, drafting, and editing

- Replaced the monolithic canvas implementation with explicit command, renderer, model,
  runtime, view, tool, operation, layer, and widget boundaries.
- Added a declarative command registry shared by menus, keybindings, the command palette,
  context menus, and canvas interaction.
- Added persistent command lifecycle guidance showing the active operation, expected next
  input, completion keys, and cancellation behavior.
- Consolidated grid, object snap, construction mode, constraints, units, selection,
  geometry health, and status into clearer precision and status surfaces.
- Added endpoint, midpoint, center, edge, curve, tangent, extension, intersection,
  equal-length, axis-alignment, grid, angle, parallel, and perpendicular snapping.
- Added arithmetic and mixed `mm`/`in` expressions to supported numeric entry fields.
- Added exact previews for Trim and Extend and improved previews for drawing, transforms,
  snapping, dimensions, patterns, tracing, and conversions.
- Added spatial hit testing/indexing, improved selection filtering, and layer-aware
  editing/rendering.
- Added configurable radial menus, Draw sidebar sections/tools, context-menu sections,
  keybindings, control density, interface scale, motion reduction, contrast, and
  notification behavior.

### File formats and manufacturing workflows

- Unified vector import for DXF, SVG, and FVI through one Draft workflow, including
  replacement, add-to-document, recent files, and drag/drop.
- Expanded DXF support for layers, roles, blocks, line/arc/polyline entities, bulges,
  validation reports, geometry repair, and higher-fidelity curve export.
- Expanded SVG import/export for transforms, nested groups, view boxes, curves, path
  conversion, sizing, and DXF round trips.
- Added geometry-only StarFX FVI import/export for `MOVEDIST`, `DRAWLINE`, and `DRAWARC`.
- Added configurable FVI origin, margin, coordinate precision, Y orientation, travel
  optimization, path reversal, native arc preservation, comments, and unsupported-command
  reporting.
- Added LaserStar vector/raster package generation and dialogs for machine-oriented export
  settings.
- Added geometry preflight for open paths, duplicate or invalid geometry, tiny and
  zero-length segments, self-intersections, and minimum-clearance concerns.
- Improved curve tessellation and export fidelity so current parametric/spline geometry is
  exported rather than stale low-resolution points.

### Workspaces, settings, and reliability

- Added schema-validated workspace documents for page state, entities, selection, layers,
  groups, constraints, guides, dimensions, and feature configuration.
- Added command-oriented document mutation, reversible history, stable entity identity,
  snapshot management, and operation-result reporting.
- Added workspace Save, Save As, load, recent files, reusable workspace library, and
  platform-specific default locations.
- Added automatic recovery snapshots, recovery selection, safer retention, timestamps,
  and protection against hidden/background windows opening blocking recovery dialogs.
- Made workspace loading transactional so a failed page application does not leave
  partially applied state.
- Prevented Save As from adopting a path before its write succeeds.
- Added persistence file-size limits and clearer errors for malformed or oversized data.
- Added schema-driven application settings for paths, appearance, units, canvas behavior,
  snapping, smoothing, accessibility, updates, customization, and Trace defaults.
- Ensured Settings Cancel discards nested customization changes instead of leaking them
  into live application state.

### User interface and accessibility

- Added reusable layout, feedback, focus, input, icon, recent-file, token, and workflow
  components.
- Added command palette, keybindings, Settings, import preview, FVI export, LaserStar
  export, multi-paste, text, update, customization, shape recognition, preset manager, and
  workspace library dialogs.
- Added consistent status roles, progress feedback, success/error reporting, empty states,
  accessible names, visible focus handling, keyboard navigation, and larger pointer targets.
- Added responsive sidebars/drawers and preserved primary canvas/preview space at compact
  window sizes.
- Reorganized Help around user tasks and expanded it into a searchable manual covering
  Draft, Pattern, Trace, Convert, files, commands, shortcuts, troubleshooting, Settings,
  safety, and updates.
- Reorganized Settings so all options are visible by default, section navigation is
  optional, search covers every setting, and long rows wrap instead of clipping.
- Standardized ordinary displayed dimensions and scale values to two decimal places while
  retaining required machine precision such as `0.025 mm`.

### Architecture, testing, and updates

- Seamless, verified Windows self-updates:
  - Downloads now use a private staging directory instead of leaving replacement EXEs in
    Downloads.
  - The update dialog shows real download progress.
  - GitHub's published SHA-256 asset digest is verified before installation.
  - A detached updater waits for Simple Stipple to close, preserves a temporary rollback
    copy, replaces the installed EXE, and relaunches the app automatically.
  - Failed replacements restore the previous executable and write an installation log.
  - Development/source runs are prevented from accidentally replacing the Python
    interpreter.
- Executable architecture contracts covering dependency direction, Qt isolation,
  feature-to-feature boundaries, canonical module homes, and removed compatibility paths.
- A retrieval-oriented project map at `.opencode/knowledge/PROJECT_MAP.md` with entry
  points, module ownership, core flows, state, integrations, tests, risks, and onboarding
  guidance.
- Focused regression coverage for Help search, Settings navigation, sidebar scrollbar
  clearance, two-decimal dimension displays, update-dialog state transitions, staging-path
  safety, and Windows updater handoff behavior.
- Added repository-wide Pyright/Pylance and MyPy coverage, resolved the diagnostics found
  across Qt, document, DXF, SVG, pattern, trace, and workspace boundaries, and documented
  the cooperative canvas composition boundary.
- Added Ruff import/style enforcement and a McCabe complexity ceiling of 15 for new or
  changed code, with explicit exceptions for known legacy hotspots.
- Added circular-import validation, package/module-home checks, feature-boundary checks,
  wheel resource inventory, isolated-package import checks, and offscreen startup/UI smoke
  coverage.
- Added Numba-backed geometry acceleration, pyclipper-backed path operations, optional
  Sentry telemetry, and development dependencies for Pyright, Hypothesis, pytest-qt, and
  benchmarking.

### Structural and usability changes

- Reorganized the runtime package into a capability-first modular monolith:
  - `app/` owns startup, the main window, navigation, menus, tasks, and global Qt
    coordination.
  - `document/` owns editable state, commands, undo history, mutation services, and
    workspace persistence.
  - `engine/` owns UI-independent CAD, editing, geometry, imaging, pattern, and file-format
    capabilities.
  - `features/` owns the Draft, Pattern, Trace, Convert, Repository, and Help workflows.
  - `canvas/` owns the reusable interactive vector editor.
  - `ui/` owns shared controls, dialogs, notifications, units, and styling.
  - `platform/` owns settings, paths, storage, updates, reporting, and process bootstrap.
- Removed the old `backend`, `core`, `ui.pages`, `ui.canvas`, `app.services`, and
  `app.controllers` package trees and updated all imports to canonical homes.
- Reduced runtime Python modules from 193 to 176 by removing obsolete facades, empty
  namespace shells, and a dead model-service wrapper without combining unrelated behavior
  into larger files.
- Updated `ARCHITECTURE.md` with the new package map, dependency rules, placement table,
  public surfaces, and onboarding route.
- Help navigation is now organized around user tasks: Start, Create, Organize, Navigate,
  Edit, Files, and Solve.
- Help's developer-oriented setup text was replaced with practical workflow, workspace,
  fabrication-safety, and update guidance.
- Settings now shows every settings card by default and provides clearer section
  navigation as an optional shortcut.
- Settings search now searches every setting rather than being constrained by an implicit
  default category.
- Long Settings form rows now wrap at narrow dialog widths.
- Ordinary dimensions and scale values now display at most two decimal places. Three-place
  precision remains where the underlying machine value requires it, such as a `0.025 mm`
  engraving interval.
- macOS update downloads remain SHA-256 verified and now open the downloaded disk image
  directly for the standard application-bundle replacement flow.

### Fixed

- Help search comparing queries against raw HTML instead of normalized visible section
  text.
- Help search mixing topic filtering and in-page navigation without indicating the number
  of matches or a zero-result state.
- Nearly black Help table-of-contents text rendering on the dark navigation panel.
- Clipped Help search placeholder text and cramped table-of-contents width.
- Settings opening with only the General card visible, making most settings appear absent.
- Settings categories duplicating or obscuring the same underlying cards.
- Draw sidebar labels and controls rendering beneath or against the vertical scrollbar at
  narrow widths.
- Canvas size, selection HUD, and Pattern scale fields exposing inconsistent three-place
  display precision.
- Update-check completion clearing the entire update dialog, including its title and Close
  action.
- Update completion requiring users to manually locate the new EXE, close the application,
  delete the old EXE, and launch the replacement.
- In-flight update-thread signal cleanup missing download-progress disconnection.
- Stale internal documentation and configuration paths left behind after package moves.

### Validation

- 40 focused tests pass.
- Ruff passes across source, tests, and scripts.
- All runtime modules compile and import successfully.
- Circular-import validation passes across 176 modules.
- Wheel packaging succeeds with the required DXF tiles, QSS, and SVG icon resources.
- Help and Settings layouts were rendered offscreen and visually inspected.
- The intentionally deleted legacy test suite was not restored.

### Upgrade notes

- Internal Python import paths changed substantially. External scripts importing private
  `simple_stipple.backend`, `simple_stipple.core`, `simple_stipple.ui.pages`, or
  `simple_stipple.ui.canvas` modules must migrate to the documented canonical packages.
- The public desktop entry point remains `simple_stipple.app.launcher:main`; normal users
  do not need to migrate workspace or settings files.
- Seamless replacement currently applies to frozen Windows EXE builds. macOS continues to
  use the verified DMG installation flow.

---

## 0.3.4 — 2026-07-16

### Fixed

- Cleared all Pyright/Pylance diagnostics in addition to Ruff and MyPy.
- Configured VS Code and Pyright to use the project virtual environment, preventing false missing-import diagnostics.
- Tightened FVI center and Shapely coordinate typing found by Pyright.
- Documented the canvas cooperative-mixin boundary for Pyright while retaining full MyPy coverage of those modules.

### Changed

- Added Pyright to development dependencies and the CI quality gate.
- CI now runs Pyright, MyPy, and Ruff over all 94 source files instead of two selected modules.
- Compatibility ignores no longer fail inconsistently when third-party type coverage differs across supported Python versions.
- Configured `warn_unused_ignores` to `false` in MyPy to avoid false failures when third-party type stubs vary across Python/PySide/ezdxf version combinations.
- Added McCabe complexity ceiling of 15 for new code; pre-existing complex files scoped as exceptions.

---

## 0.3.3 — 2026-07-16

### Fixed

- Resolved all repository-wide MyPy diagnostics across backend, DXF, pattern, trace, workspace, and Qt UI boundaries.
- Corrected concrete typing defects involving DXF units, SVG transforms, image conversion, pattern polygonization, workspace models, and nullable Qt widgets.
- Added explicit typing boundaries for the canvas's cooperative Qt mixin architecture.

### Changed

- CI quality workflow now type-checks all 94 source files instead of two selected modules.
- Mypy compatibility ignores no longer fail inconsistently when third-party type coverage differs across supported Python versions.

---

## 0.3.2 — 2026-07-16

### Added

- Zone-first pattern editing with clearer assignment, selection highlighting, role controls, and per-zone output ownership.
- Auto-preview controls, cancellable pattern generation, persistent preview edits, and reusable pattern defaults.
- Recovery management for unsaved work, including safer snapshot retention and clearer recovery timestamps.
- File-size limits for JSON persistence and regression coverage for oversized workspace data.
- Bezier pen tool for freehand curve creation with real-time smoothing.
- Dimension tool for adding annotated measurements on the canvas.
- Quick radial menu for fast tool and action access.
- Multi-window support for working with multiple documents simultaneously.
- Curve fit tool for approximating polylines from point clouds.
- FVI workflow integration for StarFX format round-trip.
- Trace page for image-to-outline conversion with live preview.
- Convert page with batch processing for DXF/SVG/FVI repair and conversion.
- Help page with searchable table of contents and full user manual.
- Workspace recovery for unsaved work after crashes or unexpected exits.
- Draft page as an interaction-first 2D drafting environment with CAD shape recognition.
- Shape recognition dialog that infers parametric geometry (rectangles, circles, arcs) from hand-drawn polylines.
- DXF import preview dialog with layer inspection before committing to document.
- FVI export dialog with StarFX-specific field configuration.
- LaserStar export dialog for package creation.
- Command palette for quick action access.
- Keybindings dialog for viewing and customizing keyboard shortcuts.
- Settings dialog with schema-driven configuration.
- Update dialog for checking and applying application updates.
- Workspace library dialog for managing reusable workspace presets.
- Text dialog for multiline and path-based text input.
- Multi-paste dialog for handling multiple clipboard items.
- Import preview dialog for DXF files with layer inspection.
- Custom tile asset management for pattern tiling.
- Preset manager for saving and loading pattern configurations.

### Fixed

- Pattern selections, cutouts, deleted cells, and newly drawn outlines failing to survive preview regeneration.
- Voronoi preview generation crashing when non-finite geometry or gap values reached Shapely.
- The quality workflow type-checking a task-state module that had moved to the Pattern worker boundary.
- Hidden application windows opening a blocking recovery dialog during background startup processing.
- Workspace loads leaving partially applied state when a page failed, and Save As adopting a path before the write succeeded.
- DXF/FVI/SVG import and export edge cases affecting curve fidelity, layer roles, and destination handling.
- Canvas selection, panning, HUD text, keybindings, properties, and workspace round-trip inconsistencies.
- Rotation and resize gizmos breaking bounding boxes or failing on parametric shapes.
- Slot gizmo rotation and live Properties angle updates.
- Vertex insertion failures when double-clicking editable path edges.
- Properties failing to recognize shapes created with Polyline.
- Horizontal overflow in the Draw sidebar.
- Repeated macOS `GB18030 Bitmap` font fallback warnings during canvas inference painting.
- Auto-preview controls, cancellable pattern generation, persistent preview edits, and reusable pattern defaults.
- Recovery management for unsaved work, including safer snapshot retention and clearer recovery timestamps.
- File-size limits for JSON persistence and regression coverage for oversized workspace data.

### Changed

- Conversion workflows now confirm replacements and create non-destructive repaired copies by default.
- Refined Pattern, Convert, Trace, Draft, precision, status, settings, and layer-tree layouts and feedback.
- Updated application identity, accessibility labels, help content, and workspace save-state messaging.
- Consolidated small settings, notification, units, page runtime, and operation modules without adding source-directory sprawl.

---

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

---

## Architectural Rewrite (post-0.3.1)

### Overview

A complete architectural migration from a flat `src/` structure to a layered `src/simple_stipple/` package with clear separation of concerns. The rewrite reorganized 179 files, adding 14,559 lines and removing 11,914 lines in the core refactor, followed by a 105-file QOL overhaul (6,662 additions, 1,912 deletions).

### New Package Structure

The application was reorganized into the following capability packages:

- **`canvas/`** — Complete canvas subsystem with commands, operations, tools, renderer, layers, widgets, and view
- **`features/`** — Feature pages (pattern, draft, trace, convert, help) as self-contained modules
- **`engine/`** — Processing engine with CAD, editing, formats, patterns, geometry, and imaging submodules
- **`document/`** — Document model, history, identity, commands, and workspace management
- **`app/`** — Application launcher, main window, menu system, page registry, settings, tasks, and workspace controller
- **`ui/`** — UI components, dialogs, style/theme, file utilities, notifications, and widgets
- **`platform/`** — Platform abstraction for config, paths, settings, storage, updates, and error reporting

### Canvas System (39 new files)

The canvas was completely rebuilt from scratch with a modular architecture:

- **Commands** (`commands.py`, 844 lines) — Declarative command registry with id, label, shortcut, enablement logic, and category. Single source of truth for canvas actions shared by keymap, menus, context menu, and keyboard-shortcuts dialog.
- **Renderer** (`renderer.py`, 2,265 lines) — High-performance canvas rendering with layer compositing, grid drawing, snap visualization, HUD text, and gizmo rendering.
- **Operations** — 11 operation modules covering:
  - Clipboard (copy/paste, multi-paste, 11KB)
  - Draw operations (polylines, shapes, paths, 30KB)
  - Editing (boolean ops, trim/extend, split, merge/explode, 77KB)
  - Gizmo (local-axis manipulators, rotation/resize handles, 18KB)
  - Grouping (create/ungroup, nested groups, 3.6KB)
  - Hit testing (spatial selection, proximity queries, 11KB)
  - HUD text (floating labels, temporary annotations, 39KB)
  - Layer service (layer management, visibility, 7.5KB)
  - Smoothing (path simplification, curve smoothing, 5KB)
  - Snap service (grid snap, endpoint snap, orthogonal snap, 17KB)
- **Tools** — Three tool implementations:
  - Select tool (33KB) — Selection, multi-select, box selection, selection filtering
  - Draw tool (111KB) — Polyline drawing, shape creation, bezier pen, vertex editing
  - Dimension tool (20KB) — Linear, angular, radius dimension annotation
- **Layers** — Layer tree widget and logic with visibility toggling, selection highlighting, and layer-based rendering
- **View** — Canvas view with cooperative mixin architecture, interaction handling, grid configuration, and view commands
- **Widgets** — Toolbar, status strip, precision bar, draw sidebar, properties panel

### Engine (50 new files)

The processing engine was reorganized into six submodules:

- **CAD** (12 files, 160KB) — Parametric CAD primitives and operations:
  - Procedural primitives (rectangles, circles, ellipses, polygons, arcs, slots)
  - Shape recognition (infers parametric geometry from hand-drawn polylines)
  - Constraints (parallel, perpendicular, tangent, concentric)
  - Construction geometry (reference lines, center marks, axes)
  - Coordinates (coordinate system transforms)
  - Editor geometry (editing-specific geometry calculations)
  - Path operations (split, join, offset, resample)
  - Preflight (geometry validation, self-intersection detection)
  - Snapping (endpoint, midpoint, center, tangent, intersection snap)
- **Editing** (9 files, 96KB) — Path editing operations:
  - Boolean operations (union, difference, intersection, XOR via Clipper)
  - Clipper engine (wrapper around libclipper for boolean ops)
  - Merge/explode (combine or split connected paths)
  - Offset (parallel path offsetting)
  - Resample (uniform point sampling on curves)
  - Smoothing (Chaikin, Catmull-Rom, moving average)
  - Split (split paths at intersections or arbitrary points)
  - Transform (translate, rotate, scale, mirror, skew)
  - Trim/extend (exact trim and extend with hover preview)
- **Formats** (8 files, 248KB) — File format support:
  - DXF (full read/write with layer roles, entity types, blocks, 32KB)
  - DXF backend (low-level DXF entity serialization, 8KB)
  - DXF fix (DXF repair, self-intersection fixing, 11KB)
  - DXF schema (entity type definitions, 876B)
  - FVI (StarFX FVI format read/write, 20KB)
  - LaserStar (LaserStar package format, 5.5KB)
  - SVG (SVG import/export with path conversion, 24KB)
  - Service (unified format loading/saving API)
- **Patterns** (7 files, 304KB) — Pattern generation engine:
  - Shared utilities (common pattern logic, 33KB)
  - Fill patterns (hatch, crosshatch, gradient, 12KB)
  - Organic patterns (voronoi, noise-based, 9.9KB)
  - Output (pattern output to DXF/SVG/FVI, 6KB)
  - Presets (built-in and custom preset management, 10KB)
  - Processing (pattern generation pipeline, cancellation, 50KB)
  - Tiling (custom tile assets, motif management, 7.7KB)
- **Geometry** (3 files) — Geometry utilities:
  - JIT (just-in-time geometry calculations)
  - Service (unified geometry operations API)
  - Spatial (spatial indexing, Voronoi diagrams)
  - Voronoi (Voronoi diagram generation)
- **Imaging** (2 files, 64KB) — Image processing:
  - Raster (raster image handling, 7KB)
  - Trace (image-to-outline conversion, edge detection, 18KB)
- **Workflows** — High-level workflow orchestration for pattern generation, trace, convert, and export

### Feature Pages (30 new files)

Each feature page is a self-contained module with its own form, session state, and UI:

- **Pattern** (12 files, 2.3KB) — Pattern generator with zone-based editing, preset management, custom tiles, and live preview
- **Draft** (4 files) — Interaction-first 2D drafting with CAD shape recognition, bezier pen, dimension tool, and FVI export
- **Trace** (4 files) — Image-to-outline conversion with live preview, parameter controls, and DXF export
- **Convert** (3 files) — Batch conversion with sub-tabs for fixer, FVI, SVG, and SVG-to-DXF
- **Help** (1 file) — Searchable user manual with table of contents, section filtering, and in-content search
- **Repository** (1 file) — Git integration for version control

### Application Layer (8 new files)

- **Launcher** — Application entry point, platform detection, and startup sequence
- **Menu** (719 lines) — Full menu system with Edit, View, Insert, Format, Tools, Window, Help menus, command palette integration, and keyboard shortcut management
- **Pages** — Page registry and navigation system
- **Window** (598 lines) — Main window with multi-window support, tab management, and workspace layout
- **Settings Controller** — Settings persistence and schema validation
- **Tasks** — Background task management with cancellation support
- **Workspace Controller** — Workspace save/load, recovery, and state management

### Document Model (8 new files)

- **Model** (25KB) — Document model with entity records, layers, blocks, and metadata
- **Commands** (16KB) — Document-level commands (undo/redo, save/load, export)
- **History** — Undo/redo history with snapshot management
- **Identity** — Entity identity and sync ID management
- **Service** — Document service with CRUD operations
- **Workspace** — Workspace model and serialization

### Platform Abstraction (8 new files)

- **Config** — Application configuration management
- **Error Reporting** — Crash reporting and error telemetry
- **Launcher** — Platform-specific launcher utilities
- **Paths** — Platform-aware path resolution
- **Settings** — Settings storage with schema validation
- **Storage** — Persistent storage abstraction
- **Updates** — Application update checking and installation

### UI Layer (40 new files)

- **Components** (10 files) — Reusable UI components:
  - Collapsible sections
  - Cycle buttons (dropdown-style option selectors)
  - Feedback utilities (error/ok/warning styling)
  - Focus management (escape blur filters)
  - Icons (SVG icon system)
  - Inputs (float, int, unit-aware text fields)
  - Layout utilities (splitter, surface frame, sidebar panel)
  - Recent files (recent file buttons)
  - Tokens (design tokens)
  - Workflow (workflow strip, status labels)
- **Dialogs** (12 files) — Dialog system:
  - Base dialog (shared dialog functionality)
  - Command palette (quick action search)
  - Customize dialogs (theme, appearance customization)
  - FVI dialog (StarFX FVI export)
  - Import dialog (DXF import preview)
  - Keybindings dialog (shortcut customization)
  - LaserStar export dialog
  - Multi-paste dialog
  - Settings dialog (schema-driven settings)
  - Text dialog (multiline and path-based text)
  - Update dialog (application updates)
  - Workspace library (workspace presets)
- **Style** — Dark theme with QSS stylesheet, icon assets
- **Files** — File picker utilities
- **Notifications** — Toast notifications and status messages
- **Recent** — Recent files management
- **Units** — Unit conversion utilities

### Dependencies

- Added `numba>=0.64` for JIT-optimized geometry calculations
- Added `pyclipper>=1.4.0` for boolean operations and path offsetting
- Added `pyright>=1.1.403` (dev) for type checking
- Added `sentry-sdk>=2.0` (telemetry optional) for error reporting
- Updated entry point from `src.infra.launcher:main` to `simple_stipple.app.launcher:main`
- Updated pytest pythonpath from `.` to `src`

### CI/CD

- Quality workflow now runs Pyright, MyPy, and Ruff over all 94 source files
- Release workflow updated for new package structure
- Added McCabe complexity analysis with ceiling of 15
- Added package data for tiles, QSS styles, and SVG icons

### Tests

- Removed 60+ legacy test files from the old architecture
- Added 7 new architecture-compliance tests:
  - `test_dependency_boundaries.py` — Enforces layer boundaries
  - `test_module_homes.py` — Validates module structure
  - `test_page_feature_packages.py` — Validates feature page packaging
  - `test_phase7_supplemental.py` — Comprehensive feature tests
  - `test_seamless_updates.py` — Update system tests
  - `test_ui_audit_remediations.py` — UI audit fix verification
  - `test_ui_shared_modules.py` — UI shared module tests
- Added test data files for FVI, DXF, PDF, and SVG formats
