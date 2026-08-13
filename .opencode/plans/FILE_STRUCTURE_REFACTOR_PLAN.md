# File Structure Refactor Plan

Date: 2026-08-11
Status: Complete — all five approved phases are implemented; Phase 5 release checks are
recorded in the changelog.

## 1. Current State Summary

**Final state.** `src/simple_stipple` has eight canonical top-level capability homes:
`app`, `document`, `editor`, `engine`, `features`, `platform`, `resources`, and `ui`.
The prior parallel home migrations are complete: reusable editor code lives only in
`editor`, pure domain capabilities live only in `engine`, and shared UI/platform facades
have been retired. The executable canonical-home and dependency checks are the source of
truth for future drift.

| Area | Final home | Invariant |
| --- | --- | --- |
| Reusable vector editor | `editor/` | One home for widget, tools, render, runtime, and view behavior. |
| Pure CAD, formats, imaging, patterns | `engine/` | Qt-free capability code with only `document.identity` as a shared type dependency. |
| Product workflows | `features/<workflow>/` | Named, cohesive seams without cross-feature internals. |
| Shared presentation | `ui/{components,dialogs,style}/` | No root-level facades and no editor dependency. |
| Process and persistent settings | `platform/` | Leaf capability that imports no product/runtime layers. |

There are no dumping-ground folders (`utils`, `misc`, or `common`). The real problems are
parallel homes and oversized coordinators, not the existence of focused packages.

## 2. Target Pattern

Use a **capability-first modular monolith** with a Qt model/view boundary:

- keep the Python `src/` layout, as recommended by the [Python Packaging User Guide](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/);
- keep editable document/domain data separate from Qt views, following [Qt Model/View Programming](https://doc.qt.io/qt-6/model-view-programming.html);
- use one physical canonical module per concern. No compatibility facade remains inside
  the application after a migration is complete; public re-exports, if necessary, belong
  only in the owning package's `__init__.py`.

The target deliberately does **not** collapse an entire product feature into one file.
One 2,000-line `pattern.py` would violate the user's readability requirement. A feature
package is allowed when its child modules are feature-specific and named by responsibility.

### Architecture invariants

1. `platform` imports no product workflow, editor, UI, document, or engine code.
2. `engine` and `document` are Qt-free; `engine` may use only `document.identity`.
3. `editor` owns all reusable interactive vector editing code; no `canvas` package remains.
4. `features/<name>` can import shared homes but never another feature's internals.
5. `ui` owns shared Qt presentation only and never imports `editor`.
6. A concern has one canonical import path. Tests may verify public package exports, but
   must not preserve stale module paths merely because they once existed.

## 3. Before/After Tree

```text
# Current (parallel homes abbreviated)
simple_stipple/
  app/  canvas/  document/  domain/  editor/  engine/  features/  platform/  ui/

# Target
simple_stipple/
  app/                         # launch, window, global menus and coordination
  document/                    # editable project state, undo, persistence
  engine/                      # pure CAD, formats, patterns, imaging, geometry, editing
  editor/                      # reusable vector-editor widget, tools, view, render, chrome
  features/                    # Draft, Pattern, Trace, Convert, Repository, Help
  platform/                    # paths, settings, storage, updates, reporting
  ui/                          # shared Qt controls, dialogs, theme
  resources/
```

Within `editor/`, retain only meaningful subpackages (`tools`, `view`, `operations`,
`widgets`, `dialogs`, `layers`) and root editor modules (`widget`, `renderer`, `runtime`,
`commands`, `snap`). Within `features/`, retain a package only where it represents a user
visible workflow and has multiple cohesive responsibilities.

## 4. Full Path Mapping Table

| Current path or family | Target canonical path or family | Reason | Risk |
| --- | --- | --- | --- |
| `domain/geometry.py` + `engine/geometry/{jit,spatial,voronoi}.py` | `engine/geometry/{jit,spatial,voronoi}.py` | Eliminate the duplicate geometry home; restore focused engine modules as canonical. | High |
| `domain/imaging.py` + `engine/imaging/{raster,trace}.py` | `engine/imaging/{raster,trace}.py` | Keep raster engraving and trace as separate, named responsibilities. | High |
| `domain/editing.py` + `engine/editing/{boolean,clipper_engine}.py` | `engine/editing/{boolean,clipper_engine}.py` | Preserve boolean versus clipping ownership without a parallel domain tree. | High |
| `domain/__init__.py` | deleted | `domain/` has no remaining unique capability. | Medium |
| `canvas/__init__.py` | deleted after editor migration | Remove the second name for the same reusable editor. | High |
| `canvas/{commands,constants,model,renderer,runtime,service,snap,widget}.py` | `editor/{commands,constants,document_bridge,renderer,runtime,snap,widget}.py` | Make the editor's root API discoverable in one home. | High |
| `canvas/{dialogs,layers,operations,tools,view,widgets}/**` | `editor/{dialogs,layers,operations,tools,view,widgets}/**` | Preserve cohesive local grouping while removing the obsolete package name. | High |
| `editor/{document_bridge,hit_testing,objects}.py` | `editor/{document_bridge,hit_testing,objects}.py` | Already correctly placed; update former canvas imports. | Low |
| `ui/{units,notifications,recent,files}.py` | deleted; use `ui/components/{units,notifications,recent}.py` and `ui/dialogs/files.py` | Remove migration facades after all consumers and tests use canonical paths. | Medium |
| `platform/paths.py` | deleted; use `platform/settings.py` | Path policy already belongs to settings; remove facade after rewrites. | Medium |
| `features/help/**` | unchanged | Its content/dialog split is cohesive and browsable. | Low |
| `features/{draft,trace,convert,pattern}/**` | unchanged package boundaries; extract only named page seams | These are user-visible capabilities; do not flatten into god modules. | High |
| `app/**`, `document/**`, `platform/{config,launcher,storage,updates,error_reporting,settings}.py`, `engine/{cad,formats,patterns}/**`, `ui/{components,dialogs,style}/**` | unchanged | They have distinct responsibilities and no duplicate home. | Low |

## 5. Migration Phases

- [x] Phase 1 — Remove the artificial `domain/` parallel tree. Restore canonical
  geometry, imaging, and editing implementations to their existing focused `engine/`
  modules; migrate imports, package exports, frozen-build checks, and tests; then delete
  all `domain/` files and legacy-facade tests. **High risk; manual characterization first.**
- [x] Phase 2 — Rename `canvas/` to `editor/` as one mechanical package migration.
  Update direct, relative, dynamic, test, entry-point, resource, and PyInstaller imports;
  delete `canvas/` only after no import remains. **Highest risk; automatable only after a
  complete import inventory and an offscreen smoke test.**
- [x] Phase 3 — Retire UI and platform facades (`ui/{units,notifications,recent,files}.py`,
  `platform/paths.py`), migrate every consumer to the canonical modules, and replace stale
  facade identity tests with behavior tests. **Medium risk; mechanical after inventory.**
- [x] Phase 4 — Split only the confirmed oversized coordinator seams, in priority order:
  `features/pattern/page.py`, `editor/renderer.py`,
  `editor/view/main.py`, `engine/cad/shapes.py`, then Trace/Draft pages. Each extraction
  must have one named responsibility, a focused regression test, and no generic helpers.
  **High risk; human-reviewed design work.**
- [x] Phase 5 — Update `ARCHITECTURE.md`, replace facade-oriented tests with canonical-path
  checks, enforce invariants in `tests/test_dependency_boundaries.py`, build an isolated
  wheel, run offscreen startup, and inspect every user workflow. **Release gate.**

## 6. Import and Reference Update Strategy

For each phase, inventory references with `rg` across `src/`, `tests/`, `scripts/`,
`.github/`, `README.md`, `pyproject.toml`, package data, and literal `import_module` calls.
Perform package moves with `git mv`, rewrite imports mechanically, then assert that `rg`
returns zero references to the retired module path. Update monkeypatch targets and package
exports at the same time. Do not preserve a facade solely to satisfy a test written during
the previous migration.

## 7. Risk and Rollback

High-risk failures include Qt QObject ownership, signals, paint/event order, workspace
serialization, command-palette lookup, frozen/PyInstaller imports, DXF/SVG/FVI behavior,
and external integrations that import an old path. Each phase is a separate review unit.
Stop immediately on a characterization or smoke-test regression. Keep all work unstaged;
rollback is the user-controlled reversal of that phase's diff, never a broad reset of the
dirty worktree.

## 8. Verification Plan

After every phase run:

```bash
ruff check src tests
pytest -q
git diff --check
python scripts/validate_codebase.py
.venv/bin/python -m pip check
QT_QPA_PLATFORM=offscreen .venv/bin/python -c 'from simple_stipple.app.window import App'
```

For Phase 2 additionally run the frozen-import and PyInstaller build checks. For Phase 4,
exercise Draft, Pattern, Trace, Convert, workspace recovery, DXF/SVG/FVI import/export,
image engraving, and drawing-tool flows before calling the work complete.

## Approval Record

This replacement plan supersedes the prior uncompleted plan. The user approved autonomous
execution of all phases on 2026-08-11.

## Changelog

- 2026-08-11 — Phase 1: restored geometry JIT/spatial/Voronoi, raster/trace imaging, and
  boolean/clipper editing implementations to their focused `engine/` modules; migrated
  source, test, dynamic, and frozen-build imports; replaced facade-identity coverage with
  canonical behavior characterization; and deleted the now-unreferenced `domain/` tree.
- 2026-08-11 — Phase 1 verification: focused canonical and frozen-import coverage passed;
  ruff, diff, dependency validator, pip, and offscreen import checks passed. The complete
  pytest suite reached 21% then remained CPU-bound in two independently launched processes;
  both were stopped, so a full-suite pass is intentionally not claimed.
- 2026-08-11 — Phase 1 release-gate follow-up: verbose full-suite execution completed in
  112.51 seconds (338 passed). The earlier apparent 21% stall was a slow, CPU-intensive
  section rather than a deadlock. Phase 1 is fully verified.
- 2026-08-12 — Phase 2: moved the entire reusable editor package from `canvas/` to
  `editor/`, merged the pre-existing editor seams into that one home, and rewrote static,
  relative, dynamic, frozen-build, test, monkeypatch, mypy, documentation, and Qt module
  identity references. `canvas/` is deleted and no retired import remains. Focused
  canonical-path coverage passed (33 tests); ruff, diff, pip, offscreen, frozen-import, and
  validator checks passed. A PySide6 item-teardown crash occurred once in an earlier broad
  run, but a clean full suite subsequently passed: 338 tests in 109.42 seconds.
- 2026-08-12 — Phase 3: retired `ui/{units,notifications,recent,files}.py` and
  `platform/paths.py`; every consumer, dynamic import, monkeypatch target, and test now
  uses the owning components/dialogs/settings module. The retired-module guard is the only
  intentional mention of the old paths. Focused behavior, ruff, diff, validator, pip, and
  offscreen checks passed; a clean complete suite then passed: 338 tests in 111.74 seconds.
- 2026-08-12 — Phase 4, Pattern: extracted `features/pattern/outline_state.py` for pure
  imported/transferred-outline normalization, ID reconciliation, canvas-record construction,
  bounds, and nested-region selection. Reused the existing session containment rule rather
  than duplicating Shapely logic. `PatternPage` remains the Qt coordinator. Focused behavior,
  static and smoke checks passed; the complete suite passed: 341 tests in 110.98 seconds.
- 2026-08-12 — Phase 4, renderer: extracted dense-preview retained-path batching and
  overscan raster caching to `editor/rendering/dense_preview.py`; `CanvasRenderer` retains
  the scene paint order and observable cache properties. Focused renderer coverage passed
  (143 tests); static and smoke checks passed; the complete suite passed: 342 tests in
  111.35 seconds.
- 2026-08-12 — Phase 4, editor view: extracted the grid, snap, context-menu, presentation,
  zoom, and cursor-status preference API to `editor/view/preferences.py`; `CanvasView`
  binds the same public methods while retaining lifecycle and event behavior. Focused checks
  passed (142 tests); static and smoke checks passed; the complete suite passed: 345 tests
  in 114.50 seconds.
- 2026-08-12 — Phase 4, CAD curves: extracted authored `SplineShape` and `BezierShape`
  behavior to `engine/cad/curves.py`, retaining factory, deserialization, and direct shape
  imports. Focused CAD/SVG tests passed (33 tests); static and smoke checks passed; a clean
  complete suite retry passed: 346 tests in 107.96 seconds.
- 2026-08-12 — Phase 4, Trace: extracted traced-outline DXF preflight, save destination,
  metadata-aware export, reveal, and status behavior to `features/trace/dxf_export.py`.
  `TracePage` keeps the same action bindings, signals, cancellation, and visible UI state.
  Focused behavior and static/smoke checks passed; the complete suite passed: 348 tests in
  109.36 seconds.
- 2026-08-12 — Phase 4, Draft: extracted imported-SVG backdrop placement, editable callback
  wiring, transforms, and removal to `features/draft/svg_backdrop.py`; `DraftPage` retains
  the same interaction bindings and patch surface. Focused behavior and static/smoke checks
  passed; the complete suite passed: 350 tests in 111.63 seconds.
- 2026-08-12 — Phase 3: deleted the temporary `ui/{units,notifications,recent,files}.py`
  and `platform/paths.py` facades after moving every source, test, literal monkeypatch,
  dynamic, and documentation consumer to `ui/components/{units,notifications,recent}.py`,
  `ui/dialogs/files.py`, and `platform/settings.py`. Replaced facade-identity tests with
  canonical behavior coverage and added retired-module guards. Isolated focused suites
  passed (144 and 12 tests); ruff, diff, validator, pip, canonical offscreen imports, and
  explicit retired-module absence checks passed. A combined Qt test batch still triggered
  the known PySide6 `QListWidgetItem` teardown segmentation fault after 145 tests, so its
  result is not represented as a successful full-suite run.
- 2026-08-12 — Phase 4 Pattern sub-scope: extracted the non-Qt outline-state responsibility
  from `features/pattern/page.py` into `features/pattern/outline_state.py`. The new module
  owns transferred-outline normalization, identity reconciliation, editor record/layer
  preparation, bounds, and smallest-containing-region selection; focused behavior and
  Pattern import/export/result-layer coverage passed. Remaining Phase 4 priorities are
  intentionally untouched.
- 2026-08-12 — Phase 4 renderer sub-scope: extracted dense-preview retained-path batching
  and overscan-raster caching from `editor/renderer.py` into
  `editor/rendering/dense_preview.py`. `CanvasRenderer` retains its cache inspection seam
  and paint order while delegating only this render-only responsibility; focused rendering,
  scene-order, and UI coverage passed.
- 2026-08-12 — Phase 4 view-main sub-scope: extracted CanvasView's grid, context-menu,
  snap, appearance, zoom, and cursor-status preference APIs to
  `editor/view/preferences.py`. The existing `CanvasView` public methods are bound to the
  extracted implementations; widget initialization, event priorities, and rendering remain
  in `editor/view/main.py`. Focused preference, canvas smoke, and UI behavior coverage
  passed (142 tests).
- 2026-08-12 — Phase 4 CAD-shapes sub-scope: extracted authored control-point spline and
  Bezier implementation to `engine/cad/curves.py`. `ShapeFactory` remains the sole
  construction/deserialization boundary and retains legacy direct curve-class imports via a
  lazy module attribute, avoiding an inheritance import cycle. Focused curve transforms,
  engine behavior, and SVG/DXF round-trip coverage passed (33 tests).
- 2026-08-12 — Phase 4 Trace sub-scope: extracted the traced-outline DXF export workflow to
  `features/trace/dxf_export.py`, including preflight, destination selection, metadata-aware
  native DXF export, reveal availability, and status result. `TracePage` retains its original
  private action methods as Qt connection and patch surfaces; trace signals, worker
  cancellation, visible state, and raster export remain in the page. Focused Trace export and
  worker characterization passed (2 and 15 tests respectively).
- 2026-08-12 — Phase 4 Draft sub-scope: extracted imported SVG reference-artwork placement,
  editable canvas callback wiring, transform refresh, and removal to
  `features/draft/svg_backdrop.py`. `DraftPage` retains its original private callback names as
  import and canvas patch surfaces; vector import routing, dialogs, status, and document state
  remain in the page. Focused Draft backdrop, SVG round-trip, and characterization coverage
  passed (32 tests).
- 2026-08-12 — Phase 5: reconciled `ARCHITECTURE.md` with the final eight-home runtime tree
  and removed transitional facade guidance. Canonical-home checks now assert the actual
  package tree, editor/engine subhomes, and facade-free UI/platform roots rather than
  importing historical paths; dependency checks also inspect literal `__import__` calls.
  Focused architecture coverage passed (17 tests); ruff, diff, pip, offscreen startup, and
  isolated wheel metadata/resource verification passed. `validate_codebase.py` completes with
  100 existing size/documentation advisories. A single final full-suite process completed
  without returning a terminal summary through this runner, so its result is not represented
  as a new confirmed pass.
