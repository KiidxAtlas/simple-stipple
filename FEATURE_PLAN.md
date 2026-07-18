# Feature Plan — Simple Stipple

> **Scope:** Features prioritized for the next 1–2 release cycles. Ordered by effort, impact, and dependencies on the architectural refactor.

---

## Feature 1: Live Parameter Sliders for Patterns

**Priority:** High | **Effort:** Medium | **Depends on:** Refactor (Numba + Clipper2)

### Problem

Pattern generation is a "set parameters → wait → see result" workflow. Users change spacing, scale, or angle and must wait for the full pattern to regenerate before seeing the effect.

### Solution

Replace static parameter inputs with live sliders that update the pattern preview in real-time as the user drags. Debounce updates to avoid regenerating on every pixel of slider movement.

### User Flow

1. User opens Pattern page
2. Sees sliders for spacing, scale, angle, density (instead of or alongside number inputs)
3. Drags a slider → pattern preview updates on canvas within 50ms
4. Clicks "Apply" or "Export" when satisfied

### Technical Details

- Wire Qt `QSlider` / `QDoubleSlider` to pattern generation pipeline
- Debounce slider changes (100ms) to avoid thrashing
- Use Numba JIT-compiled pattern generation for sub-50ms response
- Show a subtle "generating…" indicator during regeneration
- Keep existing number inputs as fallback for precise values

### Files to Create/Modify

| File                            | Action                                                     |
| ------------------------------- | ---------------------------------------------------------- |
| `ui/widgets/pattern_sliders.py` | **Create** — Slider widgets for pattern parameters         |
| `ui/pages/pattern/tab.py`       | **Modify** — Replace/add sliders alongside existing inputs |
| `ui/pages/pattern/workers.py`   | **Modify** — Add debounce logic, cancel stale generations  |
| `backend/pattern/organic.py`    | **Modify** — JIT-compile hot loops (via `backend/jit.py`)  |
| `backend/pattern/tiling.py`     | **Modify** — JIT-compile hot loops (via `backend/jit.py`)  |

### Acceptance Criteria

- [ ] Sliders update pattern preview within 50ms for standard patterns
- [ ] No UI freeze while pattern regenerates
- [ ] Stale pattern generations are cancelled when user keeps dragging
- [ ] Number inputs still work alongside sliders
- [ ] Works with all pattern types (honeycomb, stipple, Voronoi, etc.)

---

## Feature 2: Undo for All Editing Operations

**Priority:** High | **Effort:** Small | **Depends on:** Refactor (Command architecture)

### Problem

Currently, undo/redo is delta-based and limited. Operations like trim, extend, boolean ops, offset, resample, merge, and explode either don't support undo or have fragile undo that breaks on complex geometry.

### Solution

With the command architecture, every document-changing operation becomes a reversible command. Define the command type and undo is free.

### User Flow

1. User performs any editing operation (trim, extend, boolean, offset, etc.)
2. Presses Ctrl+Z → operation is undone cleanly
3. Presses Ctrl+Shift+Z → operation is redone
4. Undo history is consistent across all operations

### Technical Details

- Define command types for each operation: `TrimCommand`, `ExtendCommand`, `BooleanOpCommand`, `OffsetCommand`, `ResampleCommand`, `MergeCommand`, `ExplodeCommand`
- Each command implements `.reverse(document) → Document`
- `DocumentService` manages the command stack
- Undo/redo is already wired to Ctrl+Z/Ctrl+Shift+Z in the menu

### Files to Create/Modify

| File                                | Action                                                 |
| ----------------------------------- | ------------------------------------------------------ |
| `backend/commands.py`               | **Modify** — Add new command types                     |
| `backend/editing/trim_extend.py`    | **Modify** — Return command data, not direct mutations |
| `backend/editing/boolean.py`        | **Modify** — Return command data                       |
| `backend/editing/offset.py`         | **Modify** — Return command data                       |
| `backend/editing/resample.py`       | **Modify** — Return command data                       |
| `backend/editing/merge_explode.py`  | **Modify** — Return command data                       |
| `app/services/document_service.py`  | **Create** — Command stack management                  |
| `ui/canvas/interaction/commands.py` | **Modify** — Wire commands to menu/keyboard            |

### Acceptance Criteria

- [ ] Ctrl+Z undoes every editing operation (trim, extend, boolean, offset, resample, merge, explode)
- [ ] Ctrl+Shift+Z redoes every undone operation
- [ ] Undo history is consistent — no "stuck" states
- [ ] Undo works across page switches (draft → pattern → draft)
- [ ] Undo count is unlimited (bounded only by memory)

---

## Feature 3: Smart Shape Recognition (Auto-Convert to Parametric)

**Priority:** High | **Effort:** Small | **Depends on:** None (recognition already exists)

### Problem

When users import a DXF with circles, rectangles, or arcs, they get raw polylines. They have to manually recreate parametric shapes if they want to edit radius, width, or height later.

### Solution

After import, automatically detect parametric shapes and offer a one-click "Convert to Parametric" option. The shape becomes editable (radius handle, width/height inputs).

### User Flow

1. User imports a DXF with circles and rectangles
2. App detects shapes and shows a toast: "Found 3 parametric shapes. Convert?"
3. User clicks "Convert" → shapes become parametric (editable circles, rectangles)
4. User can edit radius/width/height directly or via properties panel

### Technical Details

- Use existing `recognition.py` (`recognize_polyline`) to detect shapes
- After import, run recognition on all closed polylines
- Show a non-blocking toast/notification with count of detected shapes
- Convert recognized polylines to parametric entities (circle with radius, rectangle with width/height)
- Store recognition metadata in entity `meta` dict

### Files to Create/Modify

| File                                     | Action                                                        |
| ---------------------------------------- | ------------------------------------------------------------- |
| `ui/widgets/shape_recognition_dialog.py` | **Create** — Dialog showing detected shapes + convert button  |
| `ui/pages/draft.py`                      | **Modify** — Call recognition after import, show dialog       |
| `backend/recognition.py`                 | **Modify** — Add `convert_to_parametric()` function           |
| `backend/editor.py`                      | **Modify** — Add parametric entity types (circle, rectangle)  |
| `ui/widgets/properties_panel.py`         | **Modify** — Show parametric controls (radius, width, height) |

### Acceptance Criteria

- [ ] Circles detected and converted to parametric circles (editable radius)
- [ ] Rectangles detected and converted to parametric rectangles (editable width/height)
- [ ] Regular polygons detected and converted (editable side count, radius)
- [ ] Detection runs automatically after import
- [ ] User can dismiss the detection notification without converting
- [ ] Converted shapes remain editable (handles, properties panel)

---

## Feature 4: Dimension Annotations

**Priority:** High | **Effort:** Medium | **Depends on:** None

### Problem

Laser cutting users need to verify dimensions of their designs. Currently they have to calculate distances or angles mentally, or use a separate measurement tool.

### Solution

Add dimension lines with measurements directly on the canvas. Click a line to see its length. Click a circle to see its diameter. Click three points to see the angle.

### User Flow

1. User selects the "Dimension" tool from the toolbar
2. Clicks two points on the canvas → dimension line appears with distance
3. Clicks a circle → dimension line appears with diameter
4. Clicks three points → dimension line appears with angle
5. Dimensions are editable (move, delete, change precision)
6. Dimensions export with the design (in DXF as dimension entities)

### Technical Details

- New tool mode in `ui/canvas/interaction/tools.py` — `DimensionTool`
- Dimension entities stored in `EntityRecord` with `kind="dimension"`
- Dimension rendering in `ui/canvas/mixins/render.py` — draw dimension lines with text
- Dimension calculation in `backend/geometry.py` — distance, angle, diameter functions
- Dimension text uses canvas coordinate system (scales with zoom)
- Dimensions are visual-only (don't affect geometry)

### Files to Create/Modify

| File                             | Action                                                           |
| -------------------------------- | ---------------------------------------------------------------- |
| `ui/canvas/interaction/tools.py` | **Modify** — Add `DimensionTool`                                 |
| `backend/editor.py`              | **Modify** — Add `kind="dimension"` entity type                  |
| `ui/canvas/mixins/render.py`     | **Modify** — Render dimension lines + text                       |
| `ui/canvas/mixins/draw_ops.py`   | **Modify** — Dimension placement logic                           |
| `backend/geometry.py`            | **Modify** — Add `distance()`, `angle()`, `diameter()` functions |
| `ui/widgets/properties_panel.py` | **Modify** — Show dimension properties (value, precision, color) |
| `backend/dxf/io.py`              | **Modify** — Export dimensions to DXF DIMENSION entities         |

### Acceptance Criteria

- [ ] Click two points → dimension line with distance (e.g., "12.50 mm")
- [ ] Click a circle → dimension line with diameter (e.g., "Ø 25.00 mm")
- [ ] Click three points → dimension line with angle (e.g., "45.0°")
- [ ] Dimensions scale with zoom (text stays readable)
- [ ] Dimensions can be moved, deleted, edited
- [ ] Dimensions export to DXF
- [ ] Precision configurable (0.01 mm default)

## Feature 14: Measurement Tool

**Priority:** High | **Effort:** Small | **Depends on:** None

### Problem

Users need to verify dimensions of their designs without doing mental math. Currently there's no built-in way to measure distance or angle on the canvas.

### Solution

Add a measurement tool that lets users click two points to measure distance, or three points to measure angle. Measurements appear as floating labels on the canvas.

### User Flow

1. User selects the "Measure" tool from the toolbar
2. Clicks point A, then point B → distance appears as a label (e.g., "25.40 mm")
3. Clicks point A, point B, point C → angle appears as a label (e.g., "90.0°")
4. Measurements stay on canvas until deleted
5. Measurements can be deleted individually or all at once

### Technical Details

- New tool mode in `ui/canvas/interaction/tools.py` — `MeasureTool`
- Measurement entities stored in `EntityRecord` with `kind="measurement"`
- Measurement rendering in `ui/canvas/mixins/hud_text.py` — draw measurement labels
- Measurement calculation in `backend/geometry.py` — `distance()`, `angle()` functions
- Measurements are visual-only (don't affect geometry)
- Measurements snap to vertices/edges like other canvas interactions

### Files to Create/Modify

| File                             | Action                                             |
| -------------------------------- | -------------------------------------------------- |
| `ui/canvas/interaction/tools.py` | **Modify** — Add `MeasureTool`                     |
| `backend/editor.py`              | **Modify** — Add `kind="measurement"` entity type  |
| `ui/canvas/mixins/hud_text.py`   | **Modify** — Render measurement labels             |
| `backend/geometry.py`            | **Modify** — Add `distance()`, `angle()` functions |
| `ui/components.py`               | **Modify** — Add measure tool icon to toolbar      |
| `ui/pages/draft.py`              | **Modify** — Wire measure tool to toolbar          |

### Acceptance Criteria

- [ ] Click two points → distance label (e.g., "25.40 mm")
- [ ] Click three points → angle label (e.g., "90.0°")
- [ ] Measurements snap to vertices/edges
- [ ] Measurements stay on canvas until deleted
- [ ] Measurements can be deleted individually or all at once
- [ ] Measurement tool icon in toolbar
- [ ] No interference with other tools

---

## Feature 15: Copy/Paste with Offset

**Priority:** High | **Effort:** Small | **Depends on:** None

### Problem

Pattern creation often involves copying and offsetting shapes. Currently users copy, paste, then manually move the pasted shape. This is slow and imprecise.

### Solution

Enhance copy/paste to support offset pasting. Hold a key (Shift) while pasting to paste with an automatic offset. Hold Shift+Ctrl and click to paste multiple copies at regular intervals.

### User Flow

1. User selects shapes and presses Ctrl+C → shapes copied to clipboard
2. User presses Ctrl+V → shapes pasted at same position (existing behavior)
3. User presses Ctrl+Shift+V → shapes pasted with default offset (e.g., 5mm right)
4. User presses Ctrl+Shift+V, then clicks → shapes pasted at click position
5. User presses Ctrl+Shift+Alt+V → opens dialog for offset distance + copy count
6. User enters "5mm" and "10 copies" → 10 copies pasted at 5mm intervals

### Technical Details

- Extend existing clipboard mixin (`ui/canvas/mixins/selection_ops.py`) with offset paste
- New keyboard shortcuts: `Ctrl+Shift+V` (offset paste), `Ctrl+Shift+Alt+V` (multi-paste dialog)
- Multi-paste dialog in `ui/widgets/multi_paste_dialog.py` — offset distance, copy count, direction
- Offset calculation in `backend/editing/transform.py` — translate entities by offset vector
- Clipboard stores entity IDs, not geometry (paste creates new entities with new IDs)

### Files to Create/Modify

| File                                | Action                                                        |
| ----------------------------------- | ------------------------------------------------------------- |
| `ui/widgets/multi_paste_dialog.py`  | **Create** — Offset distance + copy count dialog              |
| `ui/canvas/mixins/selection_ops.py` | **Modify** — Add `paste_with_offset()`, `paste_multiple()`    |
| `ui/canvas/interaction/commands.py` | **Modify** — Add `Ctrl+Shift+V`, `Ctrl+Shift+Alt+V` shortcuts |
| `backend/editing/transform.py`      | **Modify** — Add `translate_entities()` function              |
| `ui/pages/draft.py`                 | **Modify** — Wire shortcuts to canvas                         |

### Acceptance Criteria

- [ ] Ctrl+V pastes at same position (existing behavior preserved)
- [ ] Ctrl+Shift+V pastes with default offset (5mm right)
- [ ] Ctrl+Shift+Alt+V opens multi-paste dialog
- [ ] Multi-paste dialog accepts offset distance + copy count
- [ ] Copies are pasted at regular intervals (e.g., 5mm apart)
- [ ] Pasted entities have new IDs (don't conflict with originals)
- [ ] Works with all entity types (polylines, shapes, groups)

---

## Summary

| #   | Feature                             | Priority | Effort | Depends On                      |
| --- | ----------------------------------- | -------- | ------ | ------------------------------- |
| 1   | Live Parameter Sliders for Patterns | High     | Medium | Refactor (Numba + Clipper2)     |
| 2   | Undo for All Editing Operations     | High     | Small  | Refactor (Command architecture) |
| 3   | Smart Shape Recognition             | High     | Small  | None                            |
| 4   | Dimension Annotations               | High     | Medium | None                            |
|     |                                     |          |        |                                 |
| 14  | Measurement Tool                    | High     | Small  | None                            |
| 15  | Copy/Paste with Offset              | High     | Small  | None                            |

**Total estimated effort:** ~6–8 weeks (can be parallelized)
**Can ship before refactor:** 3, 4, 5, 14, 15
**Must ship after refactor:** 1, 2
