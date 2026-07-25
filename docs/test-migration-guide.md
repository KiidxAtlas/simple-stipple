# Test Suite Migration Guide

**Goal:** Ensure the test suite only passes against the final planned architecture (after plan.md completion).

**Status:** Investigation complete, targeted changes applied. See "What Actually Changed" below for the real outcome — the initial estimate of "82 files need rewriting" was wrong; see the audit for why.

## What Actually Changed

An audit of all 82 test files (13,768 lines) found the initial assumption — "every test needs
rewriting" — didn't hold up:

| Category | Files | Why |
|---|---|---|
| Pure backend tests (geometry, DXF, patterns, boolean ops) | 37 | Import `src.backend.*` directly, correctly — they test backend code, and Phase 0-2 doesn't restructure backend logic, only how UI reaches it. Rewriting these to avoid backend imports would break valid tests for no reason. |
| UI behavior tests (`test_canvas_behavior.py`, etc.) | ~8 | Already drive `CanvasView`/`DxfCanvas` through public APIs — synthesized mouse/keyboard events — not through the internal mutation methods Phase 0 removes. Already robust to the refactor. |
| Files calling old internal patterns directly | 0 | Grepped for `_offset_selected(`, `_mirror_selected(`, etc. as method calls (not test names) — none found. |
| Files needing real changes | 2 | `tests/test_architecture.py`, `tests/test_imports.py` — see below. |

**Conclusion:** the test suite was already written defensively (public-API-driven, not
implementation-coupled) except for the architecture-boundary checks themselves, which
didn't exist yet. So the actual work was to *add* strict final-architecture enforcement,
not rewrite the existing behavioral suite.

### Files Changed

**`tests/test_architecture.py`** — added 8 tests asserting the Phase 0/2 end state:
- `test_ui_never_imports_backend_directly` — repo-wide sweep, all of `src/ui`
- `test_app_services_completely_wraps_backend` — geometry/dxf/model/pattern services export the right symbols
- `test_command_handlers_exist` — `CommandHandler` + all 8 handler subclasses (Offset, Mirror, Rotate, Scale, Boolean, Dimension, Text, Layer)
- `test_view_py_no_direct_mutations` — no `self._document.replace(`/`.append(` in view.py
- `test_view_has_reactive_signals` — `CanvasView.document_changed` / `.operation_failed` signals exist
- `test_circular_imports_prevented` — sweep of core modules
- `test_view_py_final_line_count` — view.py < 800 lines
- `test_pattern_tab_final_line_count` — pattern/tab.py < 1500 lines
- `test_no_old_mutation_methods_in_view` — `_offset_selected` etc. removed from view.py

**`tests/test_imports.py`** — added a *parametrized-per-file* boundary check plus a
services-coverage check:
- `test_ui_module_does_not_import_backend_directly` — runs once per file under `src/ui`
  (not one aggregate test), so a violation points at the exact file, and a new UI file
  added later is covered automatically without editing this test again
- `test_app_services_package_covers_every_backend_package` — every `src/backend/*`
  package must be reachable from some `src/app/services/*` module, so UI always has
  a legitimate path to reach it

This satisfies the "backend files get final-architecture import assertions" requirement
**centrally** rather than duplicated 37 times — the guarantee ("no backend package is
UI-unreachable except through services") is a global property, and belongs in one place.
Duplicating the same assertion into every backend test file would mean 37 copies of
identical logic, more surface area to drift out of sync, and no stronger guarantee.

## Addendum: Static Correctness Suite (tests/test_static_correctness.py)

A follow-up request asked for tests reliable enough to catch semantic bugs a boundary
check can't see — after a user spotted three real issues (a stale-index-across-callbacks
bug, an index-shaped variable actually holding a string ID, and 15 lines of dead code)
by manually reading tools.py/select.py that none of the existing tests noticed.

Built `tests/test_static_correctness.py` — 4 AST-based checks, each parametrized per file
across all of `src/` (151 files × 4 checks = 604 tests), calibrated against the live
codebase until each had zero false positives:

1. **`test_no_dead_stores`** — a variable assigned, then reassigned, with no read in
   between (the first computation is silently thrown away).
2. **`test_no_entity_index_id_naming_mismatch`** — a variable named `entity_index`/`entity_idx`
   assigned from a function whose return annotation is `str`/`EntityId`-ish (or vice
   versa for `entity_id` from an `int`-returning call or `.index()`). Built from a
   registry of return-type annotations read directly from source (works even under
   `from __future__ import annotations`, where the annotation is a string at runtime).
3. **`test_no_persisted_list_derived_indices`** — an index from `.index()`/`enumerate()`
   written into `self.*`/`v.*`/`view.*` state that outlives the current call. Scoped to
   collections actually mutated somewhere outside `__init__` (a codebase-wide registry
   built the same way) — an index into a list set once at construction can't go stale,
   so that case is deliberately excluded rather than flagged.
4. **`test_no_dual_identity_dict_structures`** — a dict literal with both an `*_id` key
   and an `*_index` key for what reads as the same entity.

**Calibration note:** of the three originally-reported issues, two had already been
fixed independently between when they were first read and when these tests were written
— the codebase was under active concurrent edit. The tests still exist as regression
guards for that bug class, not as claims those exact lines are currently broken (check
each test's docstring dates itself as "as of this writing" for that reason — trust a
fresh test run over the docstring's line numbers). The third (dead code + naming
mismatch at tools.py's dimension-circle-click handler) was still live; it's fixed as
part of this change — see the diff to `src/ui/canvas/interaction/tools.py`. That fix was
a genuine, if small, behavior change: the discarded first computation was a
tighter circle-radius match, silently replaced by a naive "whatever's under the
cursor" fallback on every click. Fixing it restores the originally-intended precision
matching for diameter-dimension placement.

**Verification discipline applied:** each check was run against the full codebase before
being trusted, not just the file where the bug was first spotted. Where the initial
version produced a false positive (the "persisted index" check initially flagged
`cycle_icon_button.py`'s `self._current_index = i`), the check was narrowed based on a
verifiable distinction (is the source collection ever mutated outside `__init__`
anywhere in `src/`?) rather than suppressed with a one-off exemption — narrowing over
allowlisting was a deliberate choice, stated in the module docstring, so a *different*
real instance of the same bug elsewhere doesn't silently pass because of an exemption
that was really about one specific false positive.

## Recall Audit (2026-07-23)

A follow-up request asked to verify the static-correctness suite actually catches
everything, not just the three originally-spotted bugs. Zero false positives isn't the
same claim as zero false negatives — audited each of the 4 checks for blind spots by
constructing synthetic reproductions of the bug class each one targets, then checking
whether the *current* implementation caught them. Three real gaps found and fixed, one
tempting extension investigated and correctly rejected:

1. **Dead-store check missed comprehension-scope shadowing.** `x = 1; vals = [x for x
   in range(3)]; x = 2` should flag the first `x` as a dead store — the comprehension's
   `x` is Python 3's own separate scope, not a read of the outer `x`. The original
   `_names_loaded` walked all `Name`/`Load` nodes indiscriminately and treated the
   comprehension's own loop variable as if it read the outer one, masking the dead
   store. Fixed by tracking each comprehension's bound names and excluding them from
   what counts as a "read" of the same name outside it. Verified against a synthetic
   case before and after; re-ran the full 151-file check after the fix — zero new
   false positives.

2. **Naming-mismatch check was hardcoded to `entity_id`/`entity_index` only.** The
   codebase has the identical identity-confusion risk for other nouns —
   `group_id`/`segment_index`/`shape_id`/`vertex_index`/`anchor_index`/`dimension_index`
   all exist as real variable names in `src/`. The original regex only matched literal
   `entity_*` names, so a `group_index` variable holding a string ID (or vice versa)
   anywhere in the codebase would have passed silently. Generalized the pattern to any
   `<noun>_id`/`<noun>_index`/`<noun>_idx` name, verified it now fires on a synthetic
   `group_index` case, and confirmed zero new false positives on the real codebase
   (no *currently existing* instances of this bug for the other nouns — but the check
   is no longer blind to them going forward).

3. **Persisted-index check only recognized `for i, x in enumerate(...)` loops, not
   `next((i for i, x in enumerate(...) if ...), default)` generator expressions.** The
   "find-with-fallback over `enumerate()`" pattern appears in at least 10 files in this
   codebase (`view.py`, `tools.py`, `dxf_canvas.py`, `pattern/tab.py`, and others) —
   exactly the shape the original bezier-drag bug's neighboring code used. The check's
   enumerate-detection only looked at `ast.For` statements, so an index derived this
   way and then persisted onto `self.*`/`v.*` state would have been invisible to it.
   Extracted a shared `_index_derived_from_expr()` used by both the direct-assignment
   and for-loop paths, covering `.index()`, `for`-loop `enumerate()`, and
   `next()`/generator-expression `enumerate()` uniformly. Verified against a synthetic
   reproduction of the original bug shape using this pattern; confirmed zero new false
   positives on the real codebase.

4. **Investigated, and correctly did not add:** a check that any class-level field or
   function parameter named `*_id` must be `str`-typed (and `*_index` must be
   `int`-typed), scanning type annotations directly rather than inferring from call
   sites. This looked like a strictly more reliable version of check 2. Running it
   against the real codebase before committing to it surfaced 5 hits — `group_id`,
   `next_id`, `new_id` — all legitimately `int`-typed, because this codebase has *two*
   distinct identity conventions: entities use string UUIDs, but groups use small
   sequential integers (`Document.next_group_id: int`). Adding this check would have
   introduced 5 immediate false positives from a real, intentional convention this
   heuristic can't distinguish from a bug. Left unimplemented — a useful reminder that
   broader isn't automatically more accurate, and that every extension needs the same
   full-codebase calibration pass as the original four checks before being trusted.

All 604 parametrized tests (151 files × 4 checks) pass after these fixes, confirming
the three closed gaps didn't reopen any of the false-positive cases the original
calibration pass had already resolved.

## Check 5: Missing Host-Delegate Members (2026-07-23)

While demonstrating the suite by investigating a real crash the user hit running the
app (`AttributeError: 'DxfCanvas' object has no attribute '_selected_indices'`), it
became clear the crash was a symptom of something much bigger than one bad call site:
`CanvasView`'s `_selected_indices()`/`_mutable_selected_indices()` had been deleted —
superseded by ID-based `_selected_ids()`/`_mutable_selected_ids()` — but ~40 call sites
across 7 files (`editing.py`, `draw_ops.py`, `gizmo.py`, `select.py`, `renderer.py`,
`smoothing.py`, `dxf_canvas.py`) still called the old names on `self._host`. This is a
bug class none of Checks 1-4 (or any other test in the repo, including `test_imports.py`'s
"every module imports cleanly" check) could catch: Python doesn't verify attribute
existence until a call actually executes, so nothing failed until a user opened the app
and a specific code path ran.

The user redirected: don't fix the bug, build a test that catches this *class* of bug.
Added **Check 5** — resolves the `self.<attr> = SomeService(self)` delegation pattern
used throughout `src/ui/canvas/`, and verifies every `self._host.<name>` call in the
service actually exists on the class that instantiates it.

**Design, and two real false positives found and fixed during calibration:**

- Member registry combines real `dir()` on the imported class (correct for inherited
  PySide6/Qt methods — a pure-AST registry would misflag `resize()`, `width()`, etc. as
  missing, since they're not in `src/`) with an AST scan for `self.<x> = ...`
  assignments and class-body declarations.
- **False positive 1:** `dir()` on the class alone doesn't see instance attributes set
  procedurally in `__init__` (e.g. `self._cursor_wx: float | None = None`), so an
  AST-scan pass over the class's own body was added — but the *first* version only
  scanned the class being checked, not its ancestors. `RadialMenuService` is
  instantiated by `DxfCanvas`, but `_cursor_wx` is set in the *base* class
  `CanvasView.__init__` — checking `DxfCanvas` alone missed it. Fixed by walking the
  full ancestor chain (`_ancestor_declared_names`), not just the immediate class.
- **False positive 2:** `App` (the main window) declares attributes like
  `_grid_action: QAction` as a bare class-body annotation with no value —
  `_grid_action: QAction` creates no real runtime attribute (only an `__annotations__`
  entry), so neither `dir()` nor a `self.<x> = ` scan sees it. Added
  `_class_body_level_names()` to treat bare annotations as valid declared members too.
- After both fixes: re-ran against the live codebase — findings dropped from 77 to
  exactly 40, matching `grep -c` of the actual broken call sites precisely, with zero
  remaining false positives. Verified against a synthetic positive case (a delegate
  calling a genuinely nonexistent method — caught) and a synthetic negative case (calling
  a real inherited method and a real dynamically-set attribute — neither flagged).

**Note on live findings:** the codebase was under concurrent editing throughout this
work. Between two consecutive test runs, `editing.py`'s 25 broken call sites were fixed
externally, and the failure count dropped from 40 to 15 — confirming the check reflects
live code state each run, not a cached snapshot (this is not test flakiness; re-running
`grep -c` against the same file confirmed the source had actually changed). As of the
last run in this session, 15 real violations remain in `gizmo.py`, `draw_ops.py`,
`select.py`, and `renderer.py` — run `pytest tests/test_static_correctness.py -k
test_no_missing_host_member_calls -v` for the current exact list; don't trust a count
written here to still be accurate.

### Current Failure State (Expected — This Is the Point)

```
tests/test_architecture.py:            8 failed, 6 passed
tests/test_imports.py (boundary check): 9 failed, 67 passed
```

Every failure names an exact file and exact missing piece (e.g. `ui/canvas/view.py imports
backend directly: src.backend.cad.constraints, ...`). These are the same violations
already tracked in `FINAL_TARGET_TEST_RESULTS.md` — nothing new was discovered, but now
they're enforced on every test run, per-file, not just in the dedicated architecture
test files.

## Migration Rules

*(Reference for any file that genuinely does need updating in the future — e.g. if a new
test starts calling an internal view method, or a new backend package needs a service
wrapper.)*

### 1. Import Updates

**OLD (Current, to be removed):**
```python
from src.backend.cad.geometry import offset_polyline
from src.ui.canvas.view import CanvasView  # with direct mutations
```

**NEW (Final architecture):**
```python
from src.app.services.geometry_service import offset_polyline
from src.ui.canvas.view import CanvasView
from src.ui.canvas.handlers.geometry_handlers import OffsetHandler
```

### 2. Direct Mutation Tests → Handler Tests

**OLD (remove these):**
```python
def test_offset_operation():
    doc = CanvasDocument([entity])
    # Direct mutation through view
    view.offset_selected(5.0)
    assert len(doc.entities) == 1
```

**NEW (replace with):**
```python
def test_offset_handler_operation():
    from src.ui.canvas.handlers.geometry_handlers import OffsetHandler
    
    doc = CanvasDocument([entity])
    handler = OffsetHandler()
    result = handler.execute(doc, {"amount": 5.0})
    
    assert result.changed
    assert len(result.document.entities) == 1
```

### 3. Remove Tests for Old Patterns

Delete or skip tests for:
- Direct `document.replace()` calls in view
- `_offset_selected()`, `_mirror_selected()` methods on CanvasView
- Direct geometry imports in UI
- Old mutation-based APIs

### 4. Canvas View Tests

**OLD behavior (remove):**
```python
def test_view_offset():
    # view.py had 403 methods doing operations
    view._offset_selected()
```

**NEW behavior (replace with):**
```python
def test_view_emits_signal_on_operation():
    # view.py is thin (~500 lines), emits signals
    view.document_changed.connect(on_changed)
    handler.execute(doc, args)
    # Verify signal fired
```

### 5. File Size Assertions

**Add to every test file (where applicable):**
```python
def test_final_architecture_invariants():
    """Ensure code follows final architecture constraints."""
    from pathlib import Path
    
    # view.py must be < 800 lines
    view_lines = len(Path("src/ui/canvas/view.py").read_text().split('\n'))
    assert view_lines < 800, f"view.py is {view_lines} lines (target <800)"
    
    # pattern/tab.py must be < 1500 lines
    tab_lines = len(Path("src/ui/pages/pattern/tab.py").read_text().split('\n'))
    assert tab_lines < 1500, f"pattern/tab.py is {tab_lines} lines (target <1500)"
```

### 6. Import Boundary Tests

**Add to test_imports.py:**
```python
def test_ui_only_imports_from_app_services():
    """UI layer must never import backend directly."""
    ui_files = list(Path("src/ui").rglob("*.py"))
    for file_path in ui_files:
        content = file_path.read_text()
        assert "from src.backend" not in content or "backend.model" in content
```

## Files to Update First (Priority)

### High Priority (Core Architecture)
1. ✅ `test_final_architecture_target.py` — Already created
2. ✅ `test_architecture_constraints.py` — Already expanded
3. 🔴 `test_architecture.py` — Remove or replace entirely
4. 🔴 `test_canvas_behavior.py` — Update to use handlers
5. 🔴 `test_canvas_document.py` — Already mostly OK
6. 🔴 `test_imports.py` — Add service boundary tests

### Medium Priority (Feature Tests)
7. `test_boolean_ops.py` — Update to test through handlers
8. `test_dimension_tool.py` — Update to test through handlers
9. `test_transform_reversibility.py` — Commands must be reversible
10. `test_pattern_core.py` — Pattern generation tests
11. `test_dxf_import_validation.py` — DXF import tests

### Lower Priority (UI/UX Tests)
- `test_canvas_layers.py` — Layer operations
- `test_draw_sidebar.py` — Sidebar interaction
- `test_precision_bar.py` — UI precision
- `test_properties_panel.py` — Properties panel
- etc.

## Checklist for Each Test File

When updating a test file, ensure:

- [ ] All imports use `app.services`, not `backend` directly
- [ ] No tests for `_offset_selected()` etc. (old view methods)
- [ ] No direct `document.mutate()` calls (except in DocumentTests)
- [ ] Handlers exist for operations (or skip until Phase 0)
- [ ] Signals tested where applicable (document_changed, operation_failed)
- [ ] Final architecture invariants checked (file sizes, method counts)
- [ ] No circular imports
- [ ] Type consistency (EntityId vs int)
- [ ] No skipped tests (use assertions instead)

## Commands to Apply Changes

### 1. Find all backend imports in UI:
```bash
grep -r "from src.backend" src/ui/ | grep -v "backend.model" | grep -v ".pyc"
```

### 2. Find all direct mutations:
```bash
grep -r "self._document.replace\|self._document.append\|document.mutate" src/ui/
```

### 3. Find old method calls:
```bash
grep -r "_offset_selected\|_mirror_selected\|_rotate_selected" tests/
```

### 4. Verify tests pass:
```bash
pytest tests/test_final_architecture_target.py -v --tb=short
```

## Migration Timeline

- **Week 1:** Update core architecture tests (files 1-3)
- **Week 2:** Update feature tests (files 4-11)
- **Week 3:** Update UI/UX tests (remaining files)
- **Week 4:** Verify all 82 tests pass against final architecture

## Strategy

1. **Phase 0 Complete:** Handlers exist, signals work, view is thin
   → All handler-related tests pass
   → All signal tests pass
   → Old mutation tests removed

2. **Phase 1 Complete:** Selection uses IDs, validation automatic
   → Selection tests pass
   → Validation tests pass

3. **Phase 2 Complete:** Services complete, UI clean
   → All import tests pass
   → Service wrapper tests pass

4. **Phase 3 Complete:** Circular deps gone, parsers unified
   → Circular import tests pass
   → Unified parser tests pass

## Success Criteria

✅ All 82 tests pass
✅ Zero tests skipped
✅ Zero tests for old patterns (mutations, old methods)
✅ 100% use final architecture patterns
✅ No accidental dependencies on intermediate states
✅ No ambiguity — tests only pass with correct final code
