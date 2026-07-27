# File Structure Refactor Plan

Date: 2026-07-27  
Status: **Implemented — 2026-07-27**

## 1. Executive summary

Simple Stipple should become a **capability-first modular monolith with a thin Qt shell**.
The existing `src` layout is correct and should remain. The main problem is not raw file
count alone: it is that product workflows are split across `ui/pages`, `app/services`,
`app/controllers`, and `backend`, while 34 package initializers and several one-file
subpackages add navigation without adding meaningful boundaries.

The plan reduces ceremony and makes product vocabulary visible at the package root:

```text
simple_stipple/
├── app/                 startup, main window, navigation, app-wide coordination
├── document/            canonical document, commands, history, workspace persistence
├── engine/              reusable non-UI computation and file formats
├── features/            draft, pattern, trace, convert, repository, help
├── canvas/              reusable interactive editor subsystem
├── ui/                  genuinely shared Qt components, dialogs, theme
├── platform/            paths, settings, storage, updates, reporting
└── resources/           packaged runtime assets
```

Expected result: roughly 165–172 Python modules (from 193), fewer shallow namespace
folders, no new mega-files, and a predictable answer to “where does this belong?”

## 2. Evidence and diagnosis

### Repository inventory

- 193 Python modules / approximately 62,255 Python lines.
- Major module counts: `ui` 111, `backend` 53, `app` 18, `core` 8.
- Product pages are registered together at `app/page_runtime.py:11-69`, but their
  workflows are physically split across multiple architectural layers.
- The documented architecture explicitly assigns workflow orchestration to
  `app.services` (`ARCHITECTURE.md:28-31`) while feature views live in `ui.pages`
  (`ARCHITECTURE.md:12-16`). That rule creates cross-tree traversal by design.
- Placement rule 4 says nested `domain/` or `ui/` packages should exist only for cohesive
  modules (`ARCHITECTURE.md:53-54`), yet Draft and Trace each have one-file `domain`
  subpackages and Convert has a one-file `ui` subpackage.
- Entity identity belongs to the document aggregate but resides in generic `core`;
  `backend/model/document.py:31-43` exposes that ownership mismatch.
- The canonical document and command mutation boundary live in separate layer roots:
  `backend/model/document.py:37-66` and `app/services/document_service.py:30-43,96-100`.

### Navigation-cost findings

**High — a feature is not a locality.** Understanding Draft, Pattern, Trace, or Convert
requires searching page code, generic app services/controllers, backend processors, shared
canvas code, and dialogs. Fix: put feature-exclusive page, session, worker, and orchestration
code in one `features/<name>/` package; dependencies shared by two or more features remain
in `document`, `engine`, `canvas`, or `ui`.

**High — excessive namespace ceremony.** `help/page.py`, `repository/page.py`,
`convert/ui/subtabs.py`, `draft/domain/session.py`, `trace/domain/session.py`,
`trace/ui/form.py`, `canvas/rendering/renderer.py`, and
`widgets/controls/cycle_icon_button.py` each sit behind a package that adds little
information. Fix: flatten these paths as listed in Section 5.

**High — file-count-only merging would worsen risk.** Existing hotspots include
`canvas/tools/tools.py` (2,608 lines), `pattern/tab.py` (2,361),
`canvas/view/main.py` (2,282), `canvas/rendering/renderer.py` (2,265), and
`canvas/operations/editing.py` (1,918). Fix: prohibit consolidation into files over
approximately 600 lines or modules containing more than one independently changing concern;
large hotspot decomposition is a separate behavior-preserving effort.

**Medium — technical layer names obscure intent.** `backend` says where code runs, not what
it does; `core` is a catch-all containing process launch, storage, identity, reporting,
settings, and updates. Fix: use `engine`, `document`, and `platform`, each with a concrete
ownership test.

**Medium — dual document ownership.** State types and commands are in `backend.model`, but
the only command-oriented mutation boundary is in `app.services`. Fix: make `document/`
one cohesive subsystem and expose mutations through its public package API.

## 3. Research and design principles

The target architecture applies these sources:

1. Keep the existing `src` layout. PyPA explains that it prevents the repository root from
   accidentally shadowing the installed package and makes editable installs behave more
   like regular installs:
   https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/
2. Decompose by hidden design decisions, not execution steps. Parnas's modularity criterion
   is that a module hides decisions likely to change and can be understood independently:
   https://doi.org/10.1184/r1/6607958
3. Preserve model/presentation separation for the editor. Qt's model/view architecture
   separates stored data from presentation and interaction through defined interfaces,
   signals, and delegates:
   https://doc.qt.io/qtforpython-6/overviews/qtwidgets-model-view-programming.html
4. Respect Qt ownership and lifecycle. QObject parent/child trees provide deterministic
   ownership and deletion, so UI composition should remain in the Qt-facing shell rather
   than leak into pure engine modules:
   https://doc.qt.io/qtforpython-6/overviews/qtcore-objecttrees.html
5. Use packages only where a namespace communicates a stable subsystem. Python packages are
   a way to structure a module namespace; they are not a requirement for every class or
   single implementation file:
   https://docs.python.org/3/tutorial/modules.html#packages

### Local decision rules

1. **Navigate by product first.** Feature-exclusive behavior lives with the feature.
2. **Promote only proven sharing.** Code moves to a shared subsystem when at least two
   independent features use it or it is an explicit domain aggregate.
3. **One reason to change per module.** File count is secondary to conceptual cohesion.
4. **No generic buckets.** Do not add `common`, `misc`, `helpers`, or `utils`.
5. **Public package API, private implementation.** Cross-subsystem imports use documented
   exports; features never import another feature's internals.
6. **Dependencies point inward:** `platform` and `engine` import no app/feature/UI code;
   `document` may use engine value types; `canvas` may use document/engine/UI; features may
   use all shared subsystems; `app` composes everything.
7. **Qt stays at the edge.** Engine and document state remain independently testable.
8. **Progressive disclosure.** Each package gets a short README only when its responsibility
   cannot be stated in one line in the root architecture map.

## 4. Target architecture and onboarding route

```text
simple_stipple/
├── __main__.py
├── app/
│   ├── launcher.py
│   ├── window.py
│   ├── pages.py              page registry/runtime
│   ├── menu.py
│   ├── tasks.py
│   └── settings_controller.py
├── document/
│   ├── model.py
│   ├── commands.py
│   ├── history.py
│   ├── service.py
│   └── workspace.py
├── engine/
│   ├── cad/
│   ├── editing/
│   ├── geometry/
│   ├── patterns/
│   ├── imaging/
│   └── formats/              dxf.py/service modules, fvi.py, svg.py, laserstar.py
├── features/
│   ├── draft/                page.py, session.py
│   ├── pattern/              page.py, session/workers/zones/forms/presets
│   ├── trace/                page.py, session.py, form.py
│   ├── convert/              page.py, tasks.py
│   ├── repository.py
│   └── help.py
├── canvas/
│   ├── widget.py
│   ├── runtime.py
│   ├── renderer.py
│   ├── tools/
│   ├── operations/
│   └── view/
├── ui/
│   ├── components/
│   ├── dialogs/
│   ├── widgets/
│   ├── theme.py
│   ├── notifications.py
│   ├── files.py
│   └── units.py
├── platform/
│   ├── launcher.py
│   ├── paths.py
│   ├── settings.py
│   ├── storage.py
│   ├── updates.py
│   └── error_reporting.py
└── resources/
```

### Five-minute onboarding path

1. Read `README.md` for the user problem and `ARCHITECTURE.md` for the placement rules.
2. Read `app/launcher.py`, `app/window.py`, and `app/pages.py` for lifecycle/composition.
3. Open `features/<workflow>/page.py` for the behavior being changed.
4. Follow shared editing into `canvas/`, state mutation into `document/`, and pure
   algorithms/formats into `engine/`.
5. Use `.opencode/knowledge/PROJECT_MAP.md` as the symbol/flow index.

## 5. Full path mapping

Paths not named below retain their basename under the mapped package parent. All moves are
direct moves—no permanent compatibility wrappers.

### Application shell

| Current | Target | Decision |
|---|---|---|
| `app/launcher.py` | `app/launcher.py` | keep |
| `app/window.py` | `app/window.py` | keep |
| `app/page_runtime.py` | `app/pages.py` | rename to product language |
| `app/controllers/menu.py` | `app/menu.py` | flatten one-purpose controller package |
| `app/controllers/tasks.py` | `app/tasks.py` | flatten |
| `app/controllers/settings.py` | `app/settings_controller.py` | flatten, avoid collision |
| `app/controllers/workspace.py` | `document/workspace_controller.py` | move to document workflow |
| `app/workspace_session.py` | `document/workspace.py` | colocate persistence session |
| `app/config.py` | `app/config.py` | keep composition defaults |

Delete `app/controllers/__init__.py` after moves. `app/services` is dissolved by ownership:

| Current service | Target |
|---|---|
| `canvas_service.py` | `canvas/service.py` |
| `document_service.py` | `document/service.py` |
| `geometry_service.py` | `engine/geometry/service.py` |
| `dxf_service.py` | `engine/formats/service.py` |
| `model_service.py` | merge into `document/service.py` if its EntityId conversion remains cohesive |
| `presets_service.py` | `features/pattern/preset_store.py` if pattern-only; otherwise `platform/presets.py` |

The preset target is conditional on a call-site scan during implementation; no generic
`services` package remains.

### Document subsystem

| Current | Target | Decision |
|---|---|---|
| `core/entities.py` | `document/identity.py` | identity belongs to document |
| `backend/model/document.py` | `document/model.py` | canonical aggregate |
| `backend/model/commands.py` | `document/commands.py` | mutation vocabulary |
| `backend/model/editor_history.py` | `document/history.py` | keep separate reversible-state concern |
| `app/services/document_service.py` | `document/service.py` | canonical mutation boundary |

Delete `backend/model/` after moves. Do not merge model, commands, and service: each is
already substantial and changes for a different reason.

### Engine

| Current | Target |
|---|---|
| `backend/cad/*` | `engine/cad/*` |
| `backend/geometry/*` | `engine/geometry/*` |
| `backend/pattern/*` | `engine/patterns/*` |
| `backend/image/raster_engraving.py` | `engine/imaging/raster.py` |
| `backend/image/trace.py` | `engine/imaging/trace.py` |
| `backend/dxf/fix.py` | `engine/formats/dxf_fix.py` |
| `backend/dxf/io.py` | `engine/formats/dxf.py` |
| `backend/dxf/schema.py` | `engine/formats/dxf_schema.py` |
| `backend/dxf/fvi.py` | `engine/formats/fvi.py` |
| `backend/dxf/svg_dxf.py` | `engine/formats/svg.py` |
| `backend/dxf/service.py` + `app/services/dxf_service.py` | `engine/formats/service.py` after duplicate-boundary review |
| `backend/export/laserstar_package.py` | `engine/formats/laserstar.py` |

Delete `backend`, `backend/dxf`, `backend/export`, and `backend/image` namespace shells once
imports are migrated.

Editing keeps its meaningful algorithms, while four tiny stateless path-transform modules
are consolidated:

| Current | Target |
|---|---|
| `editing/offset.py`, `resample.py`, `transform.py`, `trim_extend.py` | `engine/editing/paths.py` |
| `editing/boolean.py`, `clipper_engine.py`, `merge_explode.py`, `smoothing.py`, `split.py` | same basename under `engine/editing/` |

`paths.py` must remain below 600 lines and expose named functions; if it exceeds the gate,
retain `transform.py` separately.

### Features

| Current | Target |
|---|---|
| `ui/pages/base.py` | `features/base.py` |
| `ui/pages/draft/page.py` | `features/draft/page.py` |
| `ui/pages/draft/domain/session.py` | `features/draft/session.py` |
| `ui/pages/pattern/tab.py` | `features/pattern/page.py` |
| `ui/pages/pattern/domain/*.py` | same basename under `features/pattern/` |
| `ui/pages/pattern/ui/form.py` | `features/pattern/form.py` |
| `ui/pages/pattern/ui/form_spec.py` | `features/pattern/form_spec.py` |
| `ui/pages/pattern/ui/layout.py` | `features/pattern/layout.py` |
| `ui/pages/pattern/ui/params.py` | `features/pattern/params.py` |
| `ui/pages/pattern/ui/presets_dialog.py` | `features/pattern/presets_dialog.py` |
| `ui/pages/trace/tab.py` | `features/trace/page.py` |
| `ui/pages/trace/domain/session.py` | `features/trace/session.py` |
| `ui/pages/trace/ui/form.py` | `features/trace/form.py` |
| `ui/pages/convert/page.py` | `features/convert/page.py` |
| `ui/pages/convert/ui/subtabs.py` | `features/convert/tasks.py` |
| `ui/pages/repository/page.py` | `features/repository.py` |
| `ui/pages/help/page.py` | `features/help.py` |

Delete the old `ui/pages` tree and its nested one-file `domain`/`ui` packages. Pattern
remains multi-module because it is a real feature subsystem; flat named modules are easier
to scan than generic `domain` and `ui` layers at this scale.

### Canvas and shared UI

| Current | Target |
|---|---|
| `ui/canvas/widget.py`, `commands.py`, `constants.py`, `snap.py` | same basename under `canvas/` |
| `ui/canvas/model/canvas_runtime.py` | `canvas/runtime.py` |
| `ui/canvas/model/canvas_model.py` | merge into `canvas/runtime.py` only if combined file stays cohesive and <600 lines |
| `ui/canvas/rendering/renderer.py` | `canvas/renderer.py` |
| `ui/canvas/tools/*`, `operations/*`, `view/*` | same subpath under `canvas/` |
| `ui/widgets/canvas/*` | `canvas/widgets/*` |
| `ui/widgets/layer_tree/*` | `canvas/layers/*` |
| `ui/components/*` | retain under `ui/components/*` |
| `ui/widgets/dialogs/*` | `ui/dialogs/*` |
| `ui/widgets/controls/cycle_icon_button.py` | `ui/components/cycle_button.py` |
| `ui/widgets/pattern_sliders.py` | `features/pattern/sliders.py` |
| `ui/widgets/shape_recognition_dialog.py` | `features/draft/recognition_dialog.py` |
| `ui/file_dialogs.py` | `ui/files.py` |
| `ui/style/theme.py` | `ui/theme.py` |
| `ui/notifications.py`, `ui/recent.py`, `ui/units.py` | retain pending semantic ownership review |

Keep each named dialog in its own module; combining 13 dialogs would reduce file count but
make ownership and review worse. Keep `ui/components/tokens.py`: it is a stable design-token
boundary used across dialogs, not arbitrary fragmentation.

### Platform and resources

| Current | Target |
|---|---|
| `core/launcher.py` | `platform/launcher.py` |
| `core/paths.py` | `platform/paths.py` |
| `core/settings.py` | `platform/settings.py` |
| `core/storage.py` | `platform/storage.py` |
| `core/updates.py` | `platform/updates.py` |
| `core/error_reporting.py` | `platform/error_reporting.py` |
| `resources/*` | retain |
| root `assets/icon.*` used at runtime | `resources/icons/` and load with `importlib.resources` |

Delete `core/` after moves. Root build/release artwork that is not runtime data stays outside
the package.

## 6. Dependency invariants and enforcement

```text
platform       engine
    \           /
      document
       /    \
    canvas    features
       \      /
          app
```

- `platform`: imports no `app`, `features`, `canvas`, `ui`, `document`, or `engine`.
- `engine`: imports no Qt, `app`, `features`, `canvas`, or `ui`.
- `document`: imports only platform/engine and no Qt.
- `ui`: reusable Qt primitives; imports platform only where unavoidable.
- `canvas`: may import document, engine, UI, platform; never features/app.
- `features`: may import shared subsystems; never another feature's private modules.
- `app`: composition root; may import all packages.

Update `tests/test_dependency_boundaries.py` and `tests/test_module_homes.py` in the same
phase as each move. Add an import-linter contract (or keep an equivalent AST boundary test)
to make these rules executable. Do not restore the legacy suite.

## 7. Migration phases and validation

### Phase 0 — characterize and measure

- Record `pytest`, Ruff, mypy/pyright, validation-script, import, and offscreen startup
  baselines.
- Add only targeted characterization tests for moved public behavior.
- Capture module count, directory count, maximum depth, cycles, and top fan-in/fan-out.

### Phase 1 — create stable domain nouns

- Move `backend.model` + document services into `document`.
- Move `core` process concerns into `platform`.
- Update imports atomically and enforce lower-layer Qt prohibition.

### Phase 2 — establish shared subsystems

- Rename `backend` capabilities into `engine`.
- Promote `ui.canvas` to root `canvas`.
- Flatten rendering/model/widget namespace shells; consolidate only approved tiny modules.

### Phase 3 — colocate workflows

- Move pages into `features`.
- Flatten one-file `domain` and `ui` subpackages.
- Move feature-exclusive widgets/services beside their owning workflow.
- Replace `app.services` with explicit subsystem APIs.

### Phase 4 — shell and documentation

- Flatten controllers into `app`.
- Package runtime icons/resources consistently.
- Rewrite `ARCHITECTURE.md` to match the target and refresh `PROJECT_MAP.md`.
- Add a concise “Where changes go” table to the contributor/onboarding docs.

### Phase 5 — verification and cleanup

- Remove empty legacy directories only after `rg` confirms no imports/path references.
- Run: focused pytest suite, Ruff, mypy, pyright, repository validator, package build,
  isolated-wheel import, and offscreen startup smoke.
- Run external Windows/Linux packaging CI; local macOS cannot prove those targets.
- Compare module/depth metrics and reject changes that merely rename complexity.

Each phase must remain independently reviewable. Moves and behavior changes should be
separate commits. Use `git mv` during execution, update callers in the same commit, and do
not leave compatibility wrappers.

## 8. Risks, gates, and rollback

| Risk | Gate |
|---|---|
| import churn breaks runtime-only Qt paths | import-all + offscreen startup after every phase |
| moving resources breaks frozen builds | isolated wheel and PyInstaller artifact inventory |
| consolidation creates larger cognitive hotspots | 600-line/one-reason-to-change gate |
| feature colocation introduces feature-to-feature coupling | dependency contract in CI |
| limited semantic tests miss behavior regressions | targeted characterization tests around touched flows |
| massive review obscures failures | one subsystem per commit/phase |

Rollback is phase-local: revert the move commit, not the prior completed refactor and not the
deleted legacy tests. No destructive cleanup occurs until the new paths pass validation.

## Completion record

Implemented after explicit approval. The final structure uses 178 runtime modules and
removes the old `core`, `backend`, `ui.pages`, `ui.canvas`, `app.services`, and
`app.controllers` trees. A few proposed placements were corrected during executable
boundary validation:

- shared settings re-exports live in `platform.config`, not `app.config`;
- the engine workflow facade lives in `engine.workflows`, not `platform`;
- Qt workspace coordination lives in `app.workspace_controller`, while workspace data
  remains in `document.workspace`;
- packaged theme code remains beside its QSS/icons under `ui.style`.

Focused tests, Ruff, import-all, compilation, and circular-import checks pass. The repository
validator continues to report the pre-existing oversized-module backlog; the reorganization
does not pretend those cognitive hotspots were solved by moving them.
