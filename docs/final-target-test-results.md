# Final Architecture Target Test Results

**Date:** 2026-07-22  
**Test Suite:** `tests/test_final_architecture_target.py`  
**Status:** 13 ✅ PASS | 32 ❌ FAIL | 0 ⏭️ SKIP  
**Current Pass Rate:** 29% (Target: 100%)

This test suite defines the **FINAL architecture state** after plan.md Phases 0-3 are complete. **All failures listed below are refactoring goals, not bugs.**

---

## Test Results Summary

### ✅ Already Passing (13 tests = 29%)

These architectural aspects are already correct:

**Phase 1 Complete (3 tests)**
- ✅ Selection uses entity IDs, not indices (LP-1)
- ✅ Geometry constants centralized (LP-4)
- ✅ Selection ID migration is complete (LP-1)

**Phase 3 Complete (1 test)**
- ✅ Zero circular dependencies detected (Phase 3.3)
- ✅ Command reversibility works (Phase 3 testing)

**Type Safety (5 tests)**
- ✅ All entity IDs are strings
- ✅ No int indices in selection
- ✅ Coordinate tuples are floats
- ✅ Layer type is string | None
- ✅ Group type is int | None

**API Contracts (4 tests)**
- ✅ Document.append() validates
- ✅ Document.replace() clears selection
- ✅ Selection operations are atomic
- ✅ All tests still pass (no regressions)

---

## ❌ Failing Tests by Phase (32 failures)

### PHASE 0: Command Handler Pattern (14 failures)

**Critical Blockers — Must complete before Phases 1-3**

| Test | Current State | Target | Effort |
|------|---------------|--------|--------|
| CommandHandler base class | ❌ Does not exist | Must exist | 1 day |
| OffsetHandler | ❌ Missing | Must exist | 0.5 days |
| MirrorHandler | ❌ Missing | Must exist | 0.5 days |
| RotateHandler | ❌ Missing | Must exist | 0.5 days |
| ScaleHandler | ❌ Missing | Must exist | 0.5 days |
| BooleanHandler | ❌ Missing | Must exist | 0.5 days |
| DimensionHandler | ❌ Missing | Must exist | 0.5 days |
| TextHandler | ❌ Missing | Must exist | 0.5 days |
| LayerHandler | ❌ Missing | Must exist | 0.5 days |
| CanvasView.document_changed signal | ❌ Missing | Must be Signal | 0.5 days |
| CanvasView.operation_failed signal | ❌ Missing | Must be Signal | 0.5 days |
| view.py direct mutations | ❌ Present (self._document.append/replace) | Must be removed | 5 days |
| view.py line count | ❌ 3731 lines | Target: <800 | 13 days |
| CanvasView method count | ❌ 167 public methods | Target: <50 | 13 days |

**Summary:** Phase 0 is NOT started. Need to create handler infrastructure and refactor view.py. This is the critical path — nothing else can proceed until this is done.

---

### PHASE 1: Quick Wins (1 failure)

**Mostly complete, 1 remaining issue**

| Test | Current State | Target | Effort |
|------|---------------|--------|--------|
| Document validation is automatic | ❌ Incomplete (line needs 2 points) | Fully validate | 1 day |

---

### PHASE 2: Architecture Simplification (4 failures)

**UI layer still imports backend directly — must route through app.services**

| Test | Current State | Issue | Effort |
|------|---------------|-------|--------|
| pattern/tab.py decomposed | ❌ 4051 lines | Target: <1500 | 5 days |
| UI imports only from app.services | ❌ 30+ backend imports | Route via services | 3 days |
| app.services wrapper layer | ❌ Missing exports | Add to geometry_service | 1 day |
| controllers avoid UI widgets | ❌ menu.py imports QWidgets | Remove widget imports | 1 day |

**Details of UI→backend imports (30+ violations):**
```
src/ui/canvas/view.py: src.backend.cad.constraints (5 imports)
src/ui/canvas/view.py: src.backend.cad.editor_geometry
src/ui/canvas/view.py: src.backend.cad.shapes
src/ui/canvas/view.py: src.backend.cad.snapping
src/ui/canvas/view.py: src.backend.cad.coordinates
src/ui/widgets/canvas/properties_panel.py: src.backend.cad.constraints
src/ui/pages/trace/tab.py: src.backend.cad.preflight
src/ui/pages/pattern/tab.py: src.backend.pattern.output
src/ui/canvas/rendering/renderer.py: src.backend.cad.preflight (2)
src/ui/canvas/rendering/renderer.py: src.backend.cad.constraints
src/ui/canvas/interaction/tools.py: src.backend.cad.geometry
src/ui/canvas/interaction/select.py: src.backend.cad.geometry (3)
src/ui/canvas/interaction/select.py: src.backend.cad.shapes
src/ui/canvas/services/editing.py: src.backend.cad.preflight (7 imports)
src/ui/canvas/services/editing.py: src.backend.cad.recognition
src/ui/canvas/services/editing.py: src.backend.cad.path_ops
src/ui/canvas/services/editing.py: src.backend.cad.primitives
src/ui/canvas/services/draw_ops.py: src.backend.cad.construction (5 imports)
```

---

### PHASE 3: Polish (10 failures)

**Final touches and optimizations**

| Test | Current State | Target | Effort |
|------|---------------|--------|--------|
| handlers directory exists | ❌ Missing | src/ui/canvas/handlers/* | Phase 0 |
| pattern page decomposed | ❌ Not split | pattern_ui.py, pattern_controller.py, etc. | 5 days |
| DXF parser unified | ❌ Scattered | Single DXFParser class | 2 days |
| BaseDialog pattern | ❌ Not created | BaseDialog template | 1 day |
| core.launcher upward import | ❌ Imports app.window | Must be removed | 0.5 days |
| settings routed via app.config | ❌ Direct imports | Use app.config wrapper | 1 day |
| handler.execute signature | ❌ Not defined | Must match interface | Phase 0 |
| all imports traced | ❌ offset_polyline missing | Export from geometry_service | 0.5 days |
| no dead code in view | ❌ Old methods present | Remove operation methods | Phase 0 |
| view.py final size | ❌ 3731 lines | <800 lines | 13 days |

---

## Work Breakdown by Effort

### Immediate (This Week) — 5-6 hours
- Create geometry_service exports (offset_polyline, etc.)
- Route pattern import through services
- Remove controller widget imports

### Short Term (Week 2) — 3-5 days
- Create app.services wrapper layer (complete)
- Route UI→backend imports through services
- Start Phase 0.1: CommandHandler infrastructure

### Critical Path (2-4 weeks) — Phase 0 (13 days)
- Create handlers directory and base class
- Extract offset/mirror/rotate/scale/boolean handlers
- Extract dimension/text/layer handlers
- Remove direct mutations from view.py
- Add Signal infrastructure
- Reduce view.py from 3731 to <800 lines
- Reduce CanvasView methods from 167 to <50

### After Phase 0 (1-2 weeks) — Phases 1-2
- Decompose pattern/tab.py (1500→<1500 lines)
- Break circular dependencies
- Refactor document validation
- Migrate remaining imports

### Polish (1 week) — Phase 3
- Unified DXF parser
- BaseDialog template
- Settings routing through app.config
- Remove upward imports

---

## Test Execution

### Run all final target tests:
```bash
pytest tests/test_final_architecture_target.py -v
```

### Run by phase:
```bash
pytest tests/test_final_architecture_target.py::TestPhase0CommandHandlers -v
pytest tests/test_final_architecture_target.py::TestPhase1QuickWins -v
pytest tests/test_final_architecture_target.py::TestPhase2Architecture -v
pytest tests/test_final_architecture_target.py::TestPhase3Polish -v
```

### See detailed failures:
```bash
pytest tests/test_final_architecture_target.py -v --tb=short 2>&1 | head -200
```

### Track progress (how many tests pass):
```bash
pytest tests/test_final_architecture_target.py --tb=no -q
```

---

## Key Metrics

| Metric | Current | Target | Gap |
|--------|---------|--------|-----|
| **Test Pass Rate** | 29% (13/45) | 100% (45/45) | 32 tests |
| **view.py lines** | 3731 | <800 | -2931 lines |
| **CanvasView methods** | 167 | <50 | -117 methods |
| **pattern/tab.py lines** | 4051 | <1500 | -2551 lines |
| **Backend imports in UI** | 30+ | 0 | -30+ imports |
| **Handler modules** | 0 | 8 | -8 modules |
| **Circular dependencies** | 0 | 0 | ✅ OK |
| **Type consistency** | ✅ OK | ✅ OK | ✅ OK |

---

## How to Use These Tests

### 1. Track Progress
Each failing test is a **refactoring goal**. When a test passes, that work is done.

```
Start:  13/45 passing (29%)
Goal:   45/45 passing (100%)
```

### 2. Prioritize Work
Work in this order (dependencies):
1. **Phase 0** (BLOCKER) — Without this, nothing else is possible
2. **Phase 1** — Once Phase 0 is done, these are trivial
3. **Phase 2** — Depends on Phase 0
4. **Phase 3** — Final polish after Phases 0-2

### 3. Avoid Regressions
Every time you fix a test, run the suite:
```bash
pytest tests/test_final_architecture_target.py --tb=short -q
```

If pass rate goes down, you've introduced a regression. Fix it before continuing.

### 4. Verify Completeness
When all 45 tests pass, the refactoring is DONE and architecture is correct.

---

## Notes

- ✅ **Passing tests validate:** Type safety, API contracts, basic invariants, circular dependency check
- ❌ **Failing tests are:** Refactoring work items, not bugs or architectural problems
- 🔴 **Critical blockers:** Phase 0 (handler infrastructure) — needed before anything else
- 📊 **Lowest-hanging fruit:** Phase 1 (mostly done), Phase 3 (polish, but not critical path)
- ⏰ **Estimated total effort:** ~4-6 weeks (with Phase 0 being the constraint)

---

## Companion Test Suites

- `tests/test_architecture_constraints.py` — Progress tracking (current state)
- `tests/test_final_architecture_target.py` — Final target state (THIS FILE)

Run both to see:
- **Constraints suite:** What's already good
- **Target suite:** What still needs work
