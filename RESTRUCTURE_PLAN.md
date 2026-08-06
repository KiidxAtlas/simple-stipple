# Codebase Restructure Plan — Simple Stipple

> Written as `RESTRUCTURE_PLAN.md`, not `plan.md` — the repo already has a
> tracked root `plan.md` for an unrelated feature initiative ("Interaction
> Model Overhaul"). Overwriting it would destroy that document. Do not merge
> the two; they answer different questions (feature phasing vs. file layout).

## Stack & Purpose

- **Language/runtime:** Python ≥3.10, installed `src`-layout package
  (`src/simple_stipple`), managed with `pyproject.toml` + `uv.lock`.
- **Framework:** PySide6 (Qt) desktop application.
- **Domain libraries:** `ezdxf` (DXF), `shapely` + `pyclipper` (geometry
  boolean ops), `opencv-python-headless` + `Pillow` (imaging/tracing),
  `scipy`/`numba` (pattern generation math), `pydantic` (schemas).
- **Tooling:** `pytest` (+ `pytest-qt`, `hypothesis`, `pytest-benchmark`),
  `ruff`, `mypy`, `pyright`. Packaged with `pyinstaller` for Windows/macOS
  desktop distribution (`scripts/build_standalone.py`, `SimpleStipple.spec`).
- **Purpose:** desktop app for drafting, tracing, and generating pattern
  fills (stipple, honeycomb, voronoi, etc.) for laser-cutting / vector
  workflows, exporting to DXF/FVI/LaserStar formats.
- **Scale:** 187 Python files, ~68,900 lines under `src/simple_stipple`.

## Applicable Standards

- **SOLID — Single Responsibility** (Martin, *Agile Software Development*,
  2003): flags any file mixing unrelated reasons to change. Directly
  relevant to the oversized-file findings below.
- **Acyclic/Stable Dependencies Principle** (Martin): dependencies should
  flow one direction, toward stability. This project already enforces a
  one-directional graph via `tests/test_dependency_boundaries.py`.
- **Clean Architecture** (Martin, 2017): domain/business logic independent
  of frameworks and UI. Matches this project's existing rule that `engine`
  and `document` contain zero Qt imports.
- **Domain-Driven Design — bounded contexts** (Evans, 2003): `features/*`
  as workflow-scoped packages (Draft, Pattern, Trace, Convert) map cleanly
  onto DDD bounded contexts that don't reach into each other's internals.
- **Python Packaging User Guide — src-layout**: already followed.

## Current Structure

**This codebase already has a deliberate, documented, test-enforced
architecture.** It is not an undifferentiated pile of files needing a first
pass at organization. Evidence:

- A file `ARCHITECTURE.md` existed at the repo root describing a
  "capability-first modular monolith" with an explicit package map and a
  one-directional dependency diagram. It was **deleted in commit `963ae3a`**
  ("qol, preparing for 0.3.6") with no replacement. `README.md` still links
  to it (`README.md:6`) — a dangling reference.
- `tests/test_dependency_boundaries.py`, `tests/test_module_homes.py`, and
  `tests/test_page_feature_packages.py` encode the same rules as
  executable tests. **All pass on current `HEAD`** — there is no dependency
  drift to fix.

Reconstructed package map (from the deleted `ARCHITECTURE.md`, cross-checked
against the current tree — still accurate):

| Package | Responsibility |
|---|---|
| `app/` | process entry, main window, page registry, menus, tasks, settings coordination |
| `document/` | canonical editable state, commands, undo history, workspace persistence |
| `engine/` | UI-independent CAD, geometry, editing, pattern, imaging, format capabilities |
| `features/` | product workflows named as users see them: Draft, Pattern, Trace, Convert, Repository, Help |
| `canvas/` | the reusable interactive vector editor: runtime, rendering, tools, operations, view, widgets, layers |
| `ui/` | shared Qt components, dialogs, notifications, units, packaged styling |
| `platform/` | OS paths, settings, storage, updates, error reporting |
| `resources/` | packaged non-code runtime data |

Enforced dependency direction (unchanged, still correct):
`platform`, `engine` → `document` → `canvas`, `features` → `app`.

### Real pain points found

1. **Missing `ARCHITECTURE.md`** — documented above. Straightforward to
   restore; not a structural defect.
2. **Root `plan.md` self-contradiction** — `plan.md` line 3 says an older
   `plan.md` no longer exists but several *module docstrings still reference
   it* (e.g. "see plan.md Section 9.1"). Those docstrings are now pointing at
   the wrong document. Flagged for the file-by-file pass — not a layout
   change, a comment-accuracy issue.
3. **Oversized single files inside otherwise well-placed packages** — the
   package boundaries are right, but several files have outgrown single
   responsibility and are candidates to split *within their current
   package*, not move:

   | File | Lines |
   |---|---|
   | `features/pattern/page.py` | 2,825 |
   | `canvas/tools/tools.py` | 2,661 |
   | `canvas/view/main.py` | 2,571 |
   | `canvas/renderer.py` | 2,542 |
   | `canvas/operations/editing.py` | 1,969 |
   | `engine/cad/shapes.py` | 1,774 |
   | `features/trace/page.py` | 1,587 |
   | `features/help.py` | 1,474 (single-module feature, per `ARCHITECTURE.md` intentionally so — worth re-checking now it's this large) |
   | `engine/patterns/processing.py` | 1,395 |
   | `features/convert/tasks.py` | 1,355 |

   These are also exactly the files `ruff`'s per-file `C901` (complexity)
   ignore list in `pyproject.toml` already carries as scoped exceptions —
   independent confirmation these are the known problem spots, not a new
   finding.
4. No evidence yet of genuine **misplaced** files (wrong package) or
   **duplicated logic** across packages — Phase 2's file-by-file pass is
   what would surface that with confidence; nothing found in the pass done
   so far above the file-count/line-count level.

## Proposed Structure (for approval)

Given the above, this is **not** a proposal to re-lay-out the top-level
package structure — `app / document / engine / features / canvas / ui /
platform / resources` already satisfies the standards above and is
test-enforced. Re-drawing it would be churn for no benefit, and would fight
the tests rather than the architecture. Top-level tree stays exactly as-is:

```
src/simple_stipple/
├── app/            (unchanged)
├── document/        (unchanged)
├── engine/           (unchanged)
├── features/         (unchanged)
├── canvas/            (unchanged, except: gains dialogs/ — see below)
│    └── dialogs/         text_dialog.py, keybindings_dialog.py, customize_dialogs.py
│                         (relocated from ui/dialogs/ — fixes a real bidirectional
│                          ui↔canvas coupling; net file-count change: zero, moved not added)
├── ui/               (unchanged, except: loses the 3 files above)
├── platform/          (unchanged)
└── resources/          (unchanged)
```

**File-count discipline for everything below:** minimize total files. Do not
split a file solely because it is long — only propose a split/move where it
fixes a genuine dependency-boundary violation or duplication, never for
line-count alone. See "File-level splits — WITHDRAWN" below for what this
ruled out.

### A finding that shapes the file-level proposal

Before proposing file splits, I checked whether this codebase has tried
splitting its god-classes before. It has, twice, via multiple-inheritance
mixins — and reversed both:

- `canvas/operations/editing.py:193,278` — comments read *"Snap helpers
  (inlined from `_SnapMixin`)"* and *"Shape preview helpers (inlined from
  `_DrawModeMixin`)"*.
- `canvas/view/main.py:1802` — *"Inlined from removed mixins (methods
  actually called from view.py)"*.
- Commit `9a7d3a5` — **`fix: recover methods lost in mixin-inlining
  refactor`** — a mixin split silently dropped methods, shipped, and had to
  be found and restored later.

Conclusion: **do not propose re-splitting via mixins.** The one proven-safe
decomposition pattern already in this codebase is delegation to sibling
modules of plain functions/classes the widget calls into — exactly how
`features/pattern/zones.py`, `outlines.py`, and `treatments.py` already sit
next to `page.py` today. Anything below follows that pattern, never mixins.
Given the mixin incident, `canvas/operations/editing.py` and
`canvas/view/main.py` specifically are marked **high-risk, verify-first**
rather than proposed outright — Phase 2 should read them in full before
deciding, not split on the strength of a line count.

### File-level splits — WITHDRAWN

The splits originally proposed below (`tools.py` → 9 files, `shapes.py` → 5
files, `tasks.py` → 5 files, `help.py` → 2 files, `page.py` → ~6 files) are
**withdrawn per explicit direction: minimize total file count.** None of
these files have a demonstrated correctness or duplication problem — the
only evidence against them was line count, and Python does not punish a
long file the way it punishes duplicated logic. Splitting would net ~+25
files and import boilerplate in exchange for a size number, which is a bad
trade against a stated goal of fewer files. Left as-is: `tools.py`,
`shapes.py`, `tasks.py`, `help.py`, `page.py`, and every other oversized
file — none are touched by this plan unless Phase 2 finds a genuine
duplication or misplacement (not size) problem. Proposal kept below only as
a record of what was considered and rejected, so it isn't silently
re-proposed later.

<details>
<summary>Withdrawn split proposal (kept for record only)</summary>

### Concrete file-level splits (before → after) — DO NOT EXECUTE

**High confidence — mechanical, mirrors an existing pattern in the same package, no history of prior failed attempts:**

```
canvas/tools/tools.py (2,661 lines, 9 classes)          canvas/tools/
                                                    →      base.py               (CanvasTool)
                                                            scale.py              (ScaleTool)
                                                            dimension_interaction.py (DimensionTool — renamed
                                                                                   from the file-local class;
                                                                                   collides in name today with
                                                                                   canvas/tools/dimension_tool.py's
                                                                                   DimensionTool, silently
                                                                                   disambiguated at the one import
                                                                                   site via `as SketchDimensionTool`
                                                                                   — see Naming Improvements)
                                                            edit.py               (EditTool)
                                                            draw.py               (DrawTool)
                                                            trim_extend.py        (TrimExtendTool)
                                                            select_tool.py        (SelectTool — "select.py" is
                                                                                   already taken by SelectionService)
                                                            knife.py              (KnifeTool)
                                                            radial_menu.py        (RadialMenuService)

engine/cad/shapes.py (1,774 lines, 13 classes)   engine/cad/shapes/
                                                    →      base.py       (Shape ABC, _CenterBasedShapeMixin, _ParametricRotateMixin)
                                                            lines_arcs.py (LineShape, ArcShape)
                                                            polygons.py   (PolylineShape, PolygonShape, CircleShape,
                                                                           EllipseShape, RectangleShape,
                                                                           RoundedRectangleShape, StarShape, SlotShape)
                                                            curves.py     (SplineShape, BezierShape)
                                                            factory.py    (ShapeFactory)

features/convert/tasks.py (1,355 lines, 5 classes)  features/convert/tasks/
                                                    →      base.py       (_ConversionSubTab)
                                                            fvi.py        (FviSubTab)
                                                            fixer.py      (FixerSubTab)
                                                            svg.py        (SvgSubTab)
                                                            svg_to_dxf.py (SvgToDxfSubTab)

features/help.py (1,474 lines: 1,109 lines of      features/help/
  content-builder functions, then one dialog        →      content.py    (all 20 `_build_*` functions — pure,
  class at line 1150)                                                     stateless string builders, no Qt)
                                                            dialog.py     (HelpDialog — the only Qt/state-holding part)
```

**Medium confidence — the file itself already has clean, author-written
section headers (`# ── Output ──`, `# ── Zones ──`, etc.) marking exactly
where the seams are; still needs a full read in Phase 2 to confirm no
hidden cross-section state coupling before executing:**

```
features/pattern/page.py (2,825 lines, 1 class,   features/pattern/
  14 author-labeled sections)                       →      page.py           (PatternPage shell: __init__, Qt wiring,
                                                                                dispatch — mirrors how it already
                                                                                delegates to zones.py/outlines.py)
                                                            page_output.py    ("Output" + "Continuous validation" sections)
                                                            page_build.py     ("Build (UI construction)" section)
                                                            page_dxf_io.py    ("DXF loading, outlines, pattern library")
                                                            page_presets.py   ("Presets" section)
                                                            page_generation.py ("Generation" + "Document pattern grid")
                                                            (remaining sections mapped once Phase 2 confirms boundaries)
```

**High-risk, verify-first — do not split without a full read; each has a
documented history of a reversed mixin split in this exact file:**

```
canvas/view/main.py (2,571 lines, 385 methods)      → read fully in Phase 2 before proposing anything.
canvas/operations/editing.py (1,969 lines)          → read fully in Phase 2 before proposing anything.
```

**Not proposed for splitting — line count is high but method count/lines-per-method
is reasonable, and no author section markers suggest natural seams:**

```
canvas/renderer.py (2,542 lines, 50 methods, ~51 lines/method)
engine/patterns/processing.py (1,395 lines, 25 methods, ~56 lines/method)
```
Both stay on the watch list; Phase 2 may still surface a seam on a full read.

</details>

### Also proposed (unrelated to file splits)

1. **Restore `ARCHITECTURE.md`** at the repo root, reconstructed from git
   history (`git show 963ae3a~1:ARCHITECTURE.md`) and verified line-by-line
   against the current tree, fixing anything that's drifted since.
2. **File-by-file pass (Phase 2)** over the remaining ~175 files, mainly to
   confirm placement (cheap, catches anything the line-count scan missed)
   and to execute/refine the splits above once each target file is read in
   full.
3. **No file moves across top-level packages are anticipated** — flag it
   loudly in Phase 2/3 if the file-by-file pass finds one, since that would
   contradict the passing dependency-boundary tests today.

## File Mapping

_(populated live during Phase 2 — see progress tracker below)_

**Progress: 187/187 files analyzed — Phase 2 complete.**

### app/, document/, platform/ (21 files) — DONE, all OK

Every file's placement matches the "Where changes go" table; every import
checked against the dependency rules (rules 1, 3, 4, 7) with no violations.
One placement worth recording explicitly rather than flagging:
`platform/error_reporting.py` contains a Qt toast widget, which looks
Qt-heavy for `platform/` — but it must stay there. Moving it to `ui/` would
force `platform` to import `ui`, which the dependency rules forbid (`ui`
depends on nothing above it; `platform` must stay a leaf). No duplication
found; two lookalikes checked and ruled genuinely distinct:
`platform/storage.py`'s two atomic-write helpers (different call shapes,
not duplicates), and `ui/notifications.py`'s persistent log vs.
`platform/error_reporting.py`'s transient toast (history vs. live
surfacing — distinct mechanisms).

No file moves proposed from this scan.

### canvas/ (41 files) — DONE, one real finding

No `app`/`features` imports anywhere in `canvas/` — rule 5 holds cleanly.
Three suspected-duplicate pairs were checked in full and all ruled cleanly
separated (not duplication): `renderer.py` vs. `rendering/{overlays,scene}.py`
(the latter are 19-20 line ordering wrappers that delegate back into
renderer's private methods, not reimplementations); `snap.py` vs.
`operations/snap_service.py` (cursor/draw-time snapping vs. drag/resize
snapping — distinct consumers); `commands.py` vs. `view/commands.py`
(a declarative registry vs. extracted `CanvasView` instance methods —
different concepts, same word).

**One genuine misplacement found:** `canvas/tools/select.py`'s
`SelectionService` is a stateful service (owns selection/vertex/Bezier
state, constructed in `view/config.py` the same way every `operations/*`
service is) filed under `tools/`, which `tools/tools.py`'s own docstring
defines as strategy objects that are explicitly *stateless* ("all
interaction state stays on the view"). It doesn't match either half of its
own package. Proposed: rename/move to `canvas/operations/select.py` (file
count neutral — moved, not added). Low priority, low risk — a rename, not a
behavior change.

No other file moves proposed from this scan.

### features/ (32 files) — DONE, one dead file found

No cross-feature import violations: every `features.*` import outside
`features.base` stays inside its own feature package. `zones.py` vs.
`treatments.py` (flagged for checking, given the in-flight Phase 1
region/treatment migration described in the root `plan.md`) is **not**
duplication — `treatments.py` is the canonical Phase-1 domain model,
`zones.py` is the UI layer on top of it plus a deliberate read-only
projection (`snapshot_zone_jobs`) back into the legacy zone-dict shape
`engine/patterns` still consumes. Both files' own docstrings say so
explicitly, and the pre-Phase-1 `outlines.py` module `plan.md` describes no
longer exists — the migration is complete, not straddling two designs.

**One dead file found:** `features/pattern/sliders.py` (`PatternSlider`) is
not imported or referenced anywhere in `src/`. The slider+spinbox pattern
it implements is already done inline elsewhere (`form.py`'s
`NoWheelSlider`, `layout.py`'s engraving spinbox helper) — this reads as a
superseded widget nobody wired up, not a needed file. Proposed: **delete**
(reduces file count — aligned with the minimize-files directive).

**Noted, not flagged:** `pattern/layout.py` and `trace/page.py` each have a
near-identical ~6-line local `number()` closure for building a
`QDoubleSpinBox`. Below the "two independent consumers" promotion bar
`plan.md` itself sets, and the two call sites are genuinely different UIs
(modal dialog vs. persistent panel) — not proposed for consolidation now,
noted only in case a third consumer shows up later.

No file moves proposed from this scan; one deletion proposed.

### ui/ (32 files) — DONE, the 3-file fix was incomplete

Confirms the 3 known canvas-coupled files (`customize_dialogs.py`,
`keybindings_dialog.py`, `text_dialog.py`) and finds **4 more `ui/dialogs`
files reaching into layers `ui` is documented as never touching**:

| File | Imports | Kind |
|---|---|---|
| `dialogs/settings_dialog.py` | `features.trace.form` (`TRACE_DEFAULT_FIELDS`, `trace_default`) | ui → features (a specific feature's internals) |
| `dialogs/import_dialog.py` | `engine.formats.service` (report types + summarizer) | ui → engine |
| `dialogs/fvi_dialog.py` | `engine.formats.service`, calls `DxfService.render_fvi()` directly | ui → engine (functional, not just types) |
| `dialogs/export_preflight.py` | `engine.cad.preflight` (`GeometryPreflight`, `analyze_geometry`) | ui → engine |
| `dialogs/workspace_library.py` | `document.workspace` (path/suffix helpers) | ui → document |

`tests/test_dependency_boundaries.py` checks `platform`/`engine`/`document`/
`canvas`/`features` outgoing imports — **it never checks `ui` at all**, so
none of this is caught today.

These four don't all carry the same weight, and I'm not proposing to move
all of them — moving files to satisfy a documented rule that was already
inaccurate isn't a fix, it's chasing the doc:

- **The 3 engine/document ones are not structurally broken.** `engine` and
  `document` never import `ui` back, so there's no cycle — just `ui`
  depending on something above it in the diagram, for dialogs whose entire
  job requires those types (an import-preview dialog needs DXF report
  types; a preflight dialog needs `GeometryIssue`-shaped data; a workspace
  browser needs the workspace path helpers). **Recommendation: correct
  ARCHITECTURE.md's description of `ui` instead of moving these three** —
  the only structurally necessary rule is "`ui` never imports `canvas`"
  (because `canvas` imports `ui` back, and that's the one edge that would
  actually cycle). Filing this as documentation accuracy, not a defect.
- **`settings_dialog.py` → `features.trace.form` is a different kind of
  problem.** This is `ui` (the leaf every other layer depends on) reaching
  into one specific product feature's internals — the same backwards
  direction the canvas case had, minus the literal import cycle. It's also
  a fan-in risk: if Pattern, Draft, or Convert ever want their own
  "defaults" section in Settings, this dialog would need to import each
  feature by name. Two real options, a genuine judgment call for Gate 2:
  **(a)** accept it as a pragmatic, singular exception (it's one section,
  one feature, works today), or **(b)** invert it — `features/trace/`
  registers its defaults-builder with the settings dialog via a callback
  wired at `app/window.py` (which already legitimately imports both `ui`
  and `features`), so `settings_dialog.py` stops importing `features`
  directly. (b) is the structurally correct fix and adds zero files (a
  parameter, not a module) — but it's more invasive than a rename, so it's
  presented as a choice, not a default action.

**Also found, no action proposed:** `ui/components/focus.py`'s
`CanvasEscapeRouter` duck-types canvas internals (`_mode`, `exit_to_select`,
etc.) without a static import, so it passed every grep-based check but is
the same coupling problem in a different disguise. Consumed only by
`canvas/view/config.py`. Worth knowing about; not proposing a move — it
already works, has no import to fix, and relocating it is a judgment call
with no forcing evidence either way.

**Corrects an assumption in the *other* `plan.md`:** that document's Phase 3
task 3.5 says to delete `ui/components/workflow.py` "if nothing else
imports it." **It's not dead** — `StatusRegion`/`OperationProgress`/
`set_status_label` (the module's actual live contents; `workflow_strip` is
already gone) are imported by five feature pages plus `update_dialog.py`.
Deleting it would break all of them. Noting the factual finding here only —
not editing the other `plan.md`, per this document's own opening note about
not reconciling the two.

**Confirmed not duplication:** `ui/components/tokens.py` vs.
`ui/style/tokens.py` — the latter is the single source of truth; the former
is a documented, deliberate re-export of only the spacing/motion subset.

### engine/ (50 files) — DONE, the most substantive findings of the pass

No Qt/PySide6/PyQt imports and no `app`/`canvas`/`features`/`ui` imports
found anywhere in `engine/` — rules 2 and 4 hold. Three findings:

**1. An undeclared `engine → document` edge (decision needed, not a
mechanical fix).** `engine/cad/editor_geometry.py` imports
`document.model.EntityRecord`, `engine/cad/snapping.py` imports
`document.identity.EntityId`, and `engine/editing/transform.py` imports
`document.model.new_entity_id`. ARCHITECTURE.md's diagram places `document`
above `engine` (document depends on engine, not the reverse) —
`document/model.py` already imports `engine.cad.constraints` the other
way. The enumerated rules (1-7) never actually forbid engine→document
explicitly, only the diagram implies it, and the diagram calls itself "a
navigation model rather than permission for every possible edge." So this
isn't a broken rule, but it is a real mutual dependency between two
packages each described as independent of the other — worth deciding
deliberately rather than leaving implicit:
  - **Option A:** formalize `document` as a legitimate engine dependency
    (update the diagram + add the missing test assertion so it can't drift
    further).
  - **Option B:** decouple — have these three call sites accept a plain
    id/Protocol instead of importing the concrete `document` types.
  This is a judgment call for Gate 2, not something to execute unilaterally.

**2. Duplicated transform math:** `engine/cad/editor_geometry.py`'s
`PolylineGeometry.translate/rotate/scale` reimplement the same formulas as
the free functions `translate/rotate/scale` in `engine/editing/transform.py`
instead of calling them. Proposed: have `PolylineGeometry`'s methods
delegate to `editing/transform.py`'s functions. Net effect: fewer lines,
zero new files.

**3. Duplicated containment/nesting logic:**
`engine/patterns/_shared.py::nested_polygon_region` and
`engine/patterns/regions.py::build_region_tree` both independently
implement STRtree-prefiltered polygon-containment/nesting-depth detection,
for related but distinct purposes (merged fill geometry vs. an explicit
region tree). Given the root `plan.md`'s Phase 1 already anticipates this —
it says `_zone_nested_exclusions` "becomes redundant... remove it in Phase 5
once nothing else feeds it" — this looks like a known, already-scheduled
consolidation rather than a new problem. Noted for Gate 2, not re-proposed
as new work; defer to the existing plan.md phase 5 item.

**Minor, low-priority:** `engine/formats/service.py`'s docstring references
pre-restructure paths (`backend.dxf.service`, "ui layer", "backend") that no
longer exist — stale documentation, not a structural problem. Proposed:
fix the docstring text only.

No file moves proposed from this scan.

## Consolidation Opportunities

Tested the single biggest lever available and it failed verification — see
below. The remaining candidates were found by actually building the import
graph across all 187 files (script: `ast`-walk every `ImportFrom`/`Import`,
resolve relative imports, count distinct importer files per module) and
checking which small, single-caller modules have no independent identity
beyond "one function, filed separately."

### Rejected: implicit namespace packages (would have removed 23 files)

23 of the 187 files are `__init__.py` containing nothing but a one-line
docstring. Python 3 doesn't require `__init__.py` for a package
(PEP 420 namespace packages), so I tested removing all 23 and adding
`namespaces = true` to `pyproject.toml`'s `[tool.setuptools.packages.find]`
in this worktree:

- `pytest` (all 297 tests) passed.
- Direct imports of every major module passed.
- **The built wheel silently broke at runtime**:
  `platform/paths.py`'s `from simple_stipple import resources as
  runtime_resources` raised `ImportError: cannot import name 'resources'
  from 'simple_stipple' (unknown location)` the moment the wheel was
  installed into a clean venv and actually run —
  `simple_stipple/resources/` holds only data files (`tiles/*.dxf`), no
  `.py` besides the deleted `__init__.py`, so setuptools' namespace-aware
  package discovery doesn't reliably package it the same way, and the
  `from package import subpackage` idiom doesn't resolve the same way for
  a namespace package as it does for a regular one.
- Pytest didn't catch this because `pythonpath = ["src"]` runs directly
  against the source tree, not the packaged/installed form — exactly the
  gap between "tests pass" and "the shipped PyInstaller executable works."

**Reverted.** This would have been by far the largest single reduction
(23 files, ~12% of the tree) but it demonstrably breaks the actual shipped
artifact for a desktop app that only exists as a PyInstaller build. Not
worth the risk for a one-time file-count win. Fully reproducible if you
want to re-verify: delete the 23 zero-content `__init__.py` files, add
`namespaces = true`, run `uv build --wheel`, install the wheel fresh, and
try to launch it.

### Verified safe — 5 files, all single-purpose shims with one caller each

Each of these has exactly one importer (confirmed via the import-graph
script) and is either a thin pass-through or a single tiny function with no
identity beyond that:

1. **`canvas/rendering/scene.py`** (19 lines) + **`canvas/rendering/overlays.py`**
   (20 lines) → merge both into `canvas/renderer.py`. Already characterized
   in the Phase 2 canvas/ scan as "thin ordering wrappers... delegate
   straight back into renderer's private methods," called only from
   `renderer.paintEvent`. `renderer.py` is already assessed do-not-split
   for size, so absorbing 39 more lines changes nothing about its risk
   profile. **−2 files**, plus their now-empty `canvas/rendering/` package
   (and its `__init__.py`) can go too if nothing else lives there —
   confirmed nothing else does. **−3 files total.**
2. **`features/trace/trace_jobs.py`** (19 lines) → inline into
   `features/trace/page.py`, its only caller. Already characterized as "a
   thin re-export of engine tracing/raster functions" with no logic of its
   own. **−1 file.**
3. **`engine/formats/dxf_schema.py`** (29 lines, one function —
   `validate_dxf_document`) → merge into `engine/formats/dxf.py`, its only
   caller. Read in full: it's a single validation function, no reason to
   be a separate file. **−1 file.**
4. **`engine/cad/coordinates.py`** (33 lines, one function —
   `parse_coordinate`) → merge into `engine/cad/geometry.py`, the natural
   home for CAD coordinate math; its only caller is
   `canvas/view/helpers.py`. Read in full: a single parsing function.
   **−1 file.**

**Subtotal: −6 files**, on top of the −1 already planned (`sliders.py`) and
+1 already planned (`canvas/dialogs/__init__.py`) from Gate 2.

### Optional, larger lever — requires your decision, not proposed by default

`engine/editing/` holds 9 files, 873 lines total, averaging ~97 lines each
— `offset.py`(23), `resample.py`(59), `transform.py`(59), `boolean.py`(63),
`trim_extend.py`(71), `merge_explode.py`(95), `clipper_engine.py`(145),
`smoothing.py`(161), `split.py`(197) — one geometric operation per file, a
consistent, deliberate pattern across the whole package (not shims — each
is a real, independently-testable algorithm). Grouping these into ~3 files
by relation (e.g. path-reshaping ops together, boolean/transform ops
together) could plausibly save **5-6 more files**. Not proposing this by
default: it reverses an intentional, consistently-applied style choice
rather than fixing a shim, and the grouping itself is a judgment call with
no single obviously-correct answer. Say the word and I'll do it.

2. **`engine/cad/editor_geometry.py`'s `PolylineGeometry.translate/rotate/scale`
   → delegate to `engine/editing/transform.py`'s `translate/rotate/scale`.**
   Identical formulas, two implementations. Fewer lines, zero new files.
3. **`engine/patterns/_shared.py::nested_polygon_region` vs.
   `engine/patterns/regions.py::build_region_tree`** — real overlap, but
   already scheduled: the root `plan.md` Phase 5 says
   `_zone_nested_exclusions` "becomes redundant... remove it in Phase 5 once
   nothing else feeds it." Deferred to that existing item, not re-proposed.
4. **`features/pattern/sliders.py` (`PatternSlider`)** — dead code, delete.
   Reduces file count by one.

## Centralization Recommendations

Three places where a dependency currently points the wrong way relative to
the documented layering. None are silently executed — each is a decision:

1. **`engine → document`** (`editor_geometry.py`, `snapping.py`,
   `editing/transform.py` import `EntityRecord`/`EntityId`/`new_entity_id`).
   Not a literal cycle (`document` doesn't import these back), but both
   packages are described as independent of each other and aren't. Decide:
   formalize (update the diagram + add a test) or decouple (accept a plain
   id/Protocol instead of the concrete `document` type at these 3 call
   sites).
2. **`ui → canvas`** (3 files: `customize_dialogs.py`, `keybindings_dialog.py`,
   `text_dialog.py`) — this one **is** a literal cycle, since `canvas`
   already imports `ui` in 14 places. Already decided: relocate to
   `canvas/dialogs/`. Ready to execute (file count neutral).
3. **`ui → features`** (`settings_dialog.py` imports `features.trace.form`)
   — not cyclic, but backwards (leaf importing from the product layer) and
   a fan-in risk if other features want defaults sections later. Decide:
   accept as a scoped exception, or invert via a callback `features/trace/`
   registers at `app/window.py` (zero new files either way).

The other four `ui → engine`/`ui → document` imports found in the `ui/`
scan (`import_dialog.py`, `fvi_dialog.py`, `export_preflight.py`,
`workspace_library.py`) are recommended as a **documentation fix only** —
see Naming Improvements below — since `engine`/`document` don't import `ui`
back and nothing about them is structurally broken.

## Naming Improvements

1. **`canvas/tools/select.py`'s `SelectionService`** → rename/move to
   `canvas/operations/select.py`. It's a stateful service constructed the
   same way every `operations/*` service is, filed under `tools/`, which its
   sibling `tools.py` defines as strategy objects that are explicitly
   *stateless*. Low risk, a rename — moved, not added.
2. **`engine/formats/service.py`'s docstring** references pre-restructure
   paths (`backend.dxf.service`, "ui layer", "backend") that no longer
   exist. Fix the text; no code change.
3. **`ARCHITECTURE.md`'s description of `ui`** should say the enforced rule
   precisely — "`ui` never imports `canvas`" — rather than the broader
   "genuinely shared... " phrasing that reads as forbidding engine/document
   too. The four `ui → engine`/`ui → document` imports found in Phase 2 are
   legitimate (dialogs whose job requires those types) and aren't going
   anywhere; the doc should say so instead of implying they're violations.

## Next Steps

In dependency order — each step leaves the app in a working state; run
`pytest tests/test_dependency_boundaries.py tests/test_module_homes.py
tests/test_page_feature_packages.py` after each:

1. Copy the worktree's restored `ARCHITECTURE.md` to the repo root; fix the
   `ui` description per Naming Improvements #3; fix `README.md`'s link (it
   already points to the right path, just confirm it resolves once the file
   exists again).
2. Execute the settled move: `ui/dialogs/{customize_dialogs,
   keybindings_dialog,text_dialog}.py` → `canvas/dialogs/` (new
   `__init__.py` needed for the subpackage — one file added, three moved).
   Update their import sites (`app/menu.py`, `app/window.py`, wherever else
   references them — grep `from simple_stipple.ui.dialogs.customize_dialogs`
   etc. and update). Add the test assertion: `ui` must never import
   `canvas`.
3. Delete `features/pattern/sliders.py`.
4. Fix `engine/formats/service.py`'s docstring.
5. Delegate `PolylineGeometry.translate/rotate/scale` to
   `engine/editing/transform.py`'s functions.
6. Rename/move `canvas/tools/select.py` → `canvas/operations/select.py`;
   update its ~6 import sites (`view/config.py`, `view/interactions.py`,
   `view/main.py`, `widget.py` — grep `canvas_tools.SelectTool` and
   `tools.select` to find all of them; note this does not touch
   `tools.tools.SelectTool`, a different class with a confusingly similar
   name that stays where it is).
7. Decide and act on the two judgment calls: `engine ↔ document` (formalize
   vs. decouple) and `ui → features.trace.form` in `settings_dialog.py`
   (accept vs. invert-via-callback). Both are safe to leave as-is
   indefinitely if no decision is made — neither is a bug today.

Everything else in the codebase — the full remaining ~175 files — is
confirmed correctly placed with no action needed.
