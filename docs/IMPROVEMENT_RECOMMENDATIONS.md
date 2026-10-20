# Simple Stipple — Overall Improvement Recommendations

## Executive Summary

Simple Stipple is a **well-architected** PySide6 desktop application with strong code quality (ruff lint: 0 errors, mypy: 0 issues, 0 circular imports across 167 modules, 587 tests). The architecture documentation is exceptional. The main improvement opportunities fall into **four categories**: (1) reducing god classes, (2) fixing performance bottlenecks, (3) improving test infrastructure, and (4) closing documentation gaps.

---

## 1. Architecture & Code Structure (Highest Impact)

### 1.1 Break Up God Classes

Several files far exceed reasonable size limits and mix UI, business logic, and data management:

| File                           | Lines | Problem                                        |
| ------------------------------ | ----- | ---------------------------------------------- |
| `features/pattern/page.py`     | 2,744 | UI + state + worker coordination + canvas sync |
| `canvas/operations/editing.py` | 3,440 | Vertex drag, snapping, boolean ops, selection  |
| `canvas/view/main.py`          | 2,099 | CanvasView with 80+`Any`-typed attributes      |
| `features/pattern/form.py`     | 1,019 | Form controls + state management               |
| `features/trace/page.py`       | 1,924 | Trace page UI + image placement + session      |
| `canvas/snap.py`               | 1,584 | All snap algorithms in one file                |
| `core/patterns/processing.py`  | 1,387 | Pattern generation pipeline                    |
| `canvas/renderer.py`           | 2,433 | Full paint pipeline with no caching            |

**Recommendation**: Extract each into focused modules. For example:

- `PatternPage` → `PatternPageUI` (signals/slots), `PatternState` (data model), `PatternWorker` (thread coordination)
- `editing.py` → `editing/vertex_ops.py`, `editing/boolean_ops.py`, `editing/selection.py`
- `renderer.py` → `renderer/paint_polys.py`, `renderer/paint_guides.py`, `renderer/theme.py`

### 1.2 Eliminate Parallel State Duplication

`PatternPage` maintains `_edit_polys`, `_outline_ids`, `_outline_layers`, `_orig_polys`, `_preview_polys_cache`, `_preview_categories`, `_treatments`, `_zones` — all mirroring canvas state. The canvas also holds its own `_entities_by_id`. This creates drift risk.

**Recommendation**: Establish a single source of truth. Either:

- The canvas is the authoritative state and pages subscribe to changes via signals
- Or a shared `DocumentState` model holds all geometry and pages render from it

### 1.3 Fix Type Erosion

`CanvasView` declares 80+ attributes as `Any` or `list[Any]` (lines 304-398), undermining the mypy success.

**Recommendation**: Define proper typed classes for canvas sub-components (`CanvasToolbar`, `CanvasSidebar`, `CanvasPropertiesPanel`) instead of `Any`.

---

## 2. Performance (High Impact)

### 2.1 No Spatial Indexing for Snap/Hit Testing

`snap.py` (1,584 lines) performs snap calculations on every mouse move without R-tree or grid-based acceleration. For 1,000+ entities this is O(n) per frame.

**Recommendation**: Add a spatial index (e.g., `rtree` or a grid-based hash) for snap and hit testing. The `hit_testing.py` module already exists — extend it with spatial partitioning.

### 2.2 Unbounded Canvas Rendering

`_paint_main_polys` iterates ALL entities every frame (line 327: `for ent in self._host._entities`) — only bounding-box culling, no spatial partitioning.

**Recommendation**: Use a spatial index for the render loop. The `DensePreviewRenderer` (rendering.py) already caches rasterized paths — extend this pattern to the main renderer.

### 2.3 Redundant Object Creation in Paint Path

`_theme_tokens()` imports `resolve_tokens` from `ui.style` on every paint call. New `QPen`/`QColor` objects are created every frame.

**Recommendation**: Cache theme tokens per theme change. Cache `QPen` objects by style/color.

### 2.4 Synchronous Shapely Operations

`editing.py` calls `shapely` boolean operations synchronously on the UI thread for complex geometries.

**Recommendation**: Offload boolean operations to worker threads with cancellation support (similar to the existing `CancellableTaskState` pattern).

---

## 3. Test Infrastructure (Medium-High Impact)

### 3.1 No Coverage Measurement

No `pytest-cov` or coverage reporting configured. Can't measure what's actually covered.

**Recommendation**: Add `pytest-cov` to dev dependencies and configure coverage thresholds in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
addopts = "--cov=simple_stipple --cov-fail-under=80"
```

### 3.2 No Property-Based Testing

Hypothesis is in dev dependencies but no `@given` tests exist — a missed opportunity for geometry/pattern edge cases.

**Recommendation**: Add property-based tests for:

- Pattern generation (valid/invalid spacing, density, rotation combos)
- Boolean operations (degenerate geometries, overlapping edges)
- Snap calculations (edge cases with collinear/parallel entities)

### 3.3 Test Execution Time

Full suite times out at 120s. Pattern tests alone take 16s (156 tests). Many tests spawn Qt QApplication instances.

**Recommendation**:

- Separate fast tests (no Qt) from slow tests (Qt event loop)
- Use `pytest-xdist` for parallel test execution
- Add `@pytest.mark.slow` markers and run slow tests only in CI

### 3.4 One Failing Test

`test_reopening_our_own_export_brings_the_image_back` in `test_svg_round_trip.py` — suggests a regression in SVG image round-trip export.

**Recommendation**: Fix this regression and add a regression test fixture.

---

## 4. Documentation & Onboarding (Medium Impact)

### 4.1 No API Documentation

No docstrings on public surfaces (`DocumentService`, `DxfCanvas`, `default_page_specs`). No `sphinx` or `pdoc` configured.

**Recommendation**: Add docstrings to all public APIs and configure `pdoc` for auto-generated docs.

### 4.2 No "Hello World" Contribution Path

New contributors must read ARCHITECTURE.md and trace 5 feature packages to contribute meaningfully. No small, well-scoped "good first issue" exists.

**Recommendation**: Create a `docs/CONTRIBUTING_WALKTHROUGH.md` that walks through:

1. Running the app locally
2. Making a trivial change (e.g., updating a label)
3. Running the checks
4. Submitting a PR

### 4.3 No Architecture Decision Records (ADRs)

The architecture doc describes current state but doesn't explain _why_ certain decisions were made.

**Recommendation**: Add ADRs in `docs/adr/` for key decisions:

- Why `features/` over `pages/`
- Why `core/cad/` vs `core/geometry/`
- Why entry_points for pattern generators
- Why `CancellableTaskState` pattern

### 4.4 Complex Algorithms Lack Inline Documentation

`snap.py` (1,584 lines), `editing.py` (3,440 lines), `processing.py` (1,387 lines) have minimal inline documentation for the algorithms they implement.

**Recommendation**: Add docstrings explaining the algorithm, not just what the function does. For example, the snap calculation algorithm in `snap.py` should document the spatial strategy and complexity.

---

## 5. Code Quality (Low Impact — Already Strong)

### 5.1 Formatting (6 files)

Only 6 files need ruff formatting. Trivial fix.

### 5.2 Magic Numbers

`MAX_ESTIMATED_ELEMENTS`, `_CLIP_BATCH = 1_000`, `TRACE_DEBOUNCE_MS = 220`, `MIDPOINT_BIAS = 4.0` scattered without configuration.

**Recommendation**: Extract to a `config.py` module or `pyproject.toml` settings.

### 5.3 Thread Safety

`PatternPage` uses `_preview_thread` and `_generate_thread` but shared state like `_edit_polys` is accessed from both UI and worker threads without explicit synchronization.

**Recommendation**: Add explicit thread-safe access patterns (queues, locks, or signals/slots).

---

## Priority Summary

| Priority | Area                                               | Effort | Impact                                     |
| -------- | -------------------------------------------------- | ------ | ------------------------------------------ |
| **P0**   | Break up god classes (editing.py, pattern/page.py) | High   | Very High — enables all other improvements |
| **P0**   | Fix failing SVG round-trip test                    | Low    | High — regression in export                |
| **P1**   | Add spatial indexing for snap/hit testing          | Medium | High — UI responsiveness                   |
| **P1**   | Add coverage measurement                           | Low    | Medium — visibility into test quality      |
| **P1**   | Add property-based tests for geometry              | Medium | High — catches edge cases                  |
| **P2**   | Fix CanvasView type erosion                        | Medium | Medium — unlocks mypy strict mode          |
| **P2**   | Add API docstrings                                 | Low    | Medium — onboarding                        |
| **P2**   | Create contributor walkthrough                     | Low    | Medium — onboarding                        |
| **P3**   | Cache theme tokens in renderer                     | Low    | Medium — FPS improvement                   |
| **P3**   | Extract magic numbers to config                    | Low    | Low — maintainability                      |
| **P3**   | Add ADRs                                           | Low    | Medium — architectural knowledge           |

The single highest-leverage improvement is **breaking up the god classes** — particularly `editing.py` (3,440 lines) and `pattern/page.py` (2,744 lines). This would naturally reduce coupling, improve testability, and make the other improvements (thread safety, caching, spatial indexing) much easier to implement.
