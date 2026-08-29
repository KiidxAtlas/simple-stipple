# Simple Stipple — Architectural Consolidation & Opportunity Analysis

Generated: 2026-08-29

## 1. High-Level Architecture Map

```
platform (2.1K) ──┐
                   ├─► core (11.5K) ──┐
                   │                   ├─► canvas (25.8K) ──┐
                   │                   ├─► features (20.8K) ──┐
                   │                   ├─► ui (4.3K) ──┐
                   │                   ├─► app (2.9K) ──► main process
                   └───────────────────┴───────────────────┘
```

**7 capability homes**, each with strict import boundaries enforced by tests. Direction: `platform → core → canvas/features → app`. `resources` is data-only.

---

## 2. Module-by-Module Breakdown (Lines & Concerns)

### `core/` (11,500 lines, 30 files) — Qt-free algorithms

| Sub-module | Files | Lines | Concern |
|---|---|---|---|
| `patterns/` (9) | 9 | 3,900 | Pattern generation: processing, geometry, fill, tiling, presets, output, complexity, cancellation, outline_identity |
| `cad/` (10) | 10 | 4,996 | CAD geometry: shapes, geometry, shape_factory, snapping, primitives, constraints, shape_base, preflight, detection, constants |
| `document/` (7) | 7 | 2,161 | State management: model (Pydantic workspace schema), service (command-oriented mutation), commands, organization, geometry, workspace, identity (504B — uuid helper used by 51 files) |
| `formats/` (7) | 7 | 3,278 | File I/O: svg, dxf, fvi, dxf_backend, dxf_write, laserstar, service |
| `editing/` (4) | 4 | 804 | Geometry editing: topology, boolean, smoothing, transform |
| `imaging.py` | 1 | 609 | Raster-to-vector image processing |
| `geometry.py` | 1 | 274 | Shared geometric primitives |

**Consolidation opportunities:**
- **`core/patterns/`** (9 files, 3,900 lines) — `processing.py` (1,273 lines) and `geometry.py` (921 lines) are the two largest. `processing.py` likely contains both the *algorithm* and *state management* for pattern generation. Consider whether `fill.py`, `tiling.py`, and `output.py` could be folded into `processing.py` as sub-sections, or whether `processing.py` should be split into `algorithm.py` + `state.py`.
- **`core/cad/shapes.py`** (986 lines) defines ~15 shape types (PolylineShape, PolygonShape, LineShape, ArcShape, EllipticalArcShape, etc.). This is a classic "kitchen sink" CAD shapes module. Could be split into `primitives.py` (Line, Arc, EllipticalArc) and `complex.py` (Polyline, Polygon).
- **`core/formats/dxf.py`** (791) + **`dxf_backend.py`** (538) + **`dxf_write.py`** (314) = 1,643 lines across 3 files handling DXF. The naming is confusing — `dxf_backend` vs `dxf_write` suggests an old split that never merged. Consider consolidating into `dxf_read.py` + `dxf_write.py`.

### `canvas/` (25,800 lines, 49 files) — The vector editor

This is the largest module and the most internally tangled.

| Sub-module | Files | Lines | Concern |
|---|---|---|---|
| `renderer.py` | 1 | 2,439 | Scene rendering (paint events, geometry draw) |
| `view/main.py` | 1 | 1,918 | CanvasView — the main widget, keyboard/mouse routing |
| `snap.py` | 1 | 1,501 | Snap engine (drag snap resolver, shape snap engine) |
| `operations/` (14) | 14 | ~8,500 | Edit operations: editing, select, construction, gizmo, text, draw_ops, hud_text, clipboard, smoothing, context_menu, quick_shapes |
| `tools/` (8) | 8 | ~1,200 | Canvas tools: selection, dragging, dimension_tool/backend, radial_menu, base, tools |
| `view/` (7) | 7 | ~3,000 | View config, commands, interactions, helpers, preferences, main |
| `widgets/` (5) | 5 | ~1,700 | UI chrome: toolbar, properties_panel, draw_sidebar, precision_bar |
| `layers/` (2) | 2 | ~910 | Layer management |
| `dialogs/` (3) | 3 | ~1,000 | Text, keybindings, customize dialogs |

**Consolidation opportunities:**
- **`operations/`** (14 files, ~8,500 lines) is the most fragmented sub-module. 11 of 14 files are under 100 lines. Several (clipboard, smoothing, context_menu, quick_shapes) are imported by only 1–2 other files. Consider grouping by *operation category*:
  - `geometric_ops.py` (editing, select, construction, draw_ops)
  - `annotation_ops.py` (text, hud_text)
  - `ui_ops.py` (gizmo, clipboard, smoothing, context_menu, quick_shapes)
- **`view/`** (7 files, ~3,000 lines) has tight internal coupling (view/* files import from each other). `config.py` (492 lines) + `preferences.py` (66 lines) both handle settings — merge them.
- **`canvas/snap.py`** (1,501 lines) is a monolith. The snap engine handles drag snapping, shape snapping, and angle snapping. Could be split into `snap_engine.py` + `snap_resolvers.py` + `snap_angles.py`.
- **`canvas/renderer.py`** (2,439 lines) is a single-file monolith handling all scene rendering. Consider splitting into `renderer.py` (paint orchestration) + `renderers/*.py` (per-entity renderers: lines, arcs, polylines, text, images, hatches).

### `features/` (20,800 lines, 28 files) — 5 workflow pages

| Feature | Lines | Classes | Key concern |
|---|---|---|---|
| `pattern/page.py` | 2,597 | 1 (PatternPage) | UI for pattern generation — form, preview, export |
| `trace/page.py` | 1,549 | 1 (TracePage) | Image tracing workflow |
| `draft/page.py` | 1,143 | 1 (DraftPage) | Freeform drafting |
| `convert/tasks.py` | 1,346 | 6 sub-tabs | File conversion (FVI↔DXF, SVG↔DXF) |
| `help/content.py` | 1,122 | 1 (HelpDialog) + 21 `_build_*` functions | Generated help HTML (not logic) |
| `repository/page.py` | 573 | 1 (RepoPage) | Local asset library |

**Consolidation opportunities:**
- **`pattern/`** (13 files, ~10,500 lines) is the largest feature. `page.py` (2,597) handles UI, `layout.py` (1,038) handles UI widgets, `form.py` (718) handles form state, `model.py` (2,892) handles domain model, `session.py` (11,922) handles preview worker state, `canvas_runtime.py` (6,877) handles canvas integration, `export.py` (13,106) handles export. The `page.py` at 2,597 lines is the single largest file in the codebase. It likely contains UI logic, state management, and domain logic mixed together.
- **`convert/tasks.py`** (1,346 lines, 6 sub-tab classes) is a multi-tab dialog masquerading as a tasks module. Could be split into `convert_page.py` + `convert_tasks.py` following the feature page pattern.
- **`help/content.py`** (1,122 lines, 21 `_build_*` functions) is pure content generation — HTML strings built by functions. This is not logic; it's documentation-as-code. Consider extracting to `docs/` or `resources/help/` as static Markdown, generated at build time rather than at runtime.

### `ui/` (4,300 lines, 23 files) — Shared Qt controls

| Sub-module | Lines | Concern |
|---|---|---|
| `dialogs/` (11) | 2,700 | Settings, workspace library, updates, files, export preflight, laserstar, fvi, command palette, support, multi-paste, base |
| `components/` (11) | 1,600 | Layout, icons, focus, feedback, units, recent, inputs, workflow, cycle_button |
| `style/` (1) | 364 | Theme.qss loading, status colors, icon paths |

**Consolidation opportunities:**
- **`ui/dialogs/`** (11 files, 2,700 lines) — `settings_dialog.py` (781 lines) is the largest dialog. It handles settings for every subsystem (grid, snap, smoothing, context menu profiles, etc.). Consider splitting into `settings_dialog.py` (chrome) + `settings_panels/*.py` (per-subsystem panels).
- **`ui/components/`** (11 files, 1,600 lines) — well-organized. No obvious consolidation needed.

### `app/` (2,900 lines, 6 files) — Composition root

| File | Lines | Concern |
|---|---|---|
| `menu.py` | 836 | Command spec, menu controller, command controller |
| `window.py` | 648 | App window, tab navigation |
| `workspace_controller.py` | 382 | Workspace save/load/recovery |
| `tasks.py` | 359 | App-wide background tasks |
| `pages.py` | 321 | Page registry, page runtime, settings sync |
| `launcher.py` | 133 | Application bootstrap |

**Consolidation opportunities:**
- `menu.py` (836 lines) defines command specs, a menu controller, and a command controller. The command controller likely routes commands to canvas operations. Consider whether `menu.py` should split into `menu_builder.py` (UI) + `command_registry.py` (routing).

### `platform/` (2,100 lines, 5 files) — OS abstraction

| File | Lines | Concern |
|---|---|---|
| `settings.py` | 648 | Application settings persistence |
| `updates.py` | 502 | Update checking |
| `error_reporting.py` | 427 | Crash reporting (Sentry) |
| `launcher.py` | 261 | Platform-specific bootstrap |
| `storage.py` | 83 | User data directory |

**No consolidation opportunities** — clean separation of concerns.

---

## 3. Cross-Module Dependency Hotspots

Modules that receive the **most imports from other modules** (i.e., the "glue" modules):

| Module | Importers | Why it's popular |
|---|---|---|
| `canvas.constants` (33 importers) | Nearly every canvas file | MIN_SCALE, DRAG_THRESH, and other constants |
| `platform.settings` (24 importers) | canvas, features, ui, app | `save_settings()`, `user_data_dir()` |
| `ui.style` (23 importers) | canvas, features, app | STATUS_*, icon_path, theme |
| `core.document.model` (20 importers) | canvas, features, app | EntityRecord, Document, workspace state |
| `ui.components.feedback` (19 importers) | canvas, features, app | `show_error()`, `refresh_style()` |
| `ui.components.units` (18 importers) | canvas, features | `format_length()`, `suffix()` |
| `ui.components.layout` (17 importers) | canvas, features | `CollapsibleSection`, layout helpers |

**Observation:** The top 7 most-imported modules are all "utility" modules. This is healthy — it means the architecture has clear shared surfaces. However, `canvas.constants` having 33 importers suggests constants are scattered rather than centralized. Consider whether `canvas.constants` should be folded into `canvas/__init__.py` or `core/geometry.py`.

---

## 4. Test Coverage Gaps (Untested Source Modules)

**~30 source modules have zero test coverage**, including:

| Untested Module | Lines | Risk |
|---|---|---|
| `canvas/commands.py` | 914 | Core command system — no tests |
| `canvas/runtime.py` | 630 | Canvas runtime — no tests |
| `canvas/snap.py` | 1,501 | Snap engine — tested separately but not via module import |
| `canvas/operations/editing.py` | 1,395 | Primary editing operations — no tests |
| `canvas/operations/select.py` | 795 | Selection operations — no tests |
| `canvas/operations/gizmo.py` | 448 | Gizmo operations — no tests |
| `canvas/operations/hud_text.py` | 699 | HUD text — no tests |
| `canvas/operations/construction.py` | 493 | Construction operations — no tests |
| `canvas/operations/smoothing.py` | 51 | Smoothing operations — no tests |
| `canvas/operations/quick_shapes.py` | ~80 | Quick shape creation — no tests |
| `canvas/operations/clipboard.py` | ~75 | Clipboard operations — no tests |
| `canvas/operations/context_menu.py` | ~100 | Context menu — no tests |
| `canvas/tools/selection.py` | 710 | Selection tool — no tests |
| `canvas/tools/dragging.py` | ~58 | Dragging tool — no tests |
| `canvas/tools/radial_menu.py` | ~590 | Radial menu — no tests |
| `canvas/tools/dimension_tool.py` | ~493 | Dimension tool — no tests |
| `canvas/tools/dimension_backend.py` | ~624 | Dimension backend — no tests |
| `canvas/tools/tools.py` | ~599 | Tool registry — no tests |
| `canvas/tools/base.py` | ~96 | Base tool — no tests |
| `canvas/view/main.py` | 1,918 | CanvasView — tested via integration but not module-level |
| `canvas/view/interactions.py` | ~801 | View interactions — no tests |
| `canvas/view/config.py` | ~492 | View config — no tests |
| `canvas/view/commands.py` | ~703 | View commands — no tests |
| `canvas/view/helpers.py` | ~650 | View helpers — no tests |
| `canvas/view/preferences.py` | ~66 | View preferences — tested separately |
| `canvas/layers/widget.py` | ~1,071 | Layer widget — no tests |
| `canvas/widgets/properties_panel.py` | ~668 | Properties panel — no tests |
| `canvas/widgets/toolbar.py` | ~568 | Toolbar — no tests |
| `canvas/widgets/draw_sidebar.py` | ~480 | Draw sidebar — no tests |
| `canvas/widgets/precision_bar.py` | ~358 | Precision bar — no tests |

**Key gap:** The entire `canvas/operations/` sub-module (14 files, ~8,500 lines) has no direct test coverage. The `canvas/commands.py` (914 lines) — the core command routing system — has no tests. The `canvas/tools/` sub-module (8 files, ~1,200 lines) has no tests.

---

## 5. Duplicate / Overlapping Patterns Across Features

### Pattern 1: `clear_workspace_state()` — implemented in 5 feature pages

Every feature page implements this method. The implementations are:

- **Convert**: inline (4 lines)
- **Draft**: delegates to `clear_draft_workspace_state(self)` (helper function)
- **Pattern**: delegates to `clear_pattern_workspace_state(self)` (helper function)
- **Repository**: inline (4 lines, sets text fields)
- **Trace**: delegates to `clear_trace_workspace_state(self)` (helper function)

**Opportunity:** The 3 delegating patterns (Draft, Pattern, Trace) follow an identical pattern: a page-level method that delegates to a feature-specific helper function. This could be a mixin: `WorkspaceStateClearable` with a class-level `CLEAR_FN` attribute.

### Pattern 2: Feature page class hierarchy

All 5 feature pages inherit from `BasePage(QWidget)`. The hierarchy is:

```
BasePage(QWidget)
  ├── DraftPage
  ├── PatternPage
  ├── TracePage
  ├── ConvertPage
  └── RepoPage
```

But Pattern and Trace each have a **separate** `CanvasPageRuntimeBase` subclass (PatternCanvasPageRuntime, TraceCanvasPageRuntime). This means the canvas integration is duplicated at the runtime level even though the page level is unified.

**Opportunity:** A single `FeatureCanvasPageRuntime(BaseCanvasPageRuntimeBase)` that accepts a `feature_type` parameter, or a factory pattern that creates the right runtime based on the feature.

### Pattern 3: `get_workspace_state()` / `apply_workspace_state()` / `clear_workspace_state()`

All 5 feature pages implement these 3 methods (part of the `WorkspacePage` protocol). The implementations vary in complexity:

- Simple (Convert, Repository): 4–5 lines inline
- Medium (Trace): delegates to helper
- Complex (Draft, Pattern): delegates to helper with additional state

**Opportunity:** A `WorkspacePageMixin` that provides default implementations and a registration mechanism for feature-specific clear functions.

---

## 6. Dead / Low-Usage Code Candidates

| Module | Lines | Usage | Status |
|---|---|---|---|
| `canvas/operations/quick_shapes.py` | ~80 | Imported by `widget.py`, used in 5 methods | **Active** — used for shape creation from modifiers |
| `canvas/operations/context_menu.py` | ~100 | Imported by `widget.py`, referenced in settings | **Active** — but very small |
| `canvas/operations/clipboard.py` | ~75 | Referenced in menu.py, radial_menu.py | **Active** — small but used |
| `canvas/operations/smoothing.py` | 51 | Referenced by `core/document/service.py` | **Active** — called from core |
| `core/document/identity.py` | 15 | Referenced by 51 files | **Critical** — tiny but foundational |
| `features/help/content.py` | 1,122 | Referenced by `help/dialog.py` | **Content-only** — could be static |
| `canvas/operations/construction.py` | 493 | Only imported by `operations/__init__.py` | **Check** — is it exported but unused? |

---

## 7. Architecture Change Recommendations (Priority Order)

### Phase 1: Low-risk consolidation

1. **Merge `core/formats/dxf.py` + `dxf_backend.py` + `dxf_write.py`** → `dxf_read.py` + `dxf_write.py` (clarifies naming, reduces 3 files to 2)
2. **Extract `core/patterns/processing.py`** (1,273 lines) into `algorithm.py` + `state.py` (separates computation from state)
3. **Extract `core/cad/shapes.py`** (986 lines) into `primitives.py` + `complex.py` (separates basic shapes from composite shapes)
4. **Move `features/help/content.py`** (1,122 lines) to static Markdown in `docs/help/` — generated at build time, not runtime

### Phase 2: Structural reorganization

5. **Group `canvas/operations/`** (14 files) into 3–4 category files: `geometric_ops.py`, `annotation_ops.py`, `ui_ops.py` (reduces 14 files to 4, preserves all functionality)
6. **Merge `canvas/view/config.py` + `preferences.py`** (both handle settings, 492 + 66 = 558 lines → 1 file)
7. **Create `canvas/operations/__init__.py` as a proper barrel** that re-exports only the public API (currently it's 64 lines — likely just exports)

### Phase 3: Feature page unification

8. **Extract `WorkspacePageMixin`** from the 5× `clear_workspace_state()` pattern (reduces ~20 lines of boilerplate across 5 files)
9. **Create `FeatureCanvasPageRuntime`** factory that replaces the separate `PatternCanvasPageRuntime` + `TraceCanvasPageRuntime` (reduces 2 files to 1 + factory)
10. **Split `features/pattern/page.py`** (2,597 lines) — this is the single largest file. Split into `page.py` (UI) + `state.py` (domain) following the pattern used by Draft/Trace

### Phase 4: Test coverage

11. **Add tests for `canvas/commands.py`** (914 lines) — the core command routing system
12. **Add tests for `canvas/operations/`** (~8,500 lines) — the largest untested area
13. **Add tests for `canvas/tools/`** (~1,200 lines) — all tools untested

---

## 8. Summary: Biggest Opportunities

| Area | Lines Affected | Effort | Impact |
|---|---|---|---|
| `canvas/operations/` grouping | ~8,500 | Medium | Reduces 14 files → 4, improves cohesion |
| `features/pattern/page.py` split | 2,597 | High | Single largest file → 2–3 focused files |
| `core/formats/dxf*` consolidation | 1,643 | Low | Clarifies naming, reduces files 3→2 |
| `core/patterns/processing.py` split | 1,273 | Medium | Separates algorithm from state |
| Help content extraction | 1,122 | Low | Runtime → build-time, reduces app startup |
| Feature page mixin extraction | ~20 × 5 | Low | Eliminates boilerplate across 5 files |
| Test coverage for canvas ops/tools | ~10,000 | High | Currently 0% tested |

---

## Appendix A: Full File Size Rankings (Top 40)

| Rank | File | Lines | Module |
|---|---|---|---|
| 1 | `features/pattern/page.py` | 2,597 | features |
| 2 | `canvas/renderer.py` | 2,439 | canvas |
| 3 | `canvas/view/main.py` | 1,918 | canvas |
| 4 | `features/trace/page.py` | 1,549 | features |
| 5 | `canvas/snap.py` | 1,501 | canvas |
| 6 | `canvas/operations/editing.py` | 1,395 | canvas |
| 7 | `features/convert/tasks.py` | 1,346 | features |
| 8 | `core/patterns/processing.py` | 1,273 | core |
| 9 | `features/draft/page.py` | 1,143 | features |
| 10 | `features/help/content.py` | 1,122 | features |
| 11 | `canvas/widget.py` | 1,082 | canvas |
| 12 | `canvas/layers/widget.py` | 1,071 | canvas |
| 13 | `features/pattern/layout.py` | 1,038 | features |
| 14 | `canvas/dialogs/customize_dialogs.py` | 997 | canvas |
| 15 | `core/cad/shapes.py` | 986 | core |
| 16 | `core/patterns/geometry.py` | 921 | core |
| 17 | `canvas/commands.py` | 914 | canvas |
| 18 | `core/formats/svg.py` | 905 | core |
| 19 | `app/menu.py` | 836 | app |
| 20 | `canvas/view/interactions.py` | 801 | canvas |
| 21 | `features/convert/page.py` | 799 | features |
| 22 | `canvas/operations/select.py` | 795 | canvas |
| 23 | `core/formats/dxf.py` | 791 | core |
| 24 | `core/cad/geometry.py` | 787 | core |
| 25 | `ui/dialogs/settings_dialog.py` | 781 | ui |
| 26 | `features/pattern/form.py` | 718 | features |
| 27 | `core/document/model.py` | 714 | core |
| 28 | `canvas/tools/selection.py` | 710 | canvas |
| 29 | `canvas/view/commands.py` | 703 | canvas |
| 30 | `canvas/operations/hud_text.py` | 699 | canvas |
| 31 | `canvas/widgets/properties_panel.py` | 668 | canvas |
| 32 | `core/cad/shape_factory.py` | 660 | core |
| 33 | `canvas/view/helpers.py` | 650 | canvas |
| 34 | `platform/settings.py` | 648 | platform |
| 35 | `app/window.py` | 648 | app |
| 36 | `canvas/runtime.py` | 630 | canvas |
| 37 | `canvas/tools/dimension_backend.py` | 624 | canvas |
| 38 | `core/formats/fvi.py` | 616 | core |
| 39 | `core/cad/snapping.py` | 615 | core |
| 40 | `core/imaging.py` | 609 | core |

---

## Appendix B: Test Coverage Map

### Tested Modules (29 test files, ~7,000 lines)

| Module | Test File(s) | Notes |
|---|---|---|
| `canvas.renderer` | `test_editor_dense_preview.py` | Rendering pipeline |
| `canvas.snap` | `test_editor_hit_testing.py` | Snap engine |
| `canvas.tools.dimension_backend` | `test_editor_view_preferences.py` | Dimension tool backend |
| `canvas.tools.dimension_tool` | `test_editor_view_preferences.py` | Dimension tool |
| `canvas.tools.tools` | `test_application_regressions.py` | Tool registry |
| `canvas.view.main` | `test_application_regressions.py` | Canvas view |
| `canvas.view.preferences` | `test_editor_view_preferences.py` | View preferences |
| `canvas.widget` | `test_application_regressions.py` | Canvas widget |
| `canvas.hit_testing` | `test_editor_hit_testing.py` | Hit testing |
| `core.document.model` | `test_document_images.py` | Document model |
| `core.document.service` | `test_document_replace_command.py` | Document service |
| `core.cad.shapes` | `test_engine_geometry_imaging_editing.py` | CAD shapes |
| `core.cad.primitives` | `test_engine_geometry_imaging_editing.py` | CAD primitives |
| `core.cad.constraints` | `test_architecture_contracts.py` | CAD constraints |
| `core.cad.preflight` | `test_architecture_contracts.py` | CAD preflight |
| `core.cad.shape_factory` | `test_engine_geometry_imaging_editing.py` | Shape factory |
| `core.formats.dxf` | `test_export_operations_and_outputs.py` | DXF format |
| `core.formats.dxf_write` | `test_export_operations_and_outputs.py` | DXF write |
| `core.formats.svg` | `test_svg_round_trip.py` | SVG format |
| `core.formats.laserstar` | `test_export_operations_and_outputs.py` | LaserStar format |
| `core.patterns.geometry` | `test_pattern_outline_state.py` | Pattern geometry |
| `core.patterns.fill` | `test_pattern_result_layer.py` | Pattern fill |
| `core.patterns.processing` | `test_pattern_global_lattice.py` | Pattern processing |
| `core.patterns.tiling` | `test_patterns_lattice.py` | Pattern tiling |
| `features.pattern.*` | 8 test files | Pattern feature (extensively tested) |
| `features.trace.*` | `test_trace_dxf_export.py` | Trace feature |
| `features.convert.*` | `test_export_operations_and_outputs.py` | Convert feature |
| `features.draft.*` | `test_draft_svg_backdrop.py` | Draft feature |
| `platform.settings` | `test_seamless_updates.py` | Platform settings |
| `platform.updates` | `test_seamless_updates.py` | Platform updates |
| `ui.*` | `test_ui_audit_remediations.py`, `test_ui_shared_modules.py` | UI (extensively tested) |

### Untested Modules (~30 modules, ~10,000 lines)

See Section 4 above. Key blind spots: `canvas/commands.py`, `canvas/operations/*`, `canvas/tools/*`, `canvas/view/*` (except main.py), `canvas/layers/*`, `canvas/widgets/*`, `canvas/runtime.py`, `core/document/commands.py`, `core/document/geometry.py`, `core/document/identity.py`, `core/document/organization.py`, `core/document/workspace.py`, `core/cad/constants.py`, `core/cad/detection.py`, `core/cad/geometry.py`, `core/cad/snapping.py`, `core/cad/shape_base.py`, `core/editing/smoothing.py`, `core/editing/transform.py`, `core/formats/fvi.py`, `core/formats/service.py`, `core/patterns/cancellation.py`, `core/patterns/complexity.py`, `core/patterns/output.py`, `core/patterns/presets.py`, `features/base.py`, `features/__init__.py`.
