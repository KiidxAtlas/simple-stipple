# File Structure Refactor Plan

Date: 2026-08-04
Status: **Completed — Phases 0–5 complete**

## Current State Summary

**Confirmed.** This is a Python 3.10+ PySide6 desktop application using a `src/` package layout (`pyproject.toml:1-34`, `src/simple_stipple/`). The package contains 186 source files, distributed across `app` (8), `canvas` (39), `document` (7), `engine` (50), `features` (30), `platform` (8), and `ui` (41). The previous capability-first migration is already present; no duplicate `backend`/`core` tree remains.

**Confirmed — useful boundaries.** `features/pattern/` is a cohesive feature package (`page.py`, `layout.py`, `session.py`, `zones.py`, `workers.py`); `engine/` holds UI-free computation; and `ui/` holds shared Qt components. These should remain packages, not be flattened for file-count reasons.

**Confirmed — overlapping canvas orchestration.** The same interaction system is spread through `src/simple_stipple/canvas/widget.py`, `view/main.py`, `view/commands.py`, `tools/tools.py`, `operations/editing.py`, and `renderer.py`. `widget.py` imports both `CanvasView` and tool services (`widget.py:17-20`), while `view/main.py` imports commands, tools, operations, renderer-facing state, and document types (`view/main.py:23-94`). This causes high navigation cost and unclear ownership.

**Confirmed — oversized multi-concern modules.** `src/simple_stipple/canvas/widget.py`, `renderer.py`, `operations/editing.py`, `tools/tools.py`, `view/main.py`, and `src/simple_stipple/features/pattern/page.py` are all listed as complexity exceptions in `pyproject.toml:61-88`. Consolidating these would worsen the problem; they need responsibility-based extraction.

**Confirmed — naming/ownership ambiguity.** `src/simple_stipple/engine/workflows.py` coordinates laser package export, raster engraving, trace, cancellation, patterns, and presets (`workflows.py:8-24`), despite `features/pattern/workers.py:19-20` and `features/trace/page.py:43` owning those user workflows. Its name does not reveal whether it is a pure engine facade or feature orchestration.

**Inferred — no cosmetic mega-reorganization.** The current depth is at most four source-package levels (for example `ui/dialogs/*.py` and `features/pattern/*.py`) and maps to real boundaries. A wide flattening would create import churn without reducing coupling.

## Target Pattern

Retain the **PyPA `src/` layout** and **feature modules over shared engine/document/canvas subsystems**. PyPA documents why `src/` avoids accidental imports from repository-root files: [src layout vs flat layout](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/). Qt's model/view guidance supports retaining a distinct document/model and presentation/editor boundary: [Qt Model/View Programming](https://doc.qt.io/qt-6/model-view-programming.html).

Target rules:

1. `engine` remains UI-free and contains reusable algorithms/formats only.
2. `features/<feature>` owns user workflow coordination; features do not import each other's internals.
3. `canvas` owns input interpretation, rendering, selection, and editor state—not feature export workflows.
4. `document` remains the canonical state and mutation boundary.
5. A module may be split only along a stable responsibility, never merely to make files smaller.

## Before/After Tree

```text
# Before (relevant hotspots)
canvas/{widget.py,renderer.py,tools/tools.py,operations/editing.py,view/{main.py,commands.py}}
engine/workflows.py
features/{pattern/{page.py,workers.py},trace/page.py}

# After
canvas/
  widget.py                    # thin public composition facade
  interaction/{context_menu.py,quick_shapes.py}
  rendering/{scene.py,overlays.py}
  operations/{editing.py,selection_actions.py}
  view/{main.py,commands.py}
engine/
  workflows.py                 # removed after feature-owned pure helpers are placed below
  imaging/{raster.py,trace.py}
  formats/laserstar.py
features/
  pattern/{page.py,workers.py,export_jobs.py}
  trace/{page.py,trace_jobs.py}
```

## Full Path Mapping Table

| Old path | New path | Reason | Risk |
|---|---|---|---|
| `src/simple_stipple/canvas/widget.py` | retain facade; extract `canvas/interaction/context_menu.py` | isolate context-menu construction currently mixed with canvas composition | High — public widget and dynamic Qt actions |
| `src/simple_stipple/canvas/widget.py` | retain facade; extract `canvas/interaction/quick_shapes.py` | isolate procedural/quick-shape gesture state | High — gesture/undo behavior |
| `src/simple_stipple/canvas/renderer.py` | `canvas/rendering/scene.py` + `canvas/rendering/overlays.py` | split geometry scene rendering from HUD/rulers/gizmos | High — paint ordering/performance |
| `src/simple_stipple/canvas/operations/editing.py` | retain core; extract `canvas/operations/selection_actions.py` | separate selection-level commands from vertex-edit operations | High — document command invariants |
| `src/simple_stipple/engine/workflows.py` | `features/pattern/export_jobs.py`; `features/trace/trace_jobs.py`; retain only pure shared helpers under engine | remove ambiguous cross-feature workflow facade | High — background workers/imports |
| `src/simple_stipple/features/pattern/page.py` | retain coordinator; extract export-only methods to `features/pattern/export_jobs.py` | page remains UI composition, export jobs become testable | Medium — Qt callbacks and settings |
| `src/simple_stipple/canvas/view/main.py` | retain | no move proposed; it is the intentional view composition boundary | Low |
| `src/simple_stipple/ui/{components,dialogs,style}` | retain | confirmed shared presentation boundary | Low |

## Migration Phases

- [x] **Phase 0 — Characterize seams (safe to automate):** add import/behavior tests for canvas context actions, quick shapes, renderer overlay order, Pattern export and Trace jobs.
- [x] **Phase 1 — Extract canvas interaction modules (requires focused review):** move context-menu and quick-shape helpers out of `canvas/widget.py`; preserve the widget public API.
- [x] **Phase 2 — Split renderer by paint responsibility (requires manual judgment):** move only cohesive scene/overlay helpers after a paint-order characterization suite exists.
- [x] **Phase 3 — Replace `engine/workflows.py` (requires manual judgment):** identify pure helpers versus Pattern/Trace ownership; migrate callers with no compatibility wrapper.
- [x] **Phase 4 — Pattern export job boundary (safe after Phase 3):** move export task assembly out of `features/pattern/page.py`; keep UI dialogs/signals in the page.
- [x] **Phase 5 — Boundary enforcement (safe to automate):** add import-linter contracts: engine never imports Qt/features; feature-to-feature internals forbidden; canvas never imports feature modules.

## Import/Reference Update Strategy

Before each move, use `rg -n 'simple_stipple\.(canvas|engine|features)' src tests scripts` to enumerate imports, dynamic imports, monkeypatch targets, and packaging references. Rewrite absolute imports in the same change as the move. Update `tests/test_module_homes.py`, `tests/test_dependency_boundaries.py`, command-palette identifiers, and `pyproject.toml` per-file complexity exceptions when a moved module's responsibility changes. Search source and tests again for the old path; the count must be zero before the phase closes.

## Risk & Rollback

High-risk Qt modules can fail through signal disconnection, QObject ownership, paint order, or input-state regressions. Each phase is a separate, uncommitted review unit; rollback is `git restore --source=HEAD -- <paths>` for only that phase, or `git reset --mixed <phase-start>` if the user has committed the phase. Never delete the original module until imports, tests, and an offscreen smoke test pass.

## Verification Plan

After every phase run:

```bash
ruff check src tests
pytest -q
python scripts/validate_codebase.py
python -m simple_stipple
```

For Phases 1–2 also exercise Draft and Pattern canvas draw/select/context/undo flows manually. For Phases 3–4 exercise Trace → Draft, Draft → Pattern, vector export, engraving export, cancellation, and error presentation. For Phase 5 run the new import-linter contract in CI and locally.

## Changelog

- 2026-08-04 — Phase 0: added `tests/test_refactor_phase0_characterization.py` to lock down canvas context actions, quick-shape drag commits, scene/tool/chrome paint order, Pattern export-worker protocol, and Trace worker signals. Focused verification: `ruff check tests/test_refactor_phase0_characterization.py` and `pytest -q tests/test_refactor_phase0_characterization.py tests/test_ui_audit_remediations.py` (123 passed).
- 2026-08-04 — Phase 1: extracted context-menu orchestration/customization to `canvas/interaction/context_menu.py` and quick-shape geometry/state helpers to `canvas/interaction/quick_shapes.py`. `DxfCanvas` retains its public methods as delegating compatibility wrappers. Verification: `pytest -q` (158 passed), focused canvas/UI suite (123 passed), `ruff check src tests`, and `git diff --check`. `scripts/validate_codebase.py` retains pre-existing repository-wide length/documentation findings but reports no new interaction-module finding.
- 2026-08-04 — Phase 2: extracted semantic document-scene, selection-overlay, and final-ruler chrome passes to `canvas/rendering/{scene,overlays}.py`; `CanvasRenderer` remains the public paint facade. Extended Phase 0 characterization coverage to assert scene → selection → chrome internal ordering. Verification: `pytest -q` (159 passed), focused canvas/UI suite (124 passed), `ruff check src tests`, and `git diff --check`. `scripts/validate_codebase.py` reports no new rendering-module finding.
- 2026-08-04 — Phase 3: removed `engine/workflows.py` with no compatibility facade. Pattern export dependencies now live at `features/pattern/export_jobs.py`; Trace worker/engraving dependencies now live at `features/trace/trace_jobs.py`; pure patterns, imaging, and preset helpers are imported from their existing engine homes. Verified zero remaining facade references, `pytest -q` (159 passed), `ruff check src tests`, `git diff --check`, and no new validator finding for the new feature modules.
- 2026-08-04 — Phase 4: moved Pattern raster and LaserStar export payload assembly to `features/pattern/export_jobs.py` via `EngravingJob`, positioned-engraving export, and LaserStar-package export functions. `PatternPage` retains dialogs, validation, error presentation, state updates, and status messaging. Added a direct job-payload characterization test. Verification: `pytest -q` (160 passed), focused export/UI suite (125 passed), `ruff check src tests`, `git diff --check`, and no new validator finding for the export-job module.
- 2026-08-04 — Phase 5: strengthened `tests/test_dependency_boundaries.py` as the dependency-free import-linter contract. It now resolves relative imports and literal dynamic imports, and explicitly enforces: engine never imports Qt/features, canvas never imports features, and feature packages never import another feature's internals (except shared `features.base`). Verification: `pytest -q` (162 passed), boundary/module-home suite (15 passed), `ruff check src tests`, and `git diff --check`.
