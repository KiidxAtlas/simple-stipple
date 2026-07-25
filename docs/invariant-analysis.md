# Invariant Analysis: simple-stipple

Generated: 2026-07-21

## 1. CommandStack (Undo/Redo) — `src/backend/model/editor_history.py`

| Invariant | Location | Risk |
|-----------|----------|------|
| `len(undo) + len(redo) == total_commands_recorded_since_last_clear` | `take_undo`, `take_redo` | Low |
| `redo_commands` is always empty after `record()` | `record()` | Already enforced |
| Popped pairs are always valid `(Command, Command)` tuples | `take_undo`, `take_redo` | Low |

**Potential bug**: No invariant check that undo/redo pairs are actual inverses. If a caller records mismatched `(command, inverse)`, undo/redo silently produces wrong state.

---

## 2. Document (Runtime Entity Store) — `src/backend/model/document.py`

| Invariant | Description | Risk |
|-----------|-------------|------|
| `all(entity.id is unique)` | Entity IDs must be unique | **Medium** — only called explicitly by `replace()`, not after every mutation |
| `selection ⊆ {0..len(entities)-1}` | Selection indices must be valid | **High** — `selected_ids()` guards with `0 <= index < len(entities)`, but `selection` can accumulate stale indices silently |
| `entity.layer ∈ layer_order ∪ {None}` | Entity layers must exist | **High** — no enforcement when entities are added or layers changed |
| `entity.group is None or count(entity.group) >= 2` | Groups need 2+ members | **Medium** — `reconcile_groups()` exists but is only called explicitly |
| `group_labels keys ⊆ existing groups` | Labels match groups | **Medium** — same as above |
| `next_group_id > max(existing groups, default=0)` | Group ID monotonicity | Low |

**Bug**: `replace()` calls `ensure_unique_ids()` but `append()` does not. If two `EntityRecord`s are created with the same explicit `id`, `append()` silently keeps the duplicate.

---

## 3. EntityRecord — `src/backend/model/document.py`

| Invariant | Description | Risk |
|-----------|-------------|------|
| `len(points) >= 2` for polylines | A polyline needs at least 2 points | **High** — no enforcement; 1-point "polylines" can exist |
| `points[i] is tuple[float, float]` | Each point is exactly 2 floats | **Medium** — `_rehydrate_meta()` in `view.py` handles meta, but not `EntityRecord.points` directly |
| If `kind == "circle"` → `len(points) == 1` and `meta` has `radius` | Shape-specific point count | **Medium** — no enforcement |

---

## 4. SelectionService — `src/ui/canvas/interaction/select.py`

| Invariant | Description | Risk |
|-----------|-------------|------|
| Selection indices always valid for current entity list | `selection` ↔ `document.entities` alignment | **High** — when entities are deleted/inserted, selection indices become stale |
| `selection` is a `set[int]`, not `set[str]` | Uses indices, not IDs | **Medium** — fragile under entity reorder/delete |

---

## 5. LayerService — `src/ui/canvas/services/layer_service.py`

| Invariant | Description | Risk |
|-----------|-------------|------|
| `active_layer ∈ layer_order ∪ {None}` | Active layer must exist | **High** — deleting active layer doesn't auto-select another |
| `layer_colors keys ⊆ layer_order` | Color map matches layers | **Medium** — stale colors accumulate |
| Each entity's `layer ∈ layer_order ∪ {None}` | Entity layers valid | **High** — no enforcement on entity layer assignment |

---

## 6. GroupingService — `src/ui/canvas/services/grouping.py`

| Invariant | Description | Risk |
|-----------|-------------|------|
| `group_map() values ⊆ {None} ∪ positive ints` | Group IDs are positive | Low |
| `group_of(index) is consistent with group_map()` | Per-entity group matches global map | **Medium** — no cross-check |

---

## 7. Command Reversibility — `src/backend/model/commands.py`

| Invariant | Description | Location | Risk |
|-----------|-------------|----------|------|
| `command.reverse().reverse() == command` | Double-reverse identity | `TransformCommand.reverse()` | **High** — NOT enforced. Floating-point drift accumulates over many undo/redo cycles |
| `EntityChangeCommand.before` ↔ `after` are consistent with `entity_ids` | Snapshots match entity IDs | `EntityChangeCommand._from_payload()` | **Medium** — IDs in `entity_ids` don't have to match IDs in snapshots |
| `TransformCommand` scale ≠ 0 | Zero scale is not reversible | `TransformCommand.__post_init__()` | Already enforced |
| `TransformCommand` non-uniform scale routed through snapshot | Anisotropic scale needs `DocumentSnapshot` | `TransformCommand.__post_init__()` | Already enforced |

---

## 8. Bézier Handle Editing — `src/ui/canvas/interaction/select.py`

### Bug: Mutation outside command system (HIGH)
- Location: `_set_bezier_handle()` (~line 246-292)
- Directly mutates `entity.meta` and calls `_sync_shape_storage_from_entities()` without going through `begin_preview()`/`commit_preview()` or `UpdateEntitiesCommand`
- **Bézier handle edits are not undoable**

### Bug: Preview transaction leak (MEDIUM)
- Location: `set_bezier_node_type()` (~line 294-321)
- Mutates `entity.meta["node_types"]` **before** `begin_preview()`
- If exception occurs between `begin_preview()` and `commit_preview()`, the mutation is lost silently
- Same pattern in `close_selection_as_path` where `merge_selected_segments_to_objects` and `_close_selected_polylines` modify entities before `begin_preview()`

---

## 9. Persistence — `src/backend/persistence.py`

| Invariant | Description | Risk |
|-----------|-------------|------|
| Saved JSON is valid UTF-8 | `write_json_file_atomic()` | Low |
| Atomic write: either old or new file exists, never corrupt | `os.replace()` | Already enforced |
| File size ≤ `MAX_WORKSPACE_FILE_BYTES` | `read_json_file()` | **Medium** — enforced on read but NOT on write |

---

## 10. CanvasView — `src/ui/canvas/view.py`

| Invariant | Description | Risk |
|-----------|-------------|------|
| `MIN_SCALE ≤ scale ≤ MAX_SCALE` | `0.00005 ≤ scale ≤ 20000` | **Medium** — enforced on zoom but not on programmatic set |
| `_sel` (selection indices) are valid for current entity count | Property setter guards with `0 <= index < len(entities)` | Already guarded |

---

## Summary: Highest Priority Invariants to Add

1. **Entity ID uniqueness** — enforce in `Document.append()` and after every mutation
2. **Layer consistency** — enforce `entity.layer ∈ layer_order` on entity creation/deletion
3. **Group reconciliation** — call `reconcile_groups()` automatically after entity add/delete
4. **Selection staleness** — invalidate selection indices when entities are removed
5. **Active layer on delete** — auto-select another layer when active layer is deleted
6. **Command reversibility** — add test that `cmd.reverse().reverse() == cmd` for all command types
7. **Bézier editing** — route through command system for undo support
