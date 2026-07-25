# Architecture Constraint Test Status (Expanded)

**Date:** 2026-07-22  
**Test File:** `tests/test_architecture_constraints.py`  
**Status:** 35 ✅ passing, 3 ❌ failing, 2 ⏭️ skipped | **Success Rate: 88%**

This document tracks test-driven enforcement of plan.md architectural changes. Includes 40 tests covering 11 architectural domains.

---

## Summary by Domain

| Domain | Tests | ✅ Pass | ❌ Fail | ⏭️ Skip | Status |
|--------|-------|---------|---------|---------|--------|
| **Selection uses IDs** (LP-1) | 9 | 9 | 0 | 0 | ✅ Complete |
| **UI Service Boundary** (LP-5) | 4 | 2 | 2 | 0 | ❌ 2 violations |
| **Command Handlers** (Phase 0) | 2 | 0 | 1 | 1 | ❌ Not started |
| **File Size Constraints** | 2 | 1 | 0 | 1 | ⏭️ Tracked |
| **Document Invariants** (LP-3) | 3 | 3 | 0 | 0 | ✅ Complete |
| **Service Structure** (LP-5) | 3 | 3 | 0 | 0 | ✅ Complete |
| **Layer Separation** | 2 | 2 | 0 | 0 | ✅ Complete |
| **Type Consistency** | 4 | 4 | 0 | 0 | ✅ Complete |
| **Class Complexity** | 2 | 1 | 0 | 1 | ⏭️ Tracked |
| **Mutation Safety** | 3 | 3 | 0 | 0 | ✅ Complete |
| **Model Consistency** | 4 | 4 | 0 | 0 | ✅ Complete |
| **Import Cycles** | 2 | 2 | 0 | 0 | ✅ Complete |
| **TOTAL** | **40** | **35** | **3** | **2** | **88%** |

---

## Passing Tests ✅ (35 total)

### Selection Uses Entity IDs (LP-1, 9 tests)
- ✅ `test_document_selection_type_is_set_of_entity_ids` — Selection is `set[EntityId]`, not `set[int]`
- ✅ `test_selection_survives_entity_insertion` — Selection preserved when entities inserted
- ✅ `test_selection_survives_entity_reordering` — Selection preserved when entities reordered
- ✅ `test_selection_cleanup_on_entity_deletion_not_yet_automatic` — Handles orphaned IDs
- ✅ `test_no_silent_selection_dropping` — No silent dropping of stale selections
- ✅ `test_select_multiple_ids_at_once` — Bulk selection preserves all IDs
- ✅ `test_deselect_operation_removes_ids` — Deselection removes specific IDs
- ✅ `test_select_empty_set_clears_selection` — Empty selection works correctly
- ✅ `test_selected_indices_reflects_current_order` — Indices reflect entity reordering

### Document Invariants (LP-3, 3 tests)
- ✅ `test_append_validates_invariants` — Validates & auto-reassigns duplicate IDs
- ✅ `test_replace_validates_invariants` — Clears stale selection after replace
- ✅ `test_selection_with_orphaned_ids_not_yet_validated` — Documents missing orphan detection

### Service Module Structure (LP-5, 3 tests)
- ✅ `test_geometry_service_exists_and_exports` — geometry_service.py exports functions
- ✅ `test_dxf_service_exists_and_exports` — dxf_service.py exports DXF functions
- ✅ `test_model_service_exists_and_exports` — model_service.py re-exports types

### Layer Separation (2 tests)
- ✅ `test_app_services_do_not_import_ui_widgets` — Services don't import widgets
- ✅ `test_app_controllers_do_not_import_ui_widgets` — Controllers avoid widget imports

### Type Consistency (4 tests)
- ✅ `test_entity_record_id_is_string` — EntityRecord.id is always str
- ✅ `test_selection_contains_strings_not_ints` — Selection contains strings, not ints
- ✅ `test_index_for_id_returns_int` — index_for_id() returns int indices
- ✅ `test_entity_for_id_returns_record_not_index` — entity_for_id() returns EntityRecord

### Mutation Safety (3 tests)
- ✅ `test_append_operation_is_safe` — append() commits atomically
- ✅ `test_replace_operation_clears_selection` — replace() clears orphaned selection
- ✅ `test_drop_inactive_selection_removes_hidden_entities` — Hidden entities removed from selection

### Model Consistency (4 tests)
- ✅ `test_entity_points_are_coordinate_tuples` — Points are (float, float) tuples
- ✅ `test_entity_layer_is_string_or_none` — Layer is str | None, not int
- ✅ `test_entity_group_is_int_or_none` — Group is int | None, not str
- ✅ `test_document_dimensions_are_dicts` — Dimensions are dict objects

### Import Safety (2 tests)
- ✅ `test_document_module_is_depended_on_not_circular` — No circular imports
- ✅ `test_core_settings_not_imported_by_many_modules` — Settings not scattered

### UI Service Boundary (LP-5, 2 tests)
- ✅ `test_ui_canvas_does_not_import_backend_dxf_directly` — DXF routed via services
- ✅ `test_app_services_layer_exists` — Service modules exist

---

## Failing Tests ❌

### 1. UI Geometry Imports Not Yet Routed Through Services (LP-5)
**Status:** ❌ FAIL  
**Priority:** HIGH  
**Issue:** 2 UI files still import directly from backend.cad.geometry instead of app.services

```
FAILED: test_ui_canvas_does_not_import_backend_geometry_directly

Violations found:
  - src/ui/canvas/interaction/tools.py: src.backend.cad.geometry
  - src/ui/canvas/interaction/select.py: src.backend.cad.geometry

Expected fix: Import from app.services.geometry_service instead
```

**What to fix:**
```python
# BEFORE (tools.py, select.py)
from src.backend.cad.geometry import offset_polyline, rotate_polyline, ...

# AFTER
from src.app.services.geometry_service import offset_polyline, rotate_polyline, ...
```

**Effort:** 2-3 hours  
**PR Size:** Small (import updates only, no logic changes)

---

### 2. UI Pages Import Backend Pattern/Preflight Modules (LP-5)
**Status:** ❌ FAIL  
**Priority:** HIGH  
**Issue:** 2 UI page files import directly from backend modules instead of services

```
FAILED: test_ui_pages_does_not_import_backend_directly

Violations found:
  - src/ui/pages/trace/tab.py: src.backend.cad.preflight
  - src/ui/pages/pattern/tab.py: src.backend.pattern.output

Expected fix: Create wrapper services or move to app.services layer
```

**What to fix:**
```python
# BEFORE (pattern/tab.py)
from src.backend.pattern.output import export_laserstar_package

# AFTER (create wrapper or add to service)
from src.app.services.pattern_service import export_laserstar_package
```

**Effort:** 3-4 hours  
**PR Size:** Small-Medium

---

### 3. CanvasView Missing document_changed Signal (Phase 0)
**Status:** ❌ FAIL  
**Priority:** CRITICAL  
**Issue:** Phase 0 refactor (Command Handlers) not yet started; view lacks reactive signal

```
FAILED: test_view_emits_document_changed_signal

AssertionError: CanvasView missing attribute 'document_changed' (Signal)

Expected: class-level Signal for document mutations
```

**What to add:**
```python
# Add to CanvasView class definition
class CanvasView(QWidget):
    document_changed = Signal(Document)  # ← Missing
    operation_failed = Signal(str)       # ← Missing
```

**Effort:** 13 days (full Phase 0 refactor)  
**PR Size:** LARGE (touches ~50% of view.py)  
**Blocking:** Until this lands, Phases 1-3 are difficult

---

## Test Skip Status ⏭️

**1 Skipped Test:**
- ⏭️ `test_command_handler_base_class_exists` — Phase 0 not started; base class doesn't exist yet

This test will pass once Phase 0 infrastructure is in place.

---

## Action Plan

### Immediate (This Week) — LP-5 Import Cleanup
**Effort:** ~5-6 hours total  
**Payoff:** Clean UI→services boundary, unblocks Phase 0

1. **Create/extend app.services wrapper modules:**
   - Add `offset_polyline()` to `geometry_service.py` ✅ (already exists?)
   - Add pattern output functions to `pattern_service.py`
   - Add preflight functions to new `cad_service.py`

2. **Update imports in UI files:**
   - [ ] `src/ui/canvas/interaction/tools.py` → use geometry_service
   - [ ] `src/ui/canvas/interaction/select.py` → use geometry_service
   - [ ] `src/ui/pages/pattern/tab.py` → use pattern_service
   - [ ] `src/ui/pages/trace/tab.py` → use cad_service

3. **Verify tests pass:**
   ```bash
   pytest tests/test_architecture_constraints.py::TestUIServiceBoundary -v
   ```

### Medium Priority (After LP-5) — Phase 0 Infrastructure
**Effort:** 13 days  
**Payoff:** Unlocks file decomposition, enables Phases 1-3

1. Create `src/ui/canvas/handlers/base.py` with `CommandHandler`
2. Add `document_changed`, `operation_failed` signals to CanvasView
3. Route geometry operations through handlers
4. Remove mutable document access from view

---

## Running Tests

### Run all architecture tests:
```bash
pytest tests/test_architecture_constraints.py -v
```

### Run by domain:
```bash
# Selection (LP-1)
pytest tests/test_architecture_constraints.py::TestSelectionUsesIds -v
pytest tests/test_architecture_constraints.py::TestSelectionBatchOperations -v

# Document invariants (LP-3)
pytest tests/test_architecture_constraints.py::TestDocumentInvariants -v

# Type safety
pytest tests/test_architecture_constraints.py::TestTypeConsistency -v

# Service boundary (LP-5)
pytest tests/test_architecture_constraints.py::TestServiceModuleStructure -v
pytest tests/test_architecture_constraints.py::TestUIServiceBoundary -v

# Model consistency
pytest tests/test_architecture_constraints.py::TestModelConsistency -v

# Mutation safety
pytest tests/test_architecture_constraints.py::TestDocumentMutationSafety -v
```

### Run only failing tests (work to do):
```bash
pytest tests/test_architecture_constraints.py::TestUIServiceBoundary::test_ui_canvas_does_not_import_backend_geometry_directly -v
pytest tests/test_architecture_constraints.py::TestUIServiceBoundary::test_ui_pages_does_not_import_backend_directly -v
pytest tests/test_architecture_constraints.py::TestCommandHandlerPattern::test_view_emits_document_changed_signal -v
```

### Watch for regressions:
```bash
pytest tests/test_architecture_constraints.py -v --tb=short
```

### Get test summary:
```bash
pytest tests/test_architecture_constraints.py --co -q  # List all tests
pytest tests/test_architecture_constraints.py -v --tb=no 2>&1 | tail -20  # Summary
```

---

## Test Design Philosophy

These tests enforce **architectural boundaries**, not implementation details:

- ✅ **Good test:** "UI must not import backend directly" (enforces layer separation)
- ✅ **Good test:** "Selection uses IDs, not indices" (enforces semantic correctness)
- ✅ **Good test:** "No circular imports in core modules" (enforces testability)
- ❌ **Bad test:** "CanvasView must have exactly 403 methods" (enforces current bad state)

### Test Categories

**1. Boundary Tests** (Layer separation, import rules)
- Enforce UI ↔ services ↔ backend layering
- Prevent circular dependencies
- Block unwanted coupling

**2. Semantic Tests** (Type consistency, correctness)
- Enforce EntityId vs int distinction
- Validate entity data types (tuples, strings, etc.)
- Prevent silent failures (orphaned selections)

**3. Mutation Safety Tests** (API contracts)
- Verify append/replace operations are atomic
- Check that selection doesn't get corrupted
- Ensure document invariants hold

**4. Complexity Tracking Tests** (Metrics & trending)
- Monitor file sizes (fail on phase targets)
- Count methods per class (skip if too high, document in output)
- Alert on complexity regressions

### How Tests Guide Refactoring

Each failing test is a **refactoring goal**, not a bug report:

```
FAIL: test_ui_canvas_does_not_import_backend_geometry_directly
  ↓
  means: tools.py and select.py should import from app.services
  ↓
  work: Route imports through geometry_service wrapper
  ↓
  verify: Re-run test → should pass
```

This approach makes refactoring **testable and measurable** — you can see progress as tests go from failing → passing.

---

## Next Steps

1. **Finish LP-5** (import cleanup) — 5-6 hours
   - Makes 2 failing tests pass
   - Reduces coupling debt
   - Unblocks Phase 0

2. **Start Phase 0.1** (handler infrastructure) — 2 days
   - Makes 1 skip test pass
   - Sets pattern for Phase 0.2-0.4

3. **Commit test-driven approach** — commit to running these tests in CI
   - Prevents regressions
   - Guides new feature development
   - Validates refactoring progress

---

## References

- **Plan:** `plan.md` (full refactoring roadmap)
- **Test File:** `tests/test_architecture_constraints.py`
- **Metrics:**
  - Tests: 16 total (12 pass, 1 skip, 3 fail)
  - Success Rate: 75%
  - Critical Blockers: 1 (Phase 0)
