# File Structure Refactor Plan

Date: 2026-08-13
Status: Proposed — replaces the completed, stale plan from 2026-08-11.

## 1. Current State Summary

**Confirmed.** This is a Python 3.10+ PySide6 desktop application using the `src/`
package layout (`pyproject.toml:1-33`). The runtime contains 166 Python modules and
seven capability homes: `app/`, `canvas/`, `core/`, `features/`, `platform/`,
`resources/`, and `ui/` (`tests/test_module_homes.py:10-18`). The package has no
import cycle (`python scripts/check_circular_imports.py`: 166 modules scanned).

**Confirmed strengths.** The top-level tree is already responsibility-first:
Qt-free business logic is in `core/`; reusable editor behavior is in `canvas/`; product
workflows are in `features/`; shared presentation is in `ui/`; application composition
is in `app/`. The executable rules in `tests/test_dependency_boundaries.py:45-139`
confirm those boundaries. There are no `utils`, `common`, `misc`, or `shared` dumping
grounds.

**Confirmed problems.**

| Signal                                 | Evidence                                                                                                                                                                                                                                                       | Consequence                                                                                                                                    |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Oversized coordinators                 | `canvas/renderer.py:1` (2,438 lines), `features/pattern/page.py:1` (2,549), `canvas/view/main.py:1` (1,882), `features/trace/page.py:1` (1,510)                                                                                                        | A reader cannot locate one responsibility without also loading unrelated paint, event, worker, or widget behavior.                             |
| Confusing sibling ownership            | `canvas/renderer.py:91` delegates to `canvas/rendering.py:14`; `features/pattern/layout.py:9-37` delegates to `layout_sections.py:65-754`                                                                                                              | The split is valid, but one file is a generic noun and the other holds the actual named responsibility, making navigation needlessly indirect. |
| Conversion implementation is scattered | `features/convert/form_base.py:45`, `features/convert/tasks.py:38`, `features/convert/svg_tasks.py:28`, consumed from `features/convert/page.py:35-36`                                                                                                 | One feature contains three sibling modules for one closely coupled sub-tab family; imports cross the family in both directions.                |
| Import hygiene drift                   | `ruff check src tests` reports 230 findings, including I001 in `ui/dialogs/update_dialog.py:3`, `tests/test_document_replace_command.py:9`, and many similar import blocks                                                                               | Mechanical churn hides meaningful diffs and erodes a single consistent code style.                                                             |
| Size warnings                          | `python scripts/validate_codebase.py` reports 108 advisory issues; examples include `canvas/view/config.py:42` (448-line initializer), `canvas/view/interactions.py:19` (296-line key dispatcher), and `core/cad/constraints.py:165` (211-line solver) | These are responsibility seams worth reducing one at a time, not a reason to create a new module for every method.                             |

**Architecture assessment.** The intended pattern is a capability-first modular monolith
with an explicit model/view boundary. No high-severity dependency violation was found:
`platform` is a leaf, `core` is Qt-free, `canvas` does not depend on a feature, and
features do not reach each other's internals (`tests/test_dependency_boundaries.py:45-139`).
The refactor must preserve these rules; it is not a top-level package rename.

## 2. Target Pattern

Keep the established Python `src/` layout and capability-first package tree. PyPA
documents that `src/` separates importable code from project tooling and helps prevent
testing an unintended working-tree copy: [src layout vs flat layout — Python Packaging
User Guide](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/).
Within the Qt-facing side, retain the current separation between pure document/domain
state and presentation/interaction; Qt documents that model/view separates stored data
from its presentation: [Model/View Programming — Qt Widgets](https://doc.qt.io/qt-6.5/model-view-programming.html).

Target rules:

1. Keep exactly the seven current top-level homes; do not revive `engine`, `editor`,
   `domain`, or compatibility facades.
2. Keep a module when it has an independently named responsibility, a distinct dependency
   boundary, or a separate test seam. Do not merge simply because it is short.
3. Merge only feature-private sibling modules that form one cohesive implementation unit;
   do not create generic `helpers` or `common` modules.
4. Split only the confirmed large coordinators into named peer responsibilities. A page,
   view, renderer, or service remains the public coordinator; extracted modules do not
   become backdoors to mutate document state.
5. Preserve: `platform → (none)`, `core → core.document.identity only`,
   `canvas → core/platform/ui`, `features → shared homes`, and `app` as composition root.

## 3. Before/After Tree

```text
# Before
simple_stipple/
  app/  canvas/  core/  features/  platform/  resources/  ui/
  features/convert/{form_base.py,tasks.py,svg_tasks.py,page.py}
  features/pattern/{layout.py,layout_sections.py,page.py,...}
  canvas/{renderer.py,rendering.py,view/{config.py,interactions.py,main.py},...}

# After (only approved phases; all unlisted paths stay in place)
simple_stipple/
  app/  canvas/  core/  features/  platform/  resources/  ui/
  features/convert/{tasks.py,page.py}       # sub-tab implementation in one family module
  features/pattern/{layout.py,page.py,...}  # layout composition in one module
  canvas/{renderer.py,view/{config.py,interactions.py,main.py},...}
```

The target intentionally reduces two files, not dozens. Large modules are split only
where a named responsibility is verified; no package is flattened into a god module.

## 4. Full Path Mapping Table

| Old path                                                                                                       | New path                                                             | Reason                                                                                                                                                                                                                      | Risk   |
| -------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| `src/simple_stipple/features/convert/form_base.py`                                                           | `src/simple_stipple/features/convert/tasks.py`                     | `_ConversionSubTab` is private shared machinery used only by the conversion sub-tabs (`form_base.py:45`, `tasks.py:3`, `svg_tasks.py:19`). One `tasks.py` becomes the canonical home for all conversion sub-tabs. | Medium |
| `src/simple_stipple/features/convert/svg_tasks.py`                                                           | `src/simple_stipple/features/convert/tasks.py`                     | `SvgSubTab` and `SvgToDxfSubTab` are the same conversion-tab responsibility as `FviSubTab` and `FixerSubTab` (`svg_tasks.py:28,258`; `tasks.py:38,257`).                                                        | Medium |
| `src/simple_stipple/features/convert/tasks.py`                                                               | unchanged, expanded                                                  | Retains the feature-private sub-tab API imported by`convert/page.py:35-36`.                                                                                                                                               | Medium |
| `src/simple_stipple/features/pattern/layout_sections.py`                                                     | `src/simple_stipple/features/pattern/layout.py`                    | Each`build_*_section` function is consumed solely by the layout composer (`layout.py:9-37`); one layout module reduces two-hop navigation.                                                                              | Medium |
| `src/simple_stipple/features/pattern/layout.py`                                                              | unchanged, expanded                                                  | Keeps`ZoneListWidget`, left/right composition, properties refresh, and section builders under one feature-private layout responsibility (`layout.py:90-361`).                                                           | Medium |
| `src/simple_stipple/canvas/view/config.py`                                                                   | unchanged; internally split into named initializer functions         | `CanvasView._initialize_view` has 448 lines (`config.py:42`), but its code is bound to the view's lifecycle; function extraction avoids new files and preserves public ownership.                                       | Medium |
| `src/simple_stipple/canvas/view/interactions.py`                                                             | unchanged; dispatch tables/named handlers in same module             | `keyPressEvent` (296 lines) and pointer handlers are a cohesive interaction surface (`interactions.py:19,328,581`); reduce branching without adding files.                                                              | High   |
| `src/simple_stipple/core/cad/constraints.py`                                                                 | unchanged; pure solver helpers only if characterization proves seams | `solve_constraints` and `constraint_residuals` are tightly coupled mathematical behavior (`constraints.py:165,378`); do not preemptively move them.                                                                   | High   |
| All other current runtime modules under`src/simple_stipple/{app,canvas,core,features,platform,resources,ui}` | unchanged                                                            | Each already has one canonical capability home (`tests/test_module_homes.py:10-94`).                                                                                                                                      | Low    |

## 5. Migration Phases

- [X] Phase 1 — **Low-risk hygiene.** Apply Ruff's mechanical import-order fixes and
  re-run static checks. Automation eligible: yes (`ruff check --fix src tests`).
- [X] Phase 2 — **Conversion feature consolidation.** Fold `form_base.py` and
  `svg_tasks.py` into `tasks.py`, update `page.py` imports, delete the two old modules,
  and add focused conversion-tab import/behavior characterization. Automation eligible:
  partial; Qt worker and cancellation behavior require review.
- [X] Phase 3 — **Pattern layout consolidation.** Fold section builders into `layout.py`,
  update the Pattern page and zones imports, delete `layout_sections.py`, and characterize
  Pattern form construction. Automation eligible: partial; widget construction needs
  offscreen UI verification.
- [ ] Phase 4 — **No-new-file view simplification.** Break
  `canvas/view/config.py:_initialize_view` into named setup groups and replace the long
  `keyPressEvent` branch chain with an ordered dispatch table plus named mode handlers.
  Automation eligible: no; preserve key precedence and QWidget lifecycle exactly.
- [ ] Phase 5 — **One large coordinator at a time.** Characterize and then extract the
  next confirmed seam from `canvas/renderer.py`, `features/pattern/page.py`,
  `features/trace/page.py`, or `canvas/operations/editing.py`. Use a named responsibility
  and a focused test; defer ambiguous seams. Automation eligible: no.

## 6. Import/Reference Update Strategy

Before each phase, inventory old paths with `rg` across `src/`, `tests/`, `scripts/`,
`README.md`, `ARCHITECTURE.md`, and `pyproject.toml`. Rewrite static imports, relative
imports, string/dynamic imports, monkeypatch targets, package exports, and structural
tests together. Then assert zero stale-path hits except a deliberately documented guard.

For Phase 2, first inventory `form_base` and `svg_tasks`; existing consumers are
confirmed at `features/convert/page.py:35-36` and `features/convert/tasks.py:3,11,17`.
For Phase 3, inventory `layout_sections`; its current consumer is confirmed at
`features/pattern/layout.py:9-37`. Keep `tests/test_dependency_boundaries.py` and
`tests/test_module_homes.py` aligned with only approved canonical paths.

## 7. Risk & Rollback

Medium risks are altered Qt signal wiring, import-order side effects, widget initialization,
and test monkeypatch paths. High risks are canvas event precedence, rendering cache
invalidation, background-worker cancellation, document undo/redo, and DXF/SVG/FVI round
trips. Keep every phase as one separately reviewable, unstaged commit-sized diff. If a
phase regresses, revert only that phase's files/commit with the user's chosen Git action;
never reset the pre-existing `_to_delete/` changes shown by `git status`.

## 8. Verification Plan

After Phase 1:

```bash
ruff check src tests
ruff format --check .
git diff --check
```

After Phases 2–5:

```bash
ruff check src tests
python scripts/check_circular_imports.py
python scripts/validate_codebase.py
QT_QPA_PLATFORM=offscreen pytest -q tests/test_dependency_boundaries.py tests/test_module_homes.py
QT_QPA_PLATFORM=offscreen pytest -q
git diff --check
```

Additionally run the focused feature suite before the full suite: conversion tests for
Phase 2, Pattern tests for Phase 3, editor-view tests for Phase 4, and a newly added
characterization suite for each Phase 5 seam.

## Changelog

- 2026-08-13 — Phase 1: ran `ruff check --fix src tests`, applying 201 import-order
  fixes across 68 source and test files. `git diff --check` passed. Ruff now reports 30
  non-mechanical baseline errors (complexity, duplicate definitions, and undefined names),
  and `ruff format --check .` identifies 17 pre-existing formatting differences; neither
  category was changed as part of this phase. Existing `_to_delete/` removals were untouched.
- 2026-08-13 — Phase 2: consolidated the private Convert sub-tab family into
  `features/convert/tasks.py`; deleted `form_base.py` and `svg_tasks.py`; updated the
  page and structural coverage; and confirmed zero stale old-path references. Touched-file
  Ruff, compilation, circular-import (164 modules), dependency-boundary (7 tests),
  module-home (3 tests), and diff checks passed. The page-feature test is blocked during
  unrelated canvas-widget import by a missing `SelectTool` export, before Convert classes
  load; full Ruff has 28 unrelated baseline errors. Existing `_to_delete/` removals were
  untouched.
- 2026-08-13 — Phase 3: consolidated Pattern's five named section builders into
  `features/pattern/layout.py`, deleted `layout_sections.py`, and added a structural
  canonical-home guard. AST comparison confirmed each builder body was moved unchanged;
  static searches found zero stale old-path references except the intentional absence
  assertion. Touched-file Ruff, Pattern compilation, circular imports (163 modules),
  dependency/module-home tests (10 tests), and diff checks passed. Focused Pattern page
  tests remain blocked before collection by the unrelated missing `SelectTool` canvas
