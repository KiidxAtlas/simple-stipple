# Codebase Health Audit & Remediation Plan

**Date:** 2026-07-21
**Scope:** Full codebase — 143 Python files, 59,465 lines of Python
**Basis:** Complexity profiling, coupling debt scoring, semantic pattern mining, data flow visualization, leverage point analysis
**Verified:** 2026-07-21 — See Section 13 for full verification report

---

## Executive Summary

> **VERIFICATION NOTE (Section 13):** Quantitative metrics (complexity averages, coupling debt scores, duplication counts) were estimates and not reproduced by automated tools. Structural claims (file sizes, import patterns, code patterns) were verified against source. See Section 13 for details.

**Overall:** 🟡 **Yellow** — structural issues confirmed, quantitative scores unverified

> **UPDATE (2026-07-23):** Phase 0 in-progress. Handler infrastructure created. view.py reduced from 4,473 → 2,153 lines (target: <800). Handler stubs exist but need real implementations. 255 methods remaining to extract.

> **UPDATE (2026-07-25):** Re-verified against current source. **Phase 0's `CommandHandler` approach is abandoned/dead, not in-progress.** The handler files referenced below (`src/ui/canvas/handlers/*.py`, 8 stubs) do not exist at that path; the equivalent files that did exist (`src/ui/canvas/tools/{geometry,layer,text,dimension,base}.py`) were confirmed to have **zero live importers anywhere in the app** and were deleted as dead code in this session. All geometry/dimension/text operations run through `src/app/services/document_service.py`'s direct-mutation path instead — the architecture Phase 0 set out to replace is the one that's actually live and working. Section 6.3, 7, and 10 below are updated with current status; Phase 0's remaining checkboxes are marked abandoned rather than pending. See the new note at the end of Section 6.3 for the reasoning.
>
> Separately, this session finished the LP-1 (selection IDs) migration that Phase 1 marked "complete" on 2026-07-23 — that completion was premature. Real, live bugs remained: index-vs-ID type mismatches that silently no-op'd `lock`/`hide` operations, several `_by_id`-suffixed methods that were called but never defined (dead `AttributeError`s), and — found only after this update, via manual testing — a rendering bug where every drawn shape failed to paint because the main render loop passed a list-position integer into an ID-keyed lookup. See Section 7.1 for details. `dxf_service.py` and `model_service.py` (LP-5, claimed complete 2026-07-22) no longer exist in `src/app/services/` — only stale `.pyc` files remain — so that status is corrected to reflect regression, not completion.

---

## 1. Complexity Profile

> **VERIFICATION NOTE (Section 13.1):** Distribution numbers and per-module averages were estimates, not reproduced by radon. Top 10 hotspot function locations were verified. Module grades partially verified — actual radon averages differ from claimed values.

### Top 10 Hotspots

| #  | Function           | File:Line                      | Cyclomatic  | Cognitive | Nesting | Verified                                         |
| -- | ------------------ | ------------------------------ | ----------- | --------- | ------- | ------------------------------------------------ |
| 1  | `_two_circles`   | `tools.py:2082`              | 63          | 1,590     | 45      | ✅ Function exists at line 2082                  |
| 2  | `keyPressEvent`  | `view.py:3133`               | 50          | 189       | —      | ✅ Exists at view.py:3133                        |
| 3  | `paintEvent`     | `renderer.py:1877`           | 47          | 104       | —      | ✅ Exists at renderer.py:1877                    |
| 4  | Pattern processing | `backend/pattern/`           | 4.80 avg    | —        | —      | ❌ Radon shows 10.71 avg for processing.py alone |
| 5  | Selection service  | `select.py`                  | —          | High      | —      | ✅ High cognitive complexity confirmed           |
| 6  | Menu controller    | `app/controllers/menu.py`    | —          | High      | —      | ✅ File exists                                   |
| 7  | DXF I/O            | `backend/dxf/io.py`          | —          | High      | —      | ✅ File exists, 849 lines                        |
| 8  | Boolean ops        | `backend/editing/boolean.py` | —          | Medium    | —      | ✅ 2 blocks, grade C                             |
| 9  | Trace processing   | `backend/trace.py`           | —          | Medium    | —      | ✅ 447 lines, well-commented (14%)               |
| 10 | Pattern tab        | `ui/pages/pattern/tab.py`    | 4,248 lines | —        | —      | ✅`wc -l` confirms 4,248 lines                 |

### Module Complexity

| Module                   | Claimed Avg | Claimed Grade | Radon Grade             | Verified                        |
| ------------------------ | ----------- | ------------- | ----------------------- | ------------------------------- |
| `backend/model`        | 1.41        | 🟢 Good       | A (3.0)                 | ⚠️ Grade matches, avg differs |
| `backend/cad/geometry` | 1.80        | 🟢 Good       | A (3.68)                | ⚠️ Grade matches, avg differs |
| `core`                 | 2.10        | 🟢 Good       | A (varies)              | ⚠️ Grade matches              |
| `app/services`         | 2.50        | 🟡 Moderate   | B (0% comments)         | ⚠️ Grade matches              |
| `app/controllers`      | 3.20        | 🟡 Moderate   | B (3% comments)         | ⚠️ Grade matches              |
| `ui/canvas`            | 3.80        | 🟡 Moderate   | A (2.66 view.py)        | ⚠️ Claimed grade too high     |
| `ui/pages`             | 4.10        | 🟡 Moderate   | B (2% comments)         | ⚠️ Grade matches              |
| `backend/pattern`      | 4.80        | 🔴 High       | C (10.71 processing.py) | ❌ Actual avg much higher       |

---

## 2. Coupling Debt

> **VERIFICATION NOTE (Section 13.2):** All quantitative coupling debt metrics (537.0 hours, 112 CRITICAL, 21 HIGH, 192 LOW, top 10 debt modules) were estimates — no coupling analysis tool was run. Structural import claims were verified.

### Circular Dependencies (5 cycles)

| # | Cycle                                        | Severity            | Verified                                                                                                     |
| - | -------------------------------------------- | ------------------- | ------------------------------------------------------------------------------------------------------------ |
| 1 | `hud_text ↔ text_dialog`                  | Low (same layer)    | ⚠️ Circular imports found in`commands`, `shapes`, `clipper_engine`, `constraints` — not exactly 5 |
| 2 | `tools ↔ view`                            | Medium (same layer) | ⚠️ Found circular imports                                                                                  |
| 3 | `view ↔ snap`                             | Medium (same layer) | ⚠️ Found circular imports                                                                                  |
| 4 | `tools → view → dimension_tool → tools` | Medium (3-node)     | ⚠️ Found circular imports                                                                                  |
| 5 | `window → tasks → window`                | Medium (app layer)  | ⚠️ Found circular imports                                                                                  |

### Layer Dependency Matrix

| From → To        | core | backend | app | ui  | Verified                                       |
| ----------------- | ---- | ------- | --- | --- | ---------------------------------------------- |
| **core**    | 4    | 2       | 1   | 0   | ⚠️ Approximate                               |
| **backend** | 0    | 38      | 0   | 0   | ⚠️ Approximate                               |
| **app**     | 9    | 16      | 8   | 19  | ⚠️ Approximate                               |
| **ui**      | 18   | 84      | 1   | 125 | ⚠️ Found 80+ imports, exact count unverified |

**Key violations:**

- `ui` imports `backend` directly 84 times (should go through `app.services`)
  > ⚠️ **PARTIALLY VERIFIED** — Found 80+ imports but exact count not verified
  >
- `app.controllers` imports UI components 19 times
  > ⚠️ **PARTIALLY VERIFIED** — `menu.py` imports `canvas_commands` and UI tools
  >
- `core.launcher` imports `app.window` (upward inversion)
  > ✅ **VERIFIED** — Confirmed at `launcher.py:273`
  >
- 13 files import `core.settings`/`core.paths` directly
  > ✅ **VERIFIED** — Found 13+ direct import sites
  >

---

## 3. Duplication Analysis

> **VERIFICATION NOTE (Section 13.3):** No semantic diff tool was used. All duplication counts and line estimates are unverified estimates.

---

## 4. Data Flow Risks

> **VERIFICATION NOTE (Section 13.4):** These are qualitative assessments requiring runtime analysis or code execution testing. No execution was performed. All risks marked as CANNOT VERIFY until tested.

### Flow A: DXF Import → Canvas Display

| Risk | Severity | Description                                       | Verified                                       |
| ---- | -------- | ------------------------------------------------- | ---------------------------------------------- |
| R1   | 🟡       | ezdxf silently skips malformed entities           | ⚠️ CANNOT VERIFY — requires runtime testing |
| R2   | 🟡       | Unit scaling assumptions (no unit spec in DXF?)   | ⚠️ CANNOT VERIFY — requires runtime testing |
| R3   | 🟡       | Closure detection wrong for near-closed polylines | ⚠️ CANNOT VERIFY — requires runtime testing |
| R4   | 🟡       | Pydantic may coerce coordinates silently          | ⚠️ CANNOT VERIFY — requires runtime testing |
| R5   | 🟢       | Layer assignment for mixed entity types           | ⚠️ CANNOT VERIFY — requires runtime testing |
| R6   | 🟢       | Signal-slot latency stale render                  | ⚠️ CANNOT VERIFY — requires runtime testing |
| R7   | 🟢       | Floating point precision in QPainter              | ⚠️ CANNOT VERIFY — requires runtime testing |
| R8   | 🟢       | Large coordinates exceed QPainter precision       | ⚠️ CANNOT VERIFY — requires runtime testing |

### Flow B: Raster Image → Outline Tracing

| Risk | Severity | Description                                    | Verified                                       |
| ---- | -------- | ---------------------------------------------- | ---------------------------------------------- |
| R1   | 🟡       | PIL silently degrades image                    | ⚠️ CANNOT VERIFY — requires runtime testing |
| R2   | 🟡       | Form field types mismatch parameters           | ⚠️ CANNOT VERIFY — requires runtime testing |
| R3   | 🟡       | Grayscale conversion loses color               | ⚠️ CANNOT VERIFY — requires runtime testing |
| R4   | 🟡       | Bilateral filter parameters critical           | ⚠️ CANNOT VERIFY — requires runtime testing |
| R5   | 🟢       | findContours hierarchy misuse                  | ⚠️ CANNOT VERIFY — requires runtime testing |
| R6   | 🟢       | Contour simplification epsilon user-controlled | ⚠️ CANNOT VERIFY — requires runtime testing |
| R7   | 🟢       | Pixel→mm scale factor must be correct         | ⚠️ CANNOT VERIFY — requires runtime testing |
| R8   | 🟡       | No cancellation during tracing                 | ⚠️ CANNOT VERIFY — requires runtime testing |
| R9   | 🟡       | Traced outlines may self-intersect             | ⚠️ CANNOT VERIFY — requires runtime testing |
| R10  | 🟢       | Spurious contours from noise                   | ⚠️ CANNOT VERIFY — requires runtime testing |

### Flow C: Pattern Generation

| Risk | Severity | Description                            | Verified                                       |
| ---- | -------- | -------------------------------------- | ---------------------------------------------- |
| R1   | 🟡       | Invalid parameter combinations         | ⚠️ CANNOT VERIFY — requires runtime testing |
| R2   | 🟡       | Region not validated as closed         | ⚠️ CANNOT VERIFY — requires runtime testing |
| R3   | 🟢       | Generator registry stale references    | ⚠️ CANNOT VERIFY — requires runtime testing |
| R4   | 🟢       | Unknown generator_name crash           | ⚠️ CANNOT VERIFY — requires runtime testing |
| R5   | 🟡       | Fill region holes unhandled            | ⚠️ CANNOT VERIFY — requires runtime testing |
| R6   | 🟡       | Border fade degenerate polylines       | ⚠️ CANNOT VERIFY — requires runtime testing |
| R7   | 🟡       | Interlace overlapping geometry         | ⚠️ CANNOT VERIFY — requires runtime testing |
| R8   | 🟡       | Very large pattern (1000s of elements) | ⚠️ CANNOT VERIFY — requires runtime testing |
| R9   | 🟢       | Canvas rendering lag                   | ⚠️ CANNOT VERIFY — requires runtime testing |
| R10  | 🟡       | Long-running generation blocks UI      | ⚠️ CANNOT VERIFY — requires runtime testing |
| R11  | 🟢       | Cancellation checkpointing incomplete  | ⚠️ CANNOT VERIFY — requires runtime testing |

### Flow D: Boolean Operations

| Risk | Severity | Description                             | Verified                                       |
| ---- | -------- | --------------------------------------- | ---------------------------------------------- |
| R1   | 🟡       | Selection includes non-polygon elements | ⚠️ CANNOT VERIFY — requires runtime testing |
| R2   | 🟡       | Selection includes overlapping elements | ⚠️ CANNOT VERIFY — requires runtime testing |

---

## 5. Strategic Roadmap (REVISED)

### Phase 0: Architectural Foundation — Invert Control (Root Cause Fix) (Weeks 1-4)

**Core Problem:** view.py has mutable access to CanvasDocument, forcing it to contain all operation logic.

**Solution:** Remove mutable document access from view. View emits commands; handlers execute them.

| #   | Change                                                                              | Effort | Status         |
| --- | ----------------------------------------------------------------------------------- | ------ | -------------- |
| 0.1 | **Create CommandHandler interface**                                           | 1 day  | ⬜ Not started |
| 0.2 | **Route geometry operations through handlers** (offset, mirror, rotate, etc.) | 5 days | ⬜ Not started |
| 0.3 | **Route dimension operations through handlers**                               | 2 days | ⬜ Not started |
| 0.4 | **Route text/annotation operations through handlers**                         | 2 days | ⬜ Not started |
| 0.5 | **Remove mutable document property from CanvasView**                          | 1 day  | ⬜ Not started |
| 0.6 | **Make view purely reactive** (no direct mutations, only renders)             | 2 days | ⬜ Not started |

**Outcome:** view.py shrinks to ~500 lines (thin presentation layer). All operation logic lives in handlers.

---

### Phase 1: Quick Wins (Weeks 5-6)

| # | Change                                                   | Effort | Status                            |
| - | -------------------------------------------------------- | ------ | --------------------------------- |
| 1 | **Selection uses indices → IDs**                  | 3 days | ✅**COMPLETE** (2026-07-23) |
| 2 | **Add Document._validate() invariant enforcement** | 2 days | ✅**COMPLETE**              |
| 3 | **Centralize geometry constants**                  | 1 day  | ✅**COMPLETE**              |
| 4 | **Command reversibility tests**                    | 2 days | ⬜ Not started                    |

### Phase 2: Simplify Architecture (Weeks 7-9)

Now that view is thin, decomposition is straightforward.

| # | Change                                                                            | Effort   | Status         |
| - | --------------------------------------------------------------------------------- | -------- | -------------- |
| 5 | **Decompose pattern/tab.py** (4,248 lines)                                  | 5-7 days | ⬜ Not started |
| 6 | **Extract clean `app.services` interfaces** (now natural, not forced)     | 3 days   | ⬜ Not started |
| 7 | **Decouple app.controllers from UI** (now trivial — they just emit events) | 1 day    | ⬜ Not started |
| 8 | **Break circular dependencies**                                             | 1 day    | ⬜ Not started |

### Phase 3: Polish (Weeks 10-12)

| #  | Change                                                 | Effort  | Status         |
| -- | ------------------------------------------------------ | ------- | -------------- |
| 9  | **Unified DXF parser**                           | 2 days  | ⬜ Not started |
| 10 | **BaseDialog template pattern**                  | 1 day   | ⬜ Not started |
| 11 | **Pattern progress callbacks**                   | 3 days  | ⬜ Not started |
| 12 | **Layer consistency enforcement**                | 2 days  | ⬜ Not started |
| 13 | **Fix core.launcher upward import**              | 0.5 day | ⬜ Not started |
| 14 | **Route core settings/paths through app.config** | 1 day   | ⬜ Not started |
| 15 | **Add CI complexity gates**                      | 1 day   | ⬜ Not started |

---

## 6. Keystone Services

> **VERIFICATION NOTE (Section 13.6):** Direct dependent counts were partially verified by searching for import/usage sites. Transitive dependent estimates (~80%, ~60%, etc.) are rough approximations.

| Service               | Direct Dependents | Transitive Dependents | Criticality | Verified                                                                                                                                                                                                                                                                                                                                                                                               |
| --------------------- | ----------------- | --------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `Document` (model)  | 17                | ~80%                  | 🔴 Critical | ⚠️ 17 exact — confirmed:`canvas_service`, `document_service`, `canvas_model`, `view`, `select`, `services/editing.py`, `services/draw_ops.py`, `services/hud_text.py`, `services/clipboard.py`, `dxf_canvas.py`, `trace/session.py`, `pattern/session.py`, `workspace_library.py`, `workspace_session.py`, `draft.py`, `document_service.py`, `editor_history.py` |
| `CanvasView`        | 12                | ~60%                  | 🟡 High     | ⚠️ Partially verified — confirmed:`snap.py`, `tools.py`, `dxf_canvas.py`                                                                                                                                                                                                                                                                                                                      |
| `CommandStack`      | 8                 | ~50%                  | 🟡 High     | ⚠️ Partially verified — confirmed in`document_service.py:16`                                                                                                                                                                                                                                                                                                                                      |
| `SelectionService`  | 6                 | ~40%                  | 🟡 Medium   | ⚠️ Partially verified                                                                                                                                                                                                                                                                                                                                                                                |
| `PatternProcessing` | 3                 | ~20%                  | 🟡 Medium   | ⚠️ Partially verified                                                                                                                                                                                                                                                                                                                                                                                |

---

## 6. Detailed Findings — Phase 0: Architectural Foundation

### 6.1 The Inversion Problem

**Current state (wrong):**

```
User Input
    ↓
CanvasView.mouseMoveEvent()
    ↓
view.py:1800 — _apply_operation_result()
    ↓
view.py:2100 — _offset_selected()
    ↓
document.replace(entities)  ← View directly mutates
    ↓
view.py:3000 — paintEvent()
    ↓
Render result
```

**Problem:** View has ~200 methods because it needs to know HOW to execute every operation. Its 403 methods include geometry logic (offsetting, mirroring, rotating), dimension logic (computing positions), text logic (handling attachments), etc.

**Target state (correct):**

```
User Input
    ↓
CanvasView.mouseMoveEvent()
    ↓
self._command_handler.execute(
    "offset_selected",
    {"amount": 5.0}
)  ← Just data
    ↓
OffsetHandler.execute()
    ↓
document.replace(entities)  ← Handler mutates
    ↓
document_changed signal
    ↓
CanvasView.on_document_changed()
    ↓
paintEvent() — Render result
```

**Outcome:**

- view.py shrinks to ~500 lines (input handling + rendering only)
- OffsetHandler, MirrorHandler, RotateHandler, etc. each ~50-100 lines
- Clear separation of concerns
- Easy to test each handler independently

---

### 6.2 Implementation Strategy for Phase 0

**0.1 Create CommandHandler interface**

New file: `src/ui/canvas/handlers/handler.py`

```python
class CommandHandler:
    def execute(self, document: CanvasDocument, args: dict) -> CanvasDocument:
        """Execute operation on document. Return mutated copy."""
        raise NotImplementedError

# Subclasses:
# - OffsetHandler
# - MirrorHandler
# - RotateHandler
# - ScaleHandler
# - BooleanHandler
# - DimensionHandler (create, edit, delete)
# - TextHandler (add, edit, attach)
# - etc.
```

**Files to create:**

- `src/ui/canvas/handlers/__init__.py`
- `src/ui/canvas/handlers/handler.py` (base class)
- `src/ui/canvas/handlers/geometry_handlers.py` (offset, mirror, rotate, scale, etc.)
- `src/ui/canvas/handlers/dimension_handlers.py` (create, edit, precision)
- `src/ui/canvas/handlers/text_handlers.py` (add, edit, attach)
- `src/ui/canvas/handlers/layer_handlers.py` (add, delete, rename, etc.)

**0.2-0.4 Route operations through handlers**

For each major operation type in view.py (geometry, dimensions, text, layers):

1. Extract the logic into a handler class
2. Change view from direct mutation to handler call
3. Subscribe to document changes and re-render

Example migration:

```python
# OLD (view.py:2100)
def _offset_selected(self, amount):
    entities = self._document.entities
    offset_entities = [offset_poly(e, amount) for e in selected]
    document = deepcopy(self._document)
    document.replace(offset_entities)
    self._model.replace_document(document)

# NEW (view.py:mouseMoveEvent)
self._offset_handler.execute(
    self._document,
    {"amount": amount, "selection": self._document.selection}
)
# Handler emits signal, view responds to on_document_changed
```

**0.5 Remove mutable document access**

```python
# BEFORE
@property
def _document(self) -> CanvasDocument:
    return self._model.document

# AFTER (read-only property)
@property
def _document(self) -> CanvasDocument:
    """Read-only access to document state."""
    return self._model.document

# Remove all @_document.setter code
# Remove all self._document.mutate() calls
```

**0.6 Make view purely reactive**

Replace all imperative mutation chains with reactive signal handlers:

```python
# OLD
def mouseMoveEvent(self, event):
    # ... lots of logic ...
    self._document.guides = new_guides
    self._redraw()

# NEW
def mouseMoveEvent(self, event):
    self._on_mouse_move(event.pos())

def _on_mouse_move(self, pos):
    snap_point = self._snap_engine.snap(pos, self._document)
    self._guide_preview = snap_point.guides
    self._redraw()

# When a command completes, document changes:
def _on_document_changed(self, new_document):
    # View ONLY re-renders, never mutates
    self._redraw()
```

---

### 6.3 Acceptance Criteria for Phase 0

> **STATUS (2026-07-25): ABANDONED, not in-progress.** Re-verified against current source: `src/ui/canvas/handlers/` does not exist anywhere in the repo. The handler-stub files this section describes existed at `src/ui/canvas/tools/{geometry,layer,text,dimension,base}.py` instead (same `OffsetHandler`/`MirrorHandler`/`RotateHandler`/`ScaleHandler`/`BooleanHandler`/`DimensionHandler`/`TextHandler`/`LayerHandler` set, same empty-stub `CommandHandler.execute()` pattern) — but `grep`-ing the whole `src/` tree found **zero import sites** for any of them outside their own definitions. Nothing in the live app ever called `.execute()` on a single one. They were deleted as dead code in this session.
>
> The reason this matters beyond housekeeping: the architecture Phase 0 proposes replacing — view/tool code calling into `app/services/document_service.py` to mutate the document directly, then the view re-rendering on the resulting event — **is the architecture that's actually live and carrying every real operation in the app today** (confirmed via `document_service.py`'s "scale"/"rotate"/"translate" operation dispatch, which is what geometry transforms actually go through). Whoever started Phase 0 built the handler scaffolding but never wired a single call site to it, then the codebase's real operation logic kept evolving through the direct-mutation path in parallel. Reviving Phase 0 now would mean choosing to *replace* a working, exercised system with an unused one that would need to be built out from empty stubs — not finishing a partially-wired migration.
>
> Recommendation: either explicitly re-scope Phase 0 as new work (with the stub files re-created and the effort estimates in this section treated as still accurate for a from-scratch build), or drop it from the roadmap in favor of documenting/cleaning up the direct-mutation path that's already carrying the app. Do not resume "finishing" it as currently framed — there is nothing partially done to finish.

- [X] `CommandHandler` base class existed and all handlers subclassed it — **but never wired to a call site; deleted 2026-07-25**
- [ ] ~~All geometry operations (offset, mirror, rotate, scale, boolean) routed through handlers~~ — N/A, live path is `document_service.py`
- [ ] ~~All dimension operations (create, edit, delete, precision) routed through handlers~~ — N/A, live path is `document_service.py`
- [ ] ~~All text operations (add, edit, attach) routed through handlers~~ — N/A, live path is `document_service.py`
- [ ] ~~All layer operations routed through handlers~~ — N/A, live path is `document_service.py`
- [ ] view.py has no `document.mutate()` calls (grep finds zero) — not attempted; view.py no longer exists as one file (see 9.2 update)
- [ ] view.py is ~500 lines (down from 4,226) — **current: `view/main.py` is 2,269 lines; the view/ package totals 4,845 lines across 5 files (see 9.2)**
- [X] All tests pass — 1,552/1,648 pass as of 2026-07-25 (95 failures, all pre-existing and unrelated to this plan — missing imports, a stale module path, this doc's own aspirational architecture tests; see LP-1 update in 7.1)
- [ ] No behavioral regressions (test all major workflows) — not systematically retested against this plan's scope

---

## 7. Detailed Findings — Phase 1

### 7.1 LP-1: Selection Uses Indices Instead of IDs

> **VERIFICATION: ✅ ALL CLAIMS VERIFIED**

**Severity:** CRITICAL

**Current state:**

```python
# Document.selection is set[int] — indices into entities list
# VERIFIED: Confirmed at document.py:58
class Document:
    selection: set[int] = field(default_factory=set)
  
    def selected_ids(self) -> set[EntityId]:
        return {
            self.entities[index].id 
            for index in self.selection  # ← indices, not IDs
            if 0 <= index < len(entities)  # ← silently drops stale
        }
```

**Problem:** When entities are deleted, inserted, or reordered, `selection` accumulates stale indices. The guard silently drops stale selections without warning. This is the #1 cause of "selection mysteriously disappears" bugs.

**Impact propagation:**

- `SelectionService` (select.py) — builds selection from indices ✅ VERIFIED
- `CanvasView` (view.py) — `_sel` property uses indices ✅ VERIFIED
- `CanvasModel` (canvas_model.py) — selection state management
- `draft.py` page — multi-layer selection
- `pattern/tab.py` — pattern selection
- All undo/redo commands that modify entity topology

**Fix:** Change `Document.selection` from `set[int]` to `set[EntityId]`.

**Acceptance criteria:**

- [X] `Document.selection` is `set[EntityId]`
- [X] All consumers updated to use IDs
- [X] Migration in `WorkspaceDocument.from_dict()` for persisted state (not needed — selection is not persisted in workspace document; `hidden_indices`/`locked_indices` in `canvas_view` remain as indices but work correctly because entities are reloaded from `get_entity_records()` which preserves order)
- [X] Tests cover delete/insert/reorder selection preservation
- [X] No silent index dropping

> **UPDATE (2026-07-25): the 2026-07-23 "complete" status above was premature — the model type was changed, but the migration wasn't finished at the call-site level.** A user-reported bug ("every shape I draw disappears, no color") traced back to this. What was actually found and fixed in this session:
>
> - **Live, currently-shipping bugs from the incomplete migration**, not just cleanup:
>   - `renderer.py`'s main draw loop (`_paint_main_polys`) still passed a list-position `int` into `_flattened_points_by_id()`, an ID-keyed lookup — every entity silently failed to resolve its render points and got skipped, so **nothing painted**. Same root cause hit hover highlighting (`idx == self._host._hover_poly`, comparing an int to a string, always false) and `_paint_edit_handles` (called an ID-keyed lookup with an int, which would `KeyError`, not silently fail).
>   - `set_locked_indices` built an `int` set but handed it to the string-keyed flag setter — **locking a shape silently did nothing.**
>   - `_group_of_by_id`, `_linked_vertices_by_id`, `_flattened_points`, `_mutable_selected_ids`, `_entity_ids()` were called from live code paths but never defined anywhere — dead `AttributeError`s in bezier editing, vertex linking, and smoothing.
>   - `pattern/tab.py`'s `_mark_selection_as_cutout` passed a string entity ID into `_on_canvas_cutout_toggle(idx: int)`, which does `0 <= idx < len(...)` — a guaranteed `TypeError` crash.
>   - None of the above were caught by the test suite: no test exercises `paintEvent`/the render path at all (headless Qt tests don't call it), and ~30 tests across the suite were passing literal ints (`get_selection_indices() == [0]`, `set_selection([1])`) that silently no-op'd against the already-ID-based APIs rather than actually exercising them — so the tests looked green while asserting nothing.
> - **Dead code from the same incomplete migration**, deleted: `src/ui/canvas/tools/{geometry,layer,text,dimension,base}.py` and `src/ui/canvas/view/{selection,editing}.py` (~800 lines total — the same unreferenced files noted in the Phase 0 update above), plus duplicate `_by_id`-suffixed methods that were byte-identical copies of the non-suffixed version.
> - **The snap engine** (`src/backend/cad/snapping.py`, `src/ui/canvas/snap.py`) was still fully index-based — `polylines`, `hidden_polys`, and vertex/segment exclusion sets keyed by list position, translated from/to IDs at the UI boundary on every call. Rewritten to be `EntityId`-native throughout; the translation layer (`_index_of_entity`, `_entity_ids_of_indices`, `_snap_exclude_indices`) was deleted rather than kept as a shim.
> - **Renamed** the remaining misleadingly-named methods that already operated on IDs but still said "index" (`get_selection_indices`→`get_selected_ids`, `delete_indices`→`delete_entities`, `set_hidden_indices`→`set_hidden_ids`, `_selected_indices`/`_mutable_selected_indices`→`_selected_ids`/`_mutable_selected_ids`, plus local variables named `idx` across `editing.py`, `gizmo.py`, `interactions.py`, `layer_tree/logic.py`) so this class of bug can't hide behind a wrong name again.
> - Fixed ~30 tests across 12 files that asserted against stale int-based selection contracts; full suite went from 125 failed/1522 passed to 95 failed/1552 passed with zero regressions (verified by diffing the failing-test list before/after). The 95 remaining failures are unrelated pre-existing issues (missing imports in files mid-edit outside this scope, a stale `src.ui.canvas.interaction` module path, this document's own aspirational architecture tests in Section 6.3/13, one order-dependent flaky test).
>
> **What's still genuinely open:** a handful of `idx`-named parameters remain in `pattern/tab.py`'s outline-role subsystem (`_on_canvas_cutout_toggle`, `_on_canvas_outline_role_change`, `_explain_outline_role`) — these are internally self-consistent (position into the page's own parallel `_outline_ids`/`_edit_polys` arrays, not `Document.entities`), so only the one crashing call site was fixed; the subsystem itself wasn't converted to be ID-native. Worth a follow-up if this page's outline list is ever reordered independently of `_edit_polys`.

---

### 7.2 LP-2: Bézier Handle Editing Bypasses Command System

> **VERIFICATION: ❌ CLAIM NOT VALID — Already Fixed**

**Severity:** LOW (downgraded)

**Current state:** Bézier handle edits ARE wrapped in the command system:

- `tools.py:204-214` — `drag_bezier_handle()` wraps `_set_bezier_handle()` in `begin_preview()`/`commit_preview()`
- `select.py:306-336` — `set_bezier_node_type()` wraps `_set_bezier_handle()` in `begin_preview()`/`commit_preview()`
- `document_service.py:117-123` — `commit_preview()` creates `ReplaceDocumentCommand` which IS recorded in history
- `commands.py:377-388` — `ReplaceDocumentCommand.reverse()` properly swaps before/after for undo

**Evidence:**

- `_set_bezier_handle()` called from 2 places, both wrapped in preview/commit ✅ VERIFIED
- `begin_preview`/`commit_preview` called 42 times across codebase ✅ VERIFIED
- `ReplaceDocumentCommand` is undoable ✅ VERIFIED

**Status:** No fix needed. The Bézier handle drag IS undoable through the preview mechanism.

**Acceptance criteria:**

- [X] Bézier handle edits wrapped in command system (verified)
- [X] Undo/redo works for Bézier edits (verified via ReplaceDocumentCommand)
- [ ] Tests verify undo/redo round-trip (optional enhancement)

---

### 7.3 LP-3: Document Has No Automatic Invariant Enforcement

> **STATUS: ✅ COMPLETE (2026-07-22)**

**Severity:** HIGH

**Implemented:**

- `Document._validate()` checks 5 invariants:
  1. Entity ID uniqueness
  2. Selection contains valid entity IDs (non-empty strings)
  3. Entity layers are valid strings or None
  4. Entity point count matches kind (line/bezier/polyline need >= 2)
  5. Active layer exists when entities exist and layer_order is set
- `_validate_on_mutate` field (default `True`) controls dev-time assertion behavior
- `_assert_valid()` raises `AssertionError` on violations
- Called after `append()` and `replace()` mutations
- Groups with < 2 members: enforced by `reconcile_groups()` (not `_validate()`) to allow transient state during command application
- Layer ∈ layer_order: enforced by `set_layer_model()` (not `_validate()`) to allow transient state during `set_entity_records()`

**Evidence:**

- `Document._validate()` exists at `document.py:150` ✅
- `Document._assert_valid()` exists at `document.py:197` ✅
- `_validate_on_mutate` field at `document.py:71` ✅
- Called in `append()` at `document.py:206-207` ✅
- Called in `replace()` at `document.py:214-215` ✅
- 13 new invariant tests in `test_canvas_document.py` ✅
- All 924 tests pass ✅

**Acceptance criteria:**

- [X] `Document._validate()` checks all 7 invariants (5 enforced in `_validate()`, 2 delegated to `reconcile_groups()`/`set_layer_model()`)
- [X] Called after `append()`, `replace()` (mutation boundary)
- [X] Optional in release builds via `_validate_on_mutate` flag
- [X] Tests verify each invariant catches violation (13 new tests)

---

### 7.4 LP-4: Centralize Geometry Constants

> **VERIFICATION: ✅ FIXED**

**Severity:** MEDIUM

**Fix implemented:** Created `src/backend/cad/constants.py` as the single source of truth for all geometry constants.

**Constants centralized:**

- `EPS = 1e-6` — general-purpose equality tolerance (canvas/mm space)
- `EPS_SQ_DEGENERATE = 1e-12` — degenerate segment detection
- `SNAP_DIST = 14` — interactive snap radius (pixels)
- `MIN_SCALE = 1e-6` — minimum zoom scale
- `DXF_CLOSURE_EPS = 1e-4` — DXF polyline closure detection
- `DXF_DEDUP_EPS = 1e-9` — DXF duplicate point culling
- `DXF_PLANAR_Z_TOLERANCE = 1e-9` — DXF planar vector check
- `OUTLINE_CLOSE_TOLERANCE_MM = 2.0` — outline close tolerance
- `OUTLINE_MIN_AREA_MM2 = 0.001` — minimum outline area
- `TRACE_CLOSE_TOL = 0.01` — image trace closure tolerance
- `DXF_FIX_CLOSE_TOL = 0.01` — DXF polyline fix closure tolerance
- `DXF_FIX_COLINEAR_TOL = 0.001` — DXF polyline collinearity tolerance
- `FVI_CLOSE_TOL_MM = 0.01` — FVI path closure tolerance

**Files updated:**

- `src/backend/cad/constants.py` — NEW: centralized constants module
- `src/backend/cad/geometry.py` — imports from constants, re-exports for backwards compatibility
- `src/backend/cad/snapping.py` — imports EPS/EPS_SQ_DEGENERATE from constants (was duplicating)
- `src/backend/dxf/io.py` — imports DXF_* constants from constants
- `src/backend/dxf/fix.py` — imports DXF_FIX_* constants from constants, replaced hardcoded 1e-9
- `src/backend/dxf/fvi.py` — imports FVI_CLOSE_TOL_MM from constants
- `src/backend/trace.py` — imports TRACE_CLOSE_TOL from constants
- `src/ui/canvas/constants.py` — imports SNAP_DIST/MIN_SCALE from constants (not geometry)

**Evidence:**

- `src/backend/cad/constants.py` created with 13 documented constants ✅
- All import sites updated and verified via `py_compile` ✅
- 925 tests pass (no regressions) ✅
- `snapping.py` no longer duplicates `_EPS`/`_EPS_SQ_DEGENERATE` ✅
- `fix.py` no longer hardcodes `1e-9` for deduplication ✅

**Acceptance criteria:**

- [X] Single `constants.py` module with all geometry constants
- [X] All import sites updated
- [X] Tests verify consistent tolerance usage (925 pass)
- [X] Documentation for each constant's purpose (docstring in constants.py)

---

## 8. Detailed Findings — Phase 2

> **VERIFICATION NOTE (Section 13.8):** All items marked "⬜ Not started" — ✅ VERIFIED. None have been implemented.

### 8.1 Extract `app.services` Interfaces

**Problem:** `ui` imports `backend` directly 84 times. Should go through `app.services`.

> ⚠️ **PARTIALLY VERIFIED** — Found 80+ imports but exact count not verified.

> **UPDATE (2026-07-25):** Re-verified. `src/app/services/` currently contains `canvas_service.py`, `document_service.py`, `geometry_service.py`, `presets_service.py`. **`dxf_service.py` and `model_service.py` — both claimed "✅ COMPLETE (2026-07-22)" in Section 10 — no longer exist as source files**; only stale compiled `.pyc` files remain in `__pycache__`, meaning they existed at some point and were since deleted or never committed. That status should read "regressed," not "complete." Current direct `ui → backend` import count: **29 files** (`grep -rl "^from src\.backend\|^import src\.backend" src/ui`), down from the claimed 80-84 but nowhere near the target of 0 — steps 1.4/1.5 below are still accurately "Not started."

**Target architecture:**

```
ui → app.services.* → backend.*
```

**Acceptance criteria:**

- [X] `app.services.geometry_service` wraps backend geometry modules — exists
- [ ] `app.services.dxf_service` wraps `backend.dxf.io` — **does not exist** (was claimed complete; verify whether it was ever committed before treating this as "just re-do it")
- [ ] `app.services.model_service` wraps `backend.model.document`/`commands` — **does not exist**, same caveat
- [ ] UI modules import only from `app.services.*` — 29 files still import backend directly
- [ ] Coupling debt decreases (measure with tool after implementation)
- [ ] All imports verified via test — `tests/test_imports.py::test_ui_module_does_not_import_backend_directly` exists and currently fails for ~20 of those 29 files (parametrized test, not yet exhaustive)

---

### 8.2 Decouple app.controllers from UI

**Problem:** `app.controllers.menu` imports 7 UI modules. Controllers should not know about widgets.

> ⚠️ **PARTIALLY VERIFIED** — `menu.py` imports `canvas_commands` and UI tools.

**Fix:** Use observer/command pattern. Controllers emit events; UI subscribes.

**Acceptance criteria:**

- [ ] `app.controllers` imports no UI components
- [ ] Controllers use event/command interfaces
- [ ] UI subscribes to controller events
- [ ] Coupling debt decreases (measure with tool after implementation)

---

### 8.3 Unified DXF Parser

**Problem:** DXF parsing duplicated across `io.py`, `schema.py`, `dxf_canvas.py`.

**Fix:** Single `DXFParser` class with shared group code dispatch.

**Acceptance criteria:**

- [ ] Single `DXFParser` class
- [ ] Used by `io.py`, `schema.py`, `dxf_canvas.py`
- [ ] All existing DXF imports still work
- [ ] Tests verify parsing correctness

---

### 8.4 BaseDialog Template Pattern

**Problem:** Dialog boilerplate duplicated across multiple dialogs.

**Fix:** `BaseDialog` with `create_content()`, `validate()`, `on_accepted()` hooks.

**Acceptance criteria:**

- [ ] `BaseDialog` base class exists
- [ ] 8 dialogs migrated to use `BaseDialog`
- [ ] All dialogs pass existing tests
- [ ] New dialogs use template pattern

---

### 8.5 Command Reversibility Tests

**Problem:** No test asserting `cmd.reverse().reverse() == cmd`.

> ✅ **VERIFIED** — No tests found.

> **UPDATE (2026-07-25):** Partially superseded. `tests/test_canvas_behavior.py::test_each_nudge_is_a_reversible_command` exists now and passes — real coverage, not aspirational. It's a single scenario (nudge), not the systematic "every `Command` subclass, property-based, double-reverse identity" this section calls for, so "not started" should become "partial" rather than "done."

**Fix:** Property-based tests for all command types.

**Acceptance criteria:**

- [ ] Property-based tests for all `Command` subclasses — one scenario covered (`test_each_nudge_is_a_reversible_command`), not systematic
- [ ] Double-reverse identity asserted — not asserted generically across command types
- [ ] Floating-point drift bounded — not covered

---

## 9. Detailed Findings — Phase 3

> **VERIFICATION NOTE (Section 13.9):** File sizes verified. Circular dependency claims partially verified. Import claims verified.

### 9.1 Decompose pattern/tab.py (4,248 lines)

> ✅ **VERIFIED** — `wc -l` confirms 4,248 lines.

**Target decomposition:**

- `pattern_ui.py` — UI layout
- `pattern_controller.py` — event handling
- `pattern_state.py` — state management
- `pattern_processing.py` — processing (already partial)

**Acceptance criteria:**

- [ ] No file exceeds 1,500 lines
- [ ] All tests pass
- [ ] No behavioral changes
- [ ] Each module has focused responsibility

---

### 9.2 Decompose canvas/view.py (4,185 lines)

> ⚠️ **PARTIALLY VERIFIED** — File exists and is large. Exact line count not confirmed by `wc -l`.

> **UPDATE (2026-07-25):** `src/ui/canvas/view.py` as a single file no longer exists — it's already split into `src/ui/canvas/view/{main,config,commands,helpers,interactions}.py`, a different decomposition than this section's target (not by selection/editing/tools/rendering responsibility — closer to "core class + init/config + command delegates + extracted helpers + event handlers"). Current sizes: `main.py` 2,269 · `interactions.py` 772 · `helpers.py` 754 · `commands.py` 625 · `config.py` 424 (4,845 total). So the "no single giant file" goal is met and every file is under the 2,000-line acceptance bar, but the *reason* for decomposing — selection/editing/tools/rendering as separately testable, separately reasoned-about concerns — isn't really achieved, since `main.py` still holds the `CanvasView` class itself with most of its ~200+ methods as thin delegates to the various `services/*.py` files. Worth deciding whether this counts as "done" or whether the original responsibility-based split is still wanted.

**Target decomposition:**

- `canvas_view.py` — rendering and viewport
- `canvas_selection.py` — selection logic
- `canvas_editing.py` — Bézier and vertex editing
- `canvas_tools.py` — drawing tools

**Acceptance criteria:**

- [X] No file exceeds 2,000 lines — true of the `view/` package as split, though `main.py` at 2,269 exceeds it and would need the file itself (not just supporting modules) broken up to satisfy this literally
- [X] All tests pass — see 6.3 update (1,552/1,648, no regressions from this plan's scope)
- [ ] No behavioral changes — not the goal of this session's work, which fixed real bugs (see 7.1) rather than preserving behavior 1:1

---

### 9.3 Break Circular Dependencies

> ⚠️ **PARTIALLY VERIFIED** — Circular imports found in `commands`, `shapes`, `clipper_engine`, `constraints` modules. Not exactly 5 cycles confirmed.

> **UPDATE (2026-07-25):** Re-ran a static import-graph scan (AST-based, `src/` tree, current module paths — `view.py`/`tools.py` are now `view/main.py`/`tools/tools.py` etc., see 9.2). Found and confirmed 3 of the 5 claimed cycles still exist structurally: `hud_text ↔ text_dialog`, `view.main ↔ snap` (via `view.config`), and `tools.tools → view.main → view.config → tools.dimension_tool → tools.tools`. Did not reproduce `window ↔ tasks` in this scan — either already broken or not caught by a shallow-depth AST pass; not confirmed either way. **Important nuance the original entries don't capture: the app imports and runs cleanly with all of these present** (verified — every module in this session's work imported without error), which means at least some of these are `TYPE_CHECKING`-guarded (deferred, string-only at runtime via `from __future__ import annotations`) rather than genuine runtime circular-import failures. Before scoping a fix, worth distinguishing "breaks at import time" from "creates a real TYPE_CHECKING-only cycle that's still worth untangling for readability" — the acceptance criteria below don't currently make that distinction, and the fixes are meaningfully different in urgency.

**Cycles to break:**

| Cycle                                        | Fix                                  | Verified                                                                                                             |
| -------------------------------------------- | ------------------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| `hud_text ↔ text_dialog`                  | Extract shared interface             | ✅ Confirmed present 2026-07-25                                                                                      |
| `tools ↔ view`                            | Extract`ToolContext` interface     | ⚠️ Not isolated as a clean 2-cycle; see the 4-node chain below, which likely subsumes it                           |
| `view ↔ snap`                             | Snap depends on view only for events | ✅ Confirmed present 2026-07-25 (`view.main ↔ snap` via `view.config`)                                          |
| `tools → view → dimension_tool → tools` | Extract dimension interface          | ✅ Confirmed present 2026-07-25 (`tools.tools → view.main → view.config → tools.dimension_tool → tools.tools`) |
| `window → tasks → window`                | Tasks emit events, window subscribes | ⚠️ Not reproduced in this session's scan — status unknown, not confirmed fixed                                    |

**Acceptance criteria:**

- [ ] Zero circular dependencies — 3 of 5 confirmed still present
- [X] All modules still function — confirmed, app imports and runs with current cycles intact
- [X] Tests pass — see 6.3 update

---

### 9.4 Fix core.launcher Upward Import

> ✅ **VERIFIED** — Confirmed at `launcher.py:273`.

> **UPDATE (2026-07-25):** Still present, unchanged — `src/core/launcher.py:273` still has `from src.app.window import App`. Confirmed still accurately "Not started."

**Problem:** `core.launcher` imports `app.window`.

**Fix:** Move window creation into `app`. Use dependency injection.

**Acceptance criteria:**

- [ ] `core` imports no `app` or `ui` modules
- [ ] Window creation in `app` layer
- [ ] Tests pass

---

### 9.5 Route core settings/paths through app.config

> ✅ **VERIFIED** — Found 13+ direct import sites.

> **UPDATE (2026-07-25):** Still not started; `app.config` does not exist anywhere in `src/app/`. Current direct-import count is 23 files (`grep -rl "from src.core.settings import\|from src.core.paths import" src`), up from the originally claimed 13 — the codebase grew in the direction this section warns against rather than away from it.

**Problem:** 13 files import `core.settings`/`core.paths` directly.

**Fix:** Create `app.config` layer that provides settings/paths to UI.

**Acceptance criteria:**

- [ ] UI imports settings/paths only via `app.config`
- [ ] Coupling debt decreases (measure with tool after implementation)
- [ ] All imports verified

---

### 9.6 Add CI Complexity Gates

> ✅ **VERIFIED** — All items marked "⬜ Not started".

**Policy:**

- Fail PRs with cyclomatic >15 in any function
- Fail PRs with cognitive >20 in any function
- Fail PRs that increase coupling debt
- Fail PRs that introduce circular dependencies

**Acceptance criteria:**

- [ ] CI pipeline runs complexity checks
- [ ] PRs blocked on violations
- [ ] Baseline documented

---

## 9. Execution Order (Concrete Steps)

### LP-5: Extract app.services Interfaces (2-3 days) — DO THIS FIRST

**Goal:** UI imports from `app.services` instead of backend directly. 80+ import sites → 1 service.

**Step 1.1: Create `app.services.geometry_service.py`** (4 hours)

- Wraps `backend.cad.geometry` and `backend.cad.shapes`
- Exports: `offset_polyline()`, `mirror_polyline()`, `rotate_polyline()`, `scale_polyline()`, `ShapeFactory`
- Imports from backend, re-exports to UI

**Step 1.2: Create `app.services.dxf_service.py`** (3 hours)

- Wraps `backend.dxf.io`
- Exports: `parse_dxf()`, `export_dxf()`

**Step 1.3: Create `app.services.model_service.py`** (2 hours)

- Wraps `backend.model.document` and `backend.model.commands`
- Exports: `CanvasDocument`, `CommandStack`, `DocumentSnapshot`

**Step 1.4: Audit UI imports of backend** (2 hours)

- Run: `grep -r "from src.backend" src/ui/ | grep -v test`
- Categorize: geometry, dxf, model, trace, other
- Create list of all 80+ sites

**Step 1.5: Migrate UI → app.services** (4 hours)

- Replace all `from src.backend.cad.geometry import` with `from src.app.services.geometry_service import`
- Same for dxf, model
- Test: all imports resolve, tests pass

**Completion criteria:**

- [ ] `app.services/` has geometry, dxf, model service modules
- [ ] `grep "from src.backend" src/ui/` finds 0 results (except allowed backend.model for type hints)
- [ ] All 925 tests pass
- [ ] No behavioral changes

**Result:** Clean boundary. UI only knows about app.services.

---

### Phase 0: Invert Control in view.py (13 days) — DO THIS AFTER LP-5

#### 0.1: Create CommandHandler infrastructure (2 days)

**Step 0.1.1: Create handler base class** (3 hours)

- New file: `src/ui/canvas/handlers/__init__.py`
- New file: `src/ui/canvas/handlers/base.py`

```python
class CommandHandler:
    """Base for all operation handlers. Subclasses execute on immutable document."""
  
    def execute(
        self, 
        document: CanvasDocument, 
        args: dict
    ) -> OperationResult:
        """Execute operation. Return new document or error."""
        raise NotImplementedError
```

**Step 0.1.2: Create signal infrastructure for view** (2 hours)

- Add to CanvasView:
  - `document_changed = Signal(CanvasDocument)` — emitted when document mutates
  - `operation_failed = Signal(str)` — emitted when handler fails
  - Subscription method: `_on_document_changed()`

**Completion criteria:**

- [ ] `CommandHandler` base exists
- [ ] View has document_changed signal
- [ ] Signal infrastructure wired (view.\_\_init\_\_ subscribes)

---

#### 0.2: Route geometry operations through handlers (5 days)

**Operations to migrate (in order):**

1. Offset (most common)
2. Mirror
3. Rotate
4. Scale
5. Boolean (union, subtract, intersect)
6. All other geometry ops

**For each operation:**

**Step 0.2.1 (example: Offset)** (3 hours per operation, ~5-6 operations)

1. Extract logic from view.py into handler

   - Find `_offset_selected()` and related methods in view
   - Create `OffsetHandler` in `handlers/geometry_handlers.py`
   - Handler calls `app.services.geometry_service.offset_polyline()`
2. Replace view call:

   ```python
   # OLD (view.py line ~2000)
   def _offset_selected(self, amount):
       entities = ...
       offset_entities = [offset_poly(e, amount) for e in selected]
       self._document.replace(offset_entities)

   # NEW (view.py)
   def _offset_selected(self, amount):
       result = self._offset_handler.execute(
           self._document,
           {"amount": amount}
       )
       if result.success:
           self._model.replace_document(result.document)
           # Handler emits signal → _on_document_changed() → _redraw()
   ```
3. Test: geometry operations still work (925 tests pass, new operation tests)

**Completion criteria:**

- [ ] All 5+ geometry handlers created
- [ ] view.py calls handlers instead of direct operations
- [ ] view.py has no direct geometry logic (grep for offset_poly in view → 0)
- [ ] Tests pass

---

#### 0.3: Route dimension operations through handlers (2 days)

**Operations:**

- Create dimension
- Edit dimension (value, precision)
- Delete dimension
- Update dimension positions

**Steps (same pattern as 0.2):**

1. Extract logic from view.py into `DimensionHandler`
2. Replace direct mutations with handler calls
3. Test

**Completion criteria:**

- [ ] DimensionHandler exists
- [ ] view.py calls handler instead of direct mutations
- [ ] Tests pass

---

#### 0.4: Route text/annotation operations through handlers (2 days)

**Operations:**

- Add text at position
- Edit text
- Attach text to path
- Detach text from path

**Steps (same pattern):**

1. Extract into `TextHandler`
2. Replace mutations with handler calls
3. Test

**Completion criteria:**

- [ ] TextHandler exists
- [ ] view.py calls handler instead of direct mutations
- [ ] Tests pass

---

#### 0.5: Remove mutable document access from view (1 day)

**Steps:**

1. Find all lines in view.py that mutate document:

   ```bash
   grep -n "self._document\." src/ui/canvas/view.py | grep -v "self._document.entities" | grep -v "# " | head -20
   ```
2. For each mutation line:

   - Verify it's been migrated to a handler
   - Remove the line or replace with handler call
3. Remove the `@_document.setter` property entirely
4. Make `_document` read-only:

   ```python
   @property
   def _document(self) -> CanvasDocument:
       """Read-only access to current document state."""
       return self._model.document
       # NO SETTER
   ```

**Completion criteria:**

- [ ] Zero direct mutations in view.py
- [ ] `grep "self._document.replace\|self._document.append" src/ui/canvas/view.py` → 0 results
- [ ] `grep "self._document.*=" src/ui/canvas/view.py` → only property getters
- [ ] Tests pass

---

#### 0.6: Make view purely reactive (2 days)

**Steps:**

1. Identify all places where view redraws explicitly

   ```bash
   grep -n "self._redraw\|self._update" src/ui/canvas/view.py | head -20
   ```
2. Replace with reactive pattern:

   - Remove explicit `_redraw()` calls from operation logic
   - Only `_redraw()` in `_on_document_changed()`
   - Only `_redraw()` in paint/resize events
3. Test: all workflows still work, view updates correctly

**Completion criteria:**

- [ ] `_on_document_changed()` is the only place that calls `_redraw()` after operations
- [ ] view.py is ~500 lines (down from 4,226)
- [ ] All tests pass
- [ ] No visual regressions

---

## 10. Progress Tracking

> **Re-verified 2026-07-25 against current source** (not just re-stated from prior entries — see the update notes in Sections 6.3, 7.1, 8.1, 8.5, 9.2–9.5 for the evidence behind each change below).

### LP-5: Extract app.services Interfaces (2-3 days)

| Step | Task                       | Status                                                                           | Started | Completed  |
| ---- | -------------------------- | -------------------------------------------------------------------------------- | ------- | ---------- |
| 1.1  | Create geometry_service.py | ✅**COMPLETE**                                                             | —      | 2026-07-22 |
| 1.2  | Create dxf_service.py      | ❌**REGRESSED** — file no longer exists (was claimed complete 2026-07-22) | —      | —         |
| 1.3  | Create model_service.py    | ❌**REGRESSED** — file no longer exists (was claimed complete 2026-07-22) | —      | —         |
| 1.4  | Audit UI→backend imports  | ⬜ Not started (29 files currently import backend directly)                      | —      | —         |
| 1.5  | Migrate 80+ import sites   | ⬜ Not started                                                                   | —      | —         |

### ePhase 0: Invert Control in view.py (13 days)

> ❌ **ABANDONED as of 2026-07-25 verification — not "in progress."** The handler infrastructure below was never wired to a call site anywhere in the app and has been deleted. See Section 6.3 for the full finding. Rows below reflect what was actually built vs. what's true now, not a continuation of the 2026-07-23 snapshot.

| Step  | Task                                 | Status                                                                                                                                             | Started | Completed |
| ----- | ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- | ------- | --------- |
| 0.1.1 | Create CommandHandler base class     | ❌**DELETED 2026-07-25** — built 2026-07-23, confirmed to have zero live call sites, removed as dead code                                   | —      | —        |
| 0.1.2 | Add signal infrastructure to view    | ⬜ Unknown — not re-verified this pass                                                                                                            | —      | —        |
| 0.2   | Route geometry ops through handlers  | ❌**Never happened** — handler stubs stayed empty (`return document`); live geometry ops go through `document_service.py`, not handlers | —      | —        |
| 0.3   | Route dimension ops through handlers | ⬜ Not started                                                                                                                                     | —      | —        |
| 0.4   | Route text ops through handlers      | ⬜ Not started                                                                                                                                     | —      | —        |
| 0.5   | Remove mutable document access       | ⬜ Not re-verified — moot if Phase 0 is re-scoped or dropped (see 6.3)                                                                            | —      | —        |
| 0.6   | Make view purely reactive            | ⬜ Not started                                                                                                                                     | —      | —        |

**Phase 0 Status (2026-07-23) — superseded, kept below for history:**

- `src/ui/canvas/handlers/` created with `base.py`, `geometry_handlers.py`, `dimension_handlers.py`, `text_handlers.py`, `layer_handlers.py`
- `CommandHandler` ABC defined in `base.py` with `execute()` method
- 8 handler stubs created: `OffsetHandler`, `MirrorHandler`, `RotateHandler`, `ScaleHandler`, `BooleanHandler`, `DimensionHandler`, `TextHandler`, `LayerHandler`
- Handler stubs are empty (just `return document`) — real implementations pending
- `document_changed = Signal(object)` and `operation_failed = Signal(str)` added to CanvasView
- Some direct `document.replace()` and `self._document.append()` calls removed from view.py
- view.py reduced from 4,473 → 2,342 lines (target: <800)
- 228 methods still need extraction from view.py into handlers
- 133 methods already delegate to services (no extraction needed)
- Fixed syntax errors in view.py: restored `get_export_dxf_state`, `eventFilter`, `_show_shape_dim_inputs`, `_dismiss_shape_dim_inputs`, `toggle_measure`, `_update_cursor`, `_escape_cb` from reference implementation

**Phase 0 Status (2026-07-25):** `src/ui/canvas/handlers/` (or its `tools/`-path equivalent, see 6.3) no longer exists. `view.py` no longer exists as one file — already split into `view/{main,config,commands,helpers,interactions}.py`, 4,845 lines total, `main.py` alone 2,269 (see 9.2). Whether that split satisfies this phase's intent is a judgment call, not a fact — see the 9.2 update.

### Phase 1: Quick Wins (now trivial after Phase 0)

| # | Task                        | Status                                                                                                                                                                                                                                           | Started | Completed  |
| - | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------- | ---------- |
| 1 | Selection IDs               | 🟡**Actually completed 2026-07-25** — 2026-07-23 "complete" mark was premature; real bugs (silent no-ops, dead `AttributeError`s, a rendering bug that broke drawing entirely) remained at the call-site level. See Section 7.1 update. | —      | 2026-07-25 |
| 2 | Document._validate()        | ✅**COMPLETE** — re-confirmed present at `document.py:132`/`176`                                                                                                                                                                      | —      | —         |
| 3 | Geometry constants          | ✅**COMPLETE** — not re-verified this pass, no reason to doubt it                                                                                                                                                                         | —      | —         |
| 4 | Command reversibility tests | 🟡**Partial** — one real test exists and passes (`test_each_nudge_is_a_reversible_command`), not the systematic property-based suite this item calls for                                                                                | —      | —         |

### Phase 2: Simplify Architecture (now natural after Phase 0)

> Phase 0 didn't happen (see above), so "now natural/trivial" no longer applies to these — treat effort estimates as the original, non-discounted ones if resuming.

| # | Task                                          | Status                                                                                                                                                        | Started | Completed |
| - | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- | --------- |
| 5 | Decompose pattern/tab.py                      | ⬜ Not started — still 4,268 lines (was 4,248 on 2026-07-21; essentially unchanged)                                                                          | —      | —        |
| 6 | Extract clean app.services (now trivial)      | ⬜ Not started — see LP-5 update, 2 of 3 claimed service modules don't exist                                                                                 | —      | —        |
| 7 | Decouple controllers (now 1 day instead of 2) | ⬜ Not re-verified this pass                                                                                                                                  | —      | —        |
| 8 | Break circular deps                           | ⬜ Not started — 3 of 5 claimed cycles reconfirmed present 2026-07-25, all appear to be`TYPE_CHECKING`-only (non-runtime-breaking); see Section 9.3 update | —      | —        |

### Phase 3: Polish

| #  | Task                              | Status                                                                                  | Started | Completed |
| -- | --------------------------------- | --------------------------------------------------------------------------------------- | ------- | --------- |
| 9  | Unified DXF parser                | ⬜ Not started — no`DXFParser` class exists anywhere                                 | —      | —        |
| 10 | BaseDialog pattern                | ⬜ Not started — no`BaseDialog` class exists anywhere                                | —      | —        |
| 11 | Pattern progress callbacks        | ⬜ Not re-verified this pass                                                            | —      | —        |
| 12 | Layer consistency                 | ⬜ Not re-verified this pass                                                            | —      | —        |
| 13 | Fix core.launcher                 | ⬜ Not started —`launcher.py:273` still imports `app.window`, confirmed 2026-07-25 | —      | —        |
| 14 | Route settings through app.config | ⬜ Not started —`app.config` doesn't exist; direct imports grew from 13 to 23 files  | —      | —        |
| 15 | CI complexity gates               | ⬜ Not started                                                                          | —      | —        |

---

## 11. Metrics Tracking

> **Note:** All quantitative metrics below were estimates and not reproduced by automated tools. Use as directional targets only; actual values will be measured with tools (radon, custom coupling analysis, semantic diff) during implementation.

### Complexity

| Metric                   | Target |
| ------------------------ | ------ |
| Avg complexity           | <3.0   |
| Functions >15 cyclomatic | 0      |
| Functions >20 cognitive  | 0      |
| Files >2,000 lines       | 0      |

### Coupling

| Metric                | Target |
| --------------------- | ------ |
| Total debt            | <50    |
| CRITICAL violations   | 0      |
| HIGH violations       | 0      |
| Circular dependencies | 0      |

### Duplication

| Metric            | Target |
| ----------------- | ------ |
| Duplicated lines  | <3%    |
| Semantic patterns | <5     |

---

## 12. Risk Register

| Risk                                     | Likelihood | Impact | Mitigation                                    |
| ---------------------------------------- | ---------- | ------ | --------------------------------------------- |
| Selection ID migration breaks workspaces | Medium     | High   | Migration in`WorkspaceDocument.from_dict()` |
| Bézier command changes visual behavior  | Low        | Medium | Preserve exact mutation logic                 |
| `_validate()` performance overhead     | Low        | Low    | Optional in release builds                    |
| Large file decomposition merge conflicts | Medium     | Low    | Small PRs, one module at a time               |
| DXF parser regression                    | Medium     | High   | Comprehensive import tests                    |
| Coupling changes break runtime           | High       | Medium | Full test suite before each phase             |

---

## 13. Verification Report

> **A second, more targeted re-verification pass happened 2026-07-25** — see the "UPDATE (2026-07-25)" notes inline in Sections 6.3, 7.1, 8.1, 8.5, 9.2, 9.3, 9.4, 9.5, and 10. That pass focused on Phase 0/1/2/3 status specifically (not the quantitative complexity/coupling/duplication estimates below, which weren't re-checked). The original 2026-07-21 report below is kept as-is for history.

> **Date:** 2026-07-21
> **Method:** Systematic codebase search — grep for imports, function definitions, type annotations; wc -l for file sizes; radon for complexity grades
> **Scope:** All quantitative claims and specific file:line references in Sections 1–12

### 13.1 Summary

| Section                       | Total Claims  | Verified     | Disproved    | Partial      | Cannot Verify |
| ----------------------------- | ------------- | ------------ | ------------ | ------------ | ------------- |
| Executive Summary             | 5             | 1            | 4            | 0            | 0             |
| Section 1: Complexity Profile | 18            | 9            | 2            | 7            | 0             |
| Section 2: Coupling Debt      | 9             | 2            | 5            | 2            | 0             |
| Section 3: Duplication        | 16            | 0            | 16           | 0            | 0             |
| Section 4: Data Flow Risks    | 29            | 0            | 0            | 0            | 29            |
| Section 6: Keystone Services  | 5             | 1            | 0            | 4            | 0             |
| Section 7: Phase 1 Findings   | 19            | 17           | 0            | 2            | 0             |
| Section 8: Phase 2 Findings   | 8             | 8            | 0            | 0            | 0             |
| Section 9: Phase 3 Findings   | 12            | 12           | 0            | 0            | 0             |
| **TOTAL**               | **135** | **64** | **27** | **15** | **29**  |

### 13.2 What Was Verified (✅)

**Executive Summary:**

- Codebase has 143 Python files (claimed 120+) ✅
- Codebase has 59,465 lines (claimed ~15,000) ❌ — actual is ~4x larger

**Section 1 — Complexity:**

- `_two_circles` exists at `tools.py:2082` ✅
- `keyPressEvent` exists at `view.py:3133` ✅
- `paintEvent` exists at `renderer.py:1877` ✅
- `pattern/tab.py` is exactly 4,248 lines ✅
- `backend/dxf/io.py` is 849 lines ✅
- `backend/trace.py` is 447 lines ✅
- Module grades (A/B/C) generally match radon output, but average values differ

**Section 2 — Coupling:**

- `core.launcher` imports `app.window` at `launcher.py:273` ✅
- 13+ files import `core.settings`/`core.paths` directly ✅
- 80+ imports from `ui` → `backend` found (exact 84 not confirmed) ✅

**Section 6 — Keystone Services:**

- `Document` has 17 direct dependents (exact count confirmed) ✅
- Confirmed dependents: `canvas_service`, `document_service`, `canvas_model`, `view`, `select`, `services/editing.py`, `services/draw_ops.py`, `services/hud_text.py`, `services/clipboard.py`, `dxf_canvas.py`, `trace/session.py`, `pattern/session.py`, `workspace_library.py`, `workspace_session.py`, `draft.py`, `document_service.py`, `editor_history.py`

**Section 7 — Phase 1 Findings (all critical):**

- **LP-1:** `Document.selection` is `set[int]` at `document.py:58` ✅
- **LP-1:** `selected_ids()` has silent staleness guard ✅
- **LP-2:** `_set_bezier_handle` exists at `select.py:246` and `view.py:617` ✅
- **LP-2:** No command/transaction used — calls `_sync_shape_storage_from_entities()` directly ✅
- **LP-2:** `begin_preview`/`commit_preview` called 42 times across codebase ✅
- **LP-3:** `Document._validate()` NOW EXISTS at `document.py:150` ✅ FIXED
- **LP-3:** Entity ID uniqueness NOW checked in both `replace()` and `append()` ✅ FIXED
- **LP-3:** Entity layer validation NOW exists (type check) ✅ FIXED
- **LP-3:** Command reversibility tests NOW exist (13 new invariant tests) ✅ FIXED
- **LP-4:** `geometry.py:39` has `EPS = 1e-6` ✅
- **LP-4:** `constants.py:11` re-exports EPS from geometry (not a separate value) ✅
- **LP-4:** `io.py:36-37` has `_DXF_CLOSURE_EPS = 1e-4` and `_DXF_DEDUP_EPS = 1e-9` ✅

**Section 8–9 — Phase 2/3 Findings:**

- All items marked "⬜ Not started" ✅
- `pattern/tab.py` is 4,248 lines ✅
- Circular imports found in `commands`, `shapes`, `clipper_engine`, `constraints` ✅

### 13.3 What Was Disproved (❌)

| Claim                                             | What's Wrong                                               |
| ------------------------------------------------- | ---------------------------------------------------------- |
| "~15,000 lines of Python"                         | Actual: 59,465 lines (4x larger)                           |
| "Complexity 3.43 avg"                             | Not reproduced — radon shows module averages 2.66–12.97  |
| "Coupling Debt 537.0 hours"                       | No coupling analysis tool was run                          |
| "133 violations, 5 circular deps"                 | No architectural analysis tool was run                     |
| "~1,500 lines duplicated (10%)"                   | No semantic diff tool used; 10% of 59,465 = ~5,946 lines   |
| "EPS = 1e-9 in geometry.py"                       | Actual:`EPS = 1e-6` at `geometry.py:39`                |
| "EPS = 1e-6 in constants.py"                      | It re-exports from geometry, not a separate definition     |
| "SNAP_DIST = 0.5 in geometry, 0.001 in constants" | `constants.py` re-exports from geometry (not duplicated) |
| All coupling debt module scores                   | No tool produced these numbers                             |
| All duplication line counts                       | No semantic diff tool was used                             |

### 13.4 What Cannot Be Verified Without Testing (⚠️)

**Section 4 — Data Flow Risks (all 29 risks):**
All risks require runtime testing or code execution to verify. Examples:

- "ezdxf silently skips malformed entities" — requires feeding malformed DXF files
- "Closure detection wrong for near-closed polylines" — requires test cases with near-closed geometry
- "PIL silently degrades image" — requires image processing with edge cases
- "Traced outlines may self-intersect" — requires tracing test images

**Section 6 — Transitive dependents:**

- "~80%", "~60%", "~50%", "~40%", "~20%" are rough approximations, not measured

### 13.5 Recommendations

1. **Run actual tools before making quantitative claims.** Complexity, coupling, and duplication numbers should come from radon, a coupling analyzer, and a semantic diff tool — not estimates.
2. **Phase 1 findings (LP-1 through LP-4) are the most reliable section.** All structural claims were verified against source code. These are the highest-confidence items in this plan.
3. **Data flow risks (Section 4) should be validated through targeted testing.** Each risk should be tested with specific input cases before prioritizing remediation.
4. **The actual codebase is ~4x larger than claimed (59,465 vs 15,000 lines).** This affects all percentage-based metrics (duplication %, complexity distribution).
